import asyncio
import html
import logging
import os
import random
import sqlite3
from datetime import datetime
from typing import Optional

import httpx

# Опциональная загрузка .env (если установлен python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не найден BOT_TOKEN в Secrets")

# Telegram ID администратора
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "8420676676"))
except ValueError as error:
    raise ValueError("ADMIN_ID должен быть числом") from error

DB_NAME = "collab_bot.db"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("collab_bot")


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher(
    storage=MemoryStorage()
)


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_NAME,
    check_same_thread=False
)

db.row_factory = sqlite3.Row

db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")


def db_commit():
    db.commit()


# ============================================================
# TABLES
# ============================================================

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,

    telegram_username TEXT DEFAULT '',

    roblox_username TEXT DEFAULT '',
    roblox_id INTEGER DEFAULT NULL,

    ability TEXT DEFAULT '',
    anonymous INTEGER DEFAULT 0,

    nickname TEXT DEFAULT '',
    description TEXT DEFAULT '',

    searching INTEGER DEFAULT 0,
    partner_id INTEGER DEFAULT NULL,

    likes INTEGER DEFAULT 0,
    dislikes INTEGER DEFAULT 0,

    bot_happy INTEGER DEFAULT 0,
    bot_good INTEGER DEFAULT 0,
    bot_neutral INTEGER DEFAULT 0,
    bot_bad INTEGER DEFAULT 0,

    banned INTEGER DEFAULT 0,
    ban_reason TEXT DEFAULT '',
    banned_at TEXT DEFAULT NULL
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    telegram_id INTEGER,
    feedback_type TEXT,
    text TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    file_id TEXT NOT NULL,
    added_by INTEGER,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS stickers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    file_id TEXT NOT NULL,
    added_by INTEGER,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    complainant_id INTEGER,
    accused_id INTEGER,

    reason_type TEXT,
    reason_text TEXT,

    status TEXT DEFAULT 'new',

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    sender_id INTEGER,
    partner_id INTEGER,

    message_id INTEGER,

    message_type TEXT,
    message_text TEXT DEFAULT '',

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS video_sessions (
    user_id INTEGER PRIMARY KEY,

    partner_id INTEGER,

    history TEXT DEFAULT '',
    current_index INTEGER DEFAULT 0,

    reroll_count INTEGER DEFAULT 0,

    pressed_user1 INTEGER DEFAULT 0,
    pressed_user2 INTEGER DEFAULT 0
)
""")


db_commit()


# ============================================================
# MIGRATIONS
# ============================================================

def add_column_if_missing(
    table: str,
    column: str,
    definition: str
):
    try:
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )
        db_commit()
    except sqlite3.OperationalError:
        pass


add_column_if_missing(
    "users",
    "banned",
    "INTEGER DEFAULT 0"
)

add_column_if_missing(
    "users",
    "ban_reason",
    "TEXT DEFAULT ''"
)

add_column_if_missing(
    "users",
    "banned_at",
    "TEXT DEFAULT NULL"
)


# Video stats
add_column_if_missing(
    "videos",
    "selected_count",
    "INTEGER DEFAULT 0"
)

add_column_if_missing(
    "videos",
    "own_idea_count",
    "INTEGER DEFAULT 0"
)

# Per-session choices (JSON)
add_column_if_missing(
    "video_sessions",
    "choices_json",
    "TEXT DEFAULT '{}'"
)

add_column_if_missing(
    "users",
    "seen_videos",
    "TEXT DEFAULT ''"
)


add_column_if_missing(
    "users",
    "nickname",
    "TEXT DEFAULT ''"
)

add_column_if_missing(
    "users",
    "description",
    "TEXT DEFAULT ''"
)


# ============================================================
# FSM
# ============================================================

class UserState(StatesGroup):

    waiting_for_roblox = State()
    waiting_for_nickname = State()
    waiting_for_description = State()

    waiting_for_bot_feedback = State()

    waiting_for_complaint = State()


class AdminState(StatesGroup):

    broadcasting = State()

    adding_video = State()
    deleting_video = State()

    adding_sticker = State()


class SupportState(StatesGroup):

    waiting_for_admin_message = State()


# message_id у админа -> user_id (для ответа админа)
ADMIN_REPLY_MAP: dict[int, int] = {}


# ============================================================
# BASIC DB
# ============================================================

def get_user(user_id: int):
    return db.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (user_id,)
    ).fetchone()


def create_user(
    user_id: int,
    telegram_username: Optional[str] = None
):
    user = get_user(user_id)

    if not user:
        db.execute(
            """
            INSERT INTO users (
                telegram_id,
                telegram_username
            )
            VALUES (?, ?)
            """,
            (
                user_id,
                telegram_username or ""
            )
        )
        db_commit()

    elif telegram_username is not None:
        db.execute(
            """
            UPDATE users
            SET telegram_username = ?
            WHERE telegram_id = ?
            """,
            (
                telegram_username,
                user_id
            )
        )
        db_commit()


def update_user(
    user_id: int,
    field: str,
    value
):
    allowed = {
        "telegram_username",
        "roblox_username",
        "roblox_id",
        "ability",
        "anonymous",
        "nickname",
        "description",
        "searching",
        "partner_id",
        "likes",
        "dislikes",
        "bot_happy",
        "bot_good",
        "bot_neutral",
        "bot_bad",
        "banned",
        "ban_reason",
        "banned_at",
    }

    if field not in allowed:
        raise ValueError(
            f"Недопустимое поле: {field}"
        )

    db.execute(
        f"""
        UPDATE users
        SET {field} = ?
        WHERE telegram_id = ?
        """,
        (
            value,
            user_id
        )
    )

    db_commit()


def increment_user(
    user_id: int,
    field: str
):
    allowed = {
        "likes",
        "dislikes",
        "bot_happy",
        "bot_good",
        "bot_neutral",
        "bot_bad",
    }

    if field not in allowed:
        raise ValueError(
            f"Недопустимое поле: {field}"
        )

    db.execute(
        f"""
        UPDATE users
        SET {field} = {field} + 1
        WHERE telegram_id = ?
        """,
        (user_id,)
    )

    db_commit()


# ============================================================
# BAN SYSTEM
# ============================================================

def is_banned(user_id: int) -> bool:
    user = get_user(user_id)

    if not user:
        return False

    return bool(user["banned"])


def ban_user(
    user_id: int,
    reason: str
):
    update_user(
        user_id,
        "banned",
        1
    )

    update_user(
        user_id,
        "ban_reason",
        reason
    )

    update_user(
        user_id,
        "banned_at",
        datetime.now().isoformat()
    )


def unban_user(
    user_id: int
):
    update_user(
        user_id,
        "banned",
        0
    )

    update_user(
        user_id,
        "ban_reason",
        ""
    )

    update_user(
        user_id,
        "banned_at",
        None
    )


async def ban_guard(
    message: Message
) -> bool:

    create_user(
        message.from_user.id,
        message.from_user.username
    )

    user = get_user(
        message.from_user.id
    )

    if user and user["banned"]:

        reason = (
            user["ban_reason"]
            or "Причина не указана."
        )

        await message.answer(
            "⛔ <b>Вы заблокированы в боте.</b>\n\n"
            f"Причина: <i>{html.escape(reason)}</i>"
        )

        return True

    return False


# ============================================================
# ABILITIES
# ============================================================

def ability_to_text(
    ability: str
):
    values = {
        "edit":
            "🎣 Я могу смонтировать видео",

        "shoot":
            "🪁 Я могу отснять кадры для видео",

        "both":
            "🦋 Я могу сделать и то, и другое",

        "nothing":
            "🫟 Я ничего не могу",
    }

    return values.get(
        ability,
        "🔷 Не указано"
    )


def can_match(
    my_ability: str,
    other_ability: str
):

    if my_ability == "edit":
        return other_ability in ("shoot", "both")

    if my_ability == "shoot":
        return other_ability in ("edit", "both")

    if my_ability == "both":
        return True

    if my_ability == "nothing":
        return other_ability == "both"

    return False


# ============================================================
# MATCHING
# ============================================================

def find_partner(
    user_id: int
):

    me = get_user(user_id)

    if not me:
        return None

    candidates = db.execute(
        """
        SELECT *
        FROM users

        WHERE searching = 1
        AND telegram_id != ?
        AND partner_id IS NULL
        AND banned = 0

        ORDER BY telegram_id ASC
        """,
        (user_id,)
    ).fetchall()

    for candidate in candidates:

        if not can_match(
            me["ability"],
            candidate["ability"]
        ):
            continue

        if not can_match(
            candidate["ability"],
            me["ability"]
        ):
            continue

        return candidate

    return None


def connect_users(
    user1: int,
    user2: int
):

    db.execute(
        """
        UPDATE users
        SET partner_id = ?,
            searching = 0
        WHERE telegram_id = ?
        """,
        (
            user2,
            user1
        )
    )

    db.execute(
        """
        UPDATE users
        SET partner_id = ?,
            searching = 0
        WHERE telegram_id = ?
        """,
        (
            user1,
            user2
        )
    )

    db_commit()


def disconnect_users(
    user1: int,
    user2: int
):

    db.execute(
        """
        UPDATE users
        SET partner_id = NULL,
            searching = 0
        WHERE telegram_id IN (?, ?)
        """,
        (
            user1,
            user2
        )
    )

    db_commit()

    delete_video_session(user1)
    delete_video_session(user2)


# ============================================================
# PROFILE
# ============================================================

def profile_text(
    user
):

    likes = user["likes"] or 0
    dislikes = user["dislikes"] or 0

    total = likes + dislikes

    positive = (
        round(likes / total * 100)
        if total
        else 0
    )

    nickname = (
        user["nickname"]
        or "Не установлен"
    )

    description = (
        user["description"]
        or "Описание отсутствует."
    )

    roblox = (
        user["roblox_username"]
        or "Не указан"
    )

    anonymous = (
        "Да 🔒"
        if user["anonymous"]
        else "Нет 👤"
    )

    return (
        "🎭 <b>Мой профиль</b>\n\n"

        f"🪁 <b>Псевдоним:</b> "
        f"{html.escape(nickname)}\n\n"

        f"🫐 <b>Описание:</b>\n"
        f"<i>{html.escape(description)}</i>\n\n"

        f"🎮 <b>Roblox:</b> "
        f"<code>{html.escape(roblox)}</code>\n\n"

        f"🛠 <b>Что я могу:</b>\n"
        f"{ability_to_text(user['ability'])}\n\n"

        f"🔒 <b>Анонимность:</b> "
        f"{anonymous}\n\n"

        "🦋 <b>Моя статистика:</b>\n"
        f"👍 {likes}  |  👎 {dislikes}\n"
        f"💙 Положительных: {positive}%"
    )


def profile_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Редактировать",
                    callback_data="edit_profile"
                )
            ]
        ]
    )


def edit_profile_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎭 Псевдоним",
                    callback_data="edit_nickname"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🫐 Описание",
                    callback_data="edit_description"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛠 Возможности",
                    callback_data="edit_ability"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎮 Roblox",
                    callback_data="edit_roblox"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔒 Анонимность",
                    callback_data="edit_anonymous"
                )
            ]
        ]
    )


# ============================================================
# KEYBOARDS
# ============================================================

def start_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌀 Дальше",
                    callback_data="start_next"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Мой профиль",
                    callback_data="my_profile"
                )
            ]
        ]
    )


def ability_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎣 Я могу смонтировать видео",
                    callback_data="ability_edit"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪁 Я могу отснять кадры для видео",
                    callback_data="ability_shoot"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🦋 Я могу сделать и то, и другое",
                    callback_data="ability_both"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🫟 Я ничего не могу",
                    callback_data="ability_nothing"
                )
            ]
        ]
    )


def anonymous_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да 🔒",
                    callback_data="anonymous_yes"
                ),
                InlineKeyboardButton(
                    text="Нет 💙",
                    callback_data="anonymous_no"
                )
            ]
        ]
    )


def search_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить поиск",
                    callback_data="cancel_search"
                )
            ]
        ]
    )


def partner_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Завершить коллаб",
                    callback_data="finish_collab"
                )
            ]
        ]
    )


def finish_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data="finish_yes"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, я нажал случайно",
                    callback_data="finish_no"
                )
            ]
        ]
    )


def partner_rating_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍",
                    callback_data="partner_like"
                ),
                InlineKeyboardButton(
                    text="👎",
                    callback_data="partner_dislike"
                )
            ]
        ]
    )


def bot_rating_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="😄",
                    callback_data="bot_happy"
                ),
                InlineKeyboardButton(
                    text="🙂",
                    callback_data="bot_good"
                )
            ],
            [
                InlineKeyboardButton(
                    text="😐",
                    callback_data="bot_neutral"
                ),
                InlineKeyboardButton(
                    text="😬",
                    callback_data="bot_bad"
                )
            ]
        ]
    )


def idea_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да 🐳",
                    callback_data="idea_yes"
                ),
                InlineKeyboardButton(
                    text="Нет, спасибо",
                    callback_data="idea_no"
                )
            ]
        ]
    )


def complaint_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Написать текстовую жалобу",
                    callback_data="complaint_text"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🫪 У меня плохое настроение",
                    callback_data="complaint_mood"
                )
            ]
        ]
    )


# ============================================================
# ROBLOX API
# ============================================================

async def check_roblox(
    username: str
):

    username = username.strip()

    if username.startswith("@"):
        username = username[1:]

    if not username:
        return False, None

    url = (
        "https://users.roblox.com/v1/"
        "usernames/users"
    )

    payload = {
        "usernames": [username],
        "excludeBannedUsers": False
    }

    try:

        async with httpx.AsyncClient(
            timeout=10
        ) as client:

            response = await client.post(
                url,
                json=payload
            )

        if response.status_code != 200:
            return None, None

        data = response.json()

        users = data.get(
            "data",
            []
        )

        if not users:
            return False, None

        return True, users[0]

    except Exception:
        logger.exception(
            "Roblox API error"
        )

        return None, None


# ============================================================
# VIDEO DATABASE
# ============================================================

def add_video(
    file_id: str,
    admin_id: int
):

    cursor = db.execute(
        """
        INSERT INTO videos (
            file_id,
            added_by
        )
        VALUES (?, ?)
        """,
        (
            file_id,
            admin_id
        )
    )

    db_commit()

    return cursor.lastrowid


def get_videos():

    rows = db.execute(
        """
        SELECT *
        FROM videos
        """
    ).fetchall()

    # Максимально случайный порядок
    rows = list(rows)
    random.shuffle(rows)
    return rows



def get_user_seen_videos(user_id: int) -> list:
    user = get_user(user_id)
    if not user:
        return []
    raw = ""
    try:
        raw = user["seen_videos"] or ""
    except (KeyError, IndexError):
        raw = ""
    if not raw:
        return []
    result = []
    for x in str(raw).split(","):
        x = x.strip()
        if x.isdigit():
            result.append(int(x))
    return result


def mark_videos_seen(user_id: int, video_ids: list):
    existing = set(get_user_seen_videos(user_id))
    for vid in video_ids:
        existing.add(int(vid))
    update_user(
        user_id,
        "seen_videos",
        ",".join(str(v) for v in sorted(existing))
    )

def get_random_video(
    excluded_ids=None
):

    excluded_ids = set(excluded_ids or [])

    videos = get_videos()  # already shuffled

    available = [
        v
        for v in videos
        if v["id"] not in excluded_ids
    ]

    if not available:
        return None

    # Дополнительная случайность
    return random.choice(available)


def delete_video(
    video_id: int
):

    cursor = db.execute(
        """
        DELETE FROM videos
        WHERE id = ?
        """,
        (video_id,)
    )

    db_commit()

    return cursor.rowcount > 0


# ============================================================
# STICKERS
# ============================================================

def add_sticker(
    file_id: str,
    admin_id: int
):

    cursor = db.execute(
        """
        INSERT INTO stickers (
            file_id,
            added_by
        )
        VALUES (?, ?)
        """,
        (
            file_id,
            admin_id
        )
    )

    db_commit()

    return cursor.lastrowid


def get_stickers():

    return db.execute(
        """
        SELECT *
        FROM stickers
        ORDER BY id ASC
        """
    ).fetchall()


def get_random_sticker():

    stickers = get_stickers()

    if not stickers:
        return None

    return random.choice(stickers)


def delete_sticker(
    sticker_id: int
):

    cursor = db.execute(
        """
        DELETE FROM stickers
        WHERE id = ?
        """,
        (sticker_id,)
    )

    db_commit()

    return cursor.rowcount > 0


# ============================================================
# VIDEO SESSIONS
# ============================================================

def encode_history(
    history: list[int]
):
    return ",".join(
        str(x)
        for x in history
    )


def decode_history(
    value: str
):
    if not value:
        return []

    result = []

    for x in value.split(","):
        try:
            result.append(int(x))
        except ValueError:
            pass

    return result


def create_video_session(
    user1: int,
    user2: int,
    video_id: int
):

    history = encode_history(
        [video_id]
    )

    for user_id in (user1, user2):

        db.execute(
            """
            INSERT OR REPLACE INTO video_sessions (
                user_id,
                partner_id,
                history,
                current_index,
                reroll_count,
                pressed_user1,
                pressed_user2,
                choices_json
            )
            VALUES (?, ?, ?, 0, 0, 0, 0, '{}')
            """,
            (
                user_id,
                user2 if user_id == user1 else user1,
                history
            )
        )

    db_commit()


def get_video_session(
    user_id: int
):

    row = db.execute(
        """
        SELECT *
        FROM video_sessions
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    if not row:
        return None

    choices = {}
    try:
        keys = list(row.keys())
        raw_choices = row["choices_json"] if "choices_json" in keys else None
    except Exception:
        raw_choices = None
    if raw_choices:
        try:
            import json
            choices = json.loads(raw_choices) or {}
        except Exception:
            choices = {}

    return {
        "user_id": row["user_id"],
        "partner_id": row["partner_id"],
        "history": decode_history(row["history"]),
        "current_index": row["current_index"],
        "reroll_count": row["reroll_count"],
        "pressed_user1": bool(row["pressed_user1"]),
        "pressed_user2": bool(row["pressed_user2"]),
        "choices": choices,
    }


def save_video_session(
    user_id: int,
    session: dict
):

    import json

    db.execute(
        """
        UPDATE video_sessions

        SET history = ?,
            current_index = ?,
            reroll_count = ?,
            pressed_user1 = ?,
            pressed_user2 = ?,
            choices_json = ?

        WHERE user_id = ?
        """,
        (
            encode_history(
                session["history"]
            ),
            session["current_index"],
            session["reroll_count"],
            int(session.get("pressed_user1", 0)),
            int(session.get("pressed_user2", 0)),
            json.dumps(session.get("choices") or {}, ensure_ascii=False),
            user_id
        )
    )

    db_commit()


def save_pair_video_session(
    user1: int,
    user2: int,
    session: dict
):

    import json

    choices_json = json.dumps(
        session.get("choices") or {},
        ensure_ascii=False
    )

    for user_id in (user1, user2):

        db.execute(
            """
            UPDATE video_sessions

            SET partner_id = ?,
                history = ?,
                current_index = ?,
                reroll_count = ?,
                pressed_user1 = ?,
                pressed_user2 = ?,
                choices_json = ?

            WHERE user_id = ?
            """,
            (
                user2 if user_id == user1 else user1,
                encode_history(
                    session["history"]
                ),
                session["current_index"],
                session["reroll_count"],
                int(session.get("pressed_user1", 0)),
                int(session.get("pressed_user2", 0)),
                choices_json,
                user_id
            )
        )

    db_commit()


def delete_video_session(
    user_id: int
):

    db.execute(
        """
        DELETE FROM video_sessions
        WHERE user_id = ?
        """,
        (user_id,)
    )

    db_commit()


# ============================================================
# CHAT LOG
# ============================================================

def log_message(
    message: Message,
    partner_id: int
):

    message_type = "unknown"
    text = ""

    if message.text:
        message_type = "text"
        text = message.text

    elif message.photo:
        message_type = "photo"
        text = message.caption or ""

    elif message.video:
        message_type = "video"
        text = message.caption or ""

    elif message.document:
        message_type = "document"
        text = message.caption or ""

    elif message.voice:
        message_type = "voice"

    elif message.video_note:
        message_type = "video_note"

    elif message.sticker:
        message_type = "sticker"

    elif message.audio:
        message_type = "audio"
        text = message.caption or ""

    db.execute(
        """
        INSERT INTO chat_logs (
            sender_id,
            partner_id,
            message_id,
            message_type,
            message_text
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            message.from_user.id,
            partner_id,
            message.message_id,
            message_type,
            text
        )
    )

    db_commit()


def get_chat_logs(
    user1: int,
    user2: int
):

    return db.execute(
        """
        SELECT *
        FROM chat_logs

        WHERE
        (
            sender_id = ?
            AND partner_id = ?
        )
        OR
        (
            sender_id = ?
            AND partner_id = ?
        )

        ORDER BY id ASC
        """,
        (
            user1,
            user2,
            user2,
            user1
        )
    ).fetchall()


def clear_chat_logs(
    user1: int,
    user2: int
):

    db.execute(
        """
        DELETE FROM chat_logs

        WHERE
        (
            sender_id = ?
            AND partner_id = ?
        )
        OR
        (
            sender_id = ?
            AND partner_id = ?
        )
        """,
        (
            user1,
            user2,
            user2,
            user1
        )
    )

    db_commit()


# ============================================================
# PARTNER INFO
# ============================================================

def partner_info(
    partner
):

    nickname = (
        partner["nickname"]
        or partner["roblox_username"]
        or "Пользователь"
    )

    description = (
        partner["description"]
        or "Описание отсутствует."
    )

    likes = partner["likes"] or 0

    ability = ability_to_text(
        partner["ability"]
    )

    if partner["anonymous"]:

        contact = (
            f"@{partner['roblox_username']}"
            if partner["roblox_username"]
            else "пользователь"
        )

        return (
            f"🎭 <b>С вами связался "
            f"{html.escape(contact)}!</b>\n\n"

            f"🪁 <b>Псевдоним:</b> "
            f"{html.escape(nickname)}\n\n"

            f"🫐 <b>О себе:</b>\n"
            f"<i>{html.escape(description)}</i>\n\n"

            f"🛠 <b>Что он может:</b>\n"
            f"{ability}\n\n"

            "🔒 <i>Пользователь предпочел "
            "остаться анонимным.</i>\n\n"

            f"🐳 <b>Лайков:</b> {likes}"
        )

    username = partner["telegram_username"]

    contact = (
        "@" + username.lstrip("@")
        if username
        else "пользователь"
    )

    return (
        f"🎭 <b>С вами связался "
        f"{html.escape(contact)}!</b>\n\n"

        f"🪁 <b>Псевдоним:</b> "
        f"{html.escape(nickname)}\n\n"

        f"🫐 <b>О себе:</b>\n"
        f"<i>{html.escape(description)}</i>\n\n"

        f"🛠 <b>Что он может:</b>\n"
        f"{ability}\n\n"

        f"🐳 <b>Лайков:</b> {likes}"
    )


# ============================================================
# START
# ============================================================

async def start_flow(
    user_id: int,
    state: Optional[FSMContext] = None,
    message: Optional[Message] = None
):

    create_user(
        user_id
    )

    user = get_user(user_id)

    if user["banned"]:

        reason = (
            user["ban_reason"]
            or "Причина не указана."
        )

        if message:
            await message.answer(
                "⛔ <b>Вы заблокированы в боте.</b>\n\n"
                f"Причина: <i>{html.escape(reason)}</i>"
            )

        return

    if state:
        await state.clear()

    if user["partner_id"]:

        if message:
            await message.answer(
                "🤝 <b>У тебя уже есть собеседник.</b>\n\n"
                "🌀 Коллаборация продолжается.",
                reply_markup=partner_keyboard()
            )

        return

    if user["searching"]:

        if message:
            await message.answer(
                "🔎 <b>Ты уже находишься в поиске.</b>",
                reply_markup=search_keyboard()
            )

        return

    if message:

        await message.answer(
            "🎭 <b>Приветствуем!</b>\n\n"
            "<i>Хочешь найти креатора?</i> 🦋",
            reply_markup=start_keyboard()
        )


@dp.message(CommandStart())
async def start_command(
    message: Message,
    state: FSMContext
):
    logger.info(
        "Получен /start от user_id=%s username=%s",
        message.from_user.id,
        message.from_user.username
    )

    try:
        create_user(
            message.from_user.id,
            message.from_user.username
        )

        await start_flow(
            message.from_user.id,
            state,
            message
        )
    except Exception as e:
        logger.exception("Ошибка в start_flow: %s", e)




# ============================================================
# START NEXT
# ============================================================

@dp.callback_query(
    F.data == "start_next"
)
async def start_next(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    if is_banned(callback.from_user.id):
        return

    await callback.message.answer(
        "🪁 <b>Введи нужную информацию.</b>\n\n"
        "<i>Что ты сможешь сделать для коллаба?</i>",
        reply_markup=ability_keyboard()
    )


# ============================================================
# ABILITY
# ============================================================

@dp.callback_query(
    F.data.startswith("ability_")
)
async def choose_ability(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if user and user["partner_id"]:

        await callback.message.answer(
            "⚠️ <b>Сейчас нельзя менять возможности.</b>\n\n"
            "🌀 Сначала заверши текущую коллаборацию."
        )

        return

    ability = callback.data.replace(
        "ability_",
        ""
    )

    await state.update_data(
        editing_ability=False
    )

    update_user(
        user_id,
        "ability",
        ability
    )

    await callback.message.answer(
        "🎮 <b>Введи свой никнейм в Roblox.</b>\n\n"
        "<i>Я проверю, существует ли такой пользователь.</i>"
    )

    await state.set_state(
        UserState.waiting_for_roblox
    )


# ============================================================
# RECEIVE ROBLOX
# ============================================================

@dp.message(
    UserState.waiting_for_roblox
)
async def receive_roblox(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    if not message.text:

        await message.answer(
            "❌ Введи Roblox username текстом."
        )

        return

    username = message.text.strip()

    await message.answer(
        "🔷 <i>Проверяю Roblox...</i>"
    )

    exists, data = await check_roblox(
        username
    )

    if exists is None:

        await message.answer(
            "⚠️ <b>Roblox временно не отвечает.</b>\n\n"
            "Попробуй ещё раз."
        )

        return

    if not exists:

        await message.answer(
            "❌ <b>Такого Roblox-пользователя нет.</b>\n\n"
            "Проверь написание и попробуй снова."
        )

        return

    real_username = data["name"]

    update_user(
        message.from_user.id,
        "roblox_username",
        real_username
    )

    update_user(
        message.from_user.id,
        "roblox_id",
        data["id"]
    )

    data_state = await state.get_data()

    if data_state.get(
        "editing_roblox"
    ):

        await state.clear()

        await message.answer(
            "💙 <b>Roblox username изменён!</b>\n\n"
            f"🎮 <code>{html.escape(real_username)}</code>"
        )

        return

    telegram_username = (
        message.from_user.username
    )

    if not telegram_username:

        await message.answer(
            "❌ <b>У тебя нет Telegram username.</b>\n\n"
            "Установи username в настройках Telegram "
            "и попробуй снова."
        )

        return

    update_user(
        message.from_user.id,
        "telegram_username",
        telegram_username
    )

    await message.answer(
        "💙 <b>Roblox найден!</b>\n\n"
        f"🎮 <code>{html.escape(real_username)}</code>\n\n"

        "🔒 <i>Ты хочешь остаться анонимным?</i>",
        reply_markup=anonymous_keyboard()
    )


# ============================================================
# ANONYMOUS
# ============================================================

@dp.callback_query(
    F.data.startswith("anonymous_")
)
async def anonymous_choice(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if user and user["partner_id"]:

        await callback.message.answer(
            "⚠️ <b>Сейчас нельзя менять анонимность.</b>\n\n"
            "Сначала заверши коллаборацию."
        )

        return

    anonymous = (
        callback.data == "anonymous_yes"
    )

    data = await state.get_data()

    if data.get("editing_anonymous"):

        update_user(
            user_id,
            "anonymous",
            int(anonymous)
        )

        await state.clear()

        await callback.message.answer(
            "💙 <b>Настройка изменена!</b>\n\n"
            +
            (
                "🔒 Ты остаёшься анонимным."
                if anonymous
                else
                "👤 Твой Telegram username "
                "будет виден собеседнику."
            )
        )

        return

    update_user(
        user_id,
        "anonymous",
        int(anonymous)
    )

    update_user(
        user_id,
        "searching",
        1
    )

    await state.clear()

    await callback.message.answer(
        "🌀 <b>Ищем собеседника...</b>\n\n"
        "<i>Подбираем пользователя "
        "с подходящими возможностями.</i>",
        reply_markup=search_keyboard()
    )

    await find_and_connect(
        user_id
    )


# ============================================================
# FIND AND CONNECT
# ============================================================

async def find_and_connect(
    user_id: int
):

    partner = find_partner(
        user_id
    )

    if not partner:
        return False

    partner_id = partner["telegram_id"]

    # Повторная проверка — защита от двойного совпадения
    me = get_user(user_id)

    if not me:
        return False

    if me["partner_id"] is not None:
        return False

    if partner["partner_id"] is not None:
        return False

    connect_users(
        user_id,
        partner_id
    )

    me = get_user(user_id)
    partner = get_user(partner_id)

    await bot.send_message(
        user_id,

        "🎉 <b>Собеседник найден!</b>\n\n"
        + partner_info(partner) +
        "\n\n"
        "🦋 <i>Можете начинать общение!</i>\n\n"
        "🌀 Управление коллабом — кнопкой ниже.",

        reply_markup=partner_keyboard()
    )

    await bot.send_message(
        partner_id,

        "🎉 <b>Собеседник найден!</b>\n\n"
        + partner_info(me) +
        "\n\n"
        "🦋 <i>Можете начинать общение!</i>\n\n"
        "🌀 Управление коллабом — кнопкой ниже.",

        reply_markup=partner_keyboard()
    )

    asyncio.create_task(
        send_idea_after_minute(
            user_id,
            partner_id
        )
    )

    return True


# ============================================================
# IDEA AFTER ONE MINUTE
# ============================================================

async def send_idea_after_minute(
    user1: int,
    user2: int
):

    await asyncio.sleep(60)

    current1 = get_user(user1)
    current2 = get_user(user2)

    if not current1 or not current2:
        return

    if current1["partner_id"] != user2:
        return

    if current2["partner_id"] != user1:
        return

    text = (
        " • <b>Не можете придумать, что снять?</b>\n\n"
        "<i>Хотите я предложу вам идеи "
        "для звука/видео?</i>"
    )

    try:

        await bot.send_message(
            user1,
            text,
            reply_markup=idea_keyboard()
        )

        await bot.send_message(
            user2,
            text,
            reply_markup=idea_keyboard()
        )

    except Exception:
        logger.exception(
            "Ошибка idea timer"
        )


# ============================================================
# IDEA NO
# ============================================================

@dp.callback_query(
    F.data == "idea_no"
)
async def idea_no(
    callback: CallbackQuery
):

    await callback.answer()

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "💙 Хорошо! Продолжайте коллаборацию."
    )


# ============================================================
# IDEA YES
# ============================================================

@dp.callback_query(
    F.data == "idea_yes"
)
async def idea_yes(
    callback: CallbackQuery
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if not user or not user["partner_id"]:

        await callback.message.answer(
            "❌ Коллаборация уже завершена."
        )

        return

    partner_id = user["partner_id"]

    session = get_video_session(
        user_id
    )

    if session:

        await send_current_video(
            user_id
        )

        return

    seen1 = get_user_seen_videos(user_id)
    seen2 = get_user_seen_videos(partner_id)
    excluded = list(set(seen1 + seen2))
    first_video = get_random_video(excluded_ids=excluded)

    # Если все видео уже видели — начинаем заново из полного списка
    if not first_video:
        first_video = get_random_video()


    if not first_video:

        await callback.message.answer(
            "🫟 <b>Пока нет доступных видео.</b>\n\n"
            "Администратор ещё не добавил идеи."
        )

        return

    create_video_session(
        user_id,
        partner_id,
        first_video["id"]
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await send_current_video(
        user_id
    )


# ============================================================
# SEND VIDEO
# ============================================================


# ============================================================
# VIDEO CHOICE STATS
# ============================================================

def increment_video_stat(video_id: int, field: str):

    allowed = {
        "selected_count",
        "own_idea_count"
    }

    if field not in allowed:
        raise ValueError("Недопустимое поле видео")

    db.execute(
        f"""
        UPDATE videos
        SET {field} = COALESCE({field}, 0) + 1
        WHERE id = ?
        """,
        (video_id,)
    )

    db_commit()


def get_video_stats(video_id: int):

    row = db.execute(
        """
        SELECT selected_count, own_idea_count
        FROM videos
        WHERE id = ?
        """,
        (video_id,)
    ).fetchone()

    return row


def clear_session_choices(session: dict):
    """Сбросить выборы текущего видео (при перевыборе)."""
    session["choices"] = {}


def get_current_video_id(session: dict):
    history = session.get("history") or []
    index = session.get("current_index", 0)
    if not history or index < 0 or index >= len(history):
        return None
    return history[index]


def video_keyboard(
    session: dict
):
    """Клавиатура под видео: выбор звука + навигация."""

    rows = [
        [
            InlineKeyboardButton(
                text="🔥 Я выбрал этот звук",
                callback_data="video_choice_selected"
            )
        ],
        [
            InlineKeyboardButton(
                text="💡 У меня своя идея",
                callback_data="video_choice_own"
            )
        ]
    ]

    navigation = []

    if session.get("current_index", 0) > 0:
        navigation.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data="video_previous"
            )
        )

    if session.get("reroll_count", 0) < 3:
        navigation.append(
            InlineKeyboardButton(
                text="🔄 Перевыбрать",
                callback_data="video_reroll"
            )
        )

    if navigation:
        rows.append(navigation)

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def send_current_video(
    user_id: int
):

    user = get_user(user_id)

    if not user or not user["partner_id"]:
        return

    session = get_video_session(
        user_id
    )

    if not session:
        return

    history = session["history"]

    if not history:
        return

    index = session["current_index"]

    if index >= len(history):
        return

    video_id = history[index]

    video = db.execute(
        """
        SELECT *
        FROM videos
        WHERE id = ?
        """,
        (video_id,)
    ).fetchone()

    if not video:
        return

    selected_count = 0
    try:
        selected_count = int(video["selected_count"] or 0)
    except (KeyError, TypeError, ValueError):
        selected_count = 0

    popular_text = ""
    if selected_count > 0:
        popular_text = "\n\n🔥 <b>Выбирают чаще всего</b>"

    caption = (
        "🐳 <b>Рандомный звук для вашего видео найден!</b>"
        f"{popular_text}\n\n"
        "💙 <i>Совет: вы должны найти компромисс, "
        "если одному человеку нравится, а другому нет.</i>\n\n"
        "🦋 Не устраивайте конфликты!"
    )

    keyboard = video_keyboard(session)

    try:
        await bot.send_video(
            user_id,
            video["file_id"],
            caption=caption,
            reply_markup=keyboard
        )
        logger.info("Видео отправлено user_id=%s video_id=%s", user_id, video_id)
        try:
            mark_videos_seen(user_id, [video_id])
            partner = get_user(user_id)
            if partner and partner["partner_id"]:
                mark_videos_seen(partner["partner_id"], [video_id])
        except Exception as e:
            logger.exception("Не удалось отметить seen: %s", e)
    except Exception as e:
        logger.exception(
            "Не удалось отправить видео user_id=%s video_id=%s: %s",
            user_id, video_id, e
        )




# ============================================================
# VIDEO REROLL
# ============================================================

@dp.callback_query(
    F.data == "video_reroll"
)
async def video_reroll(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    await callback.answer()

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if not user or not user["partner_id"]:
        return

    partner_id = user["partner_id"]

    session = get_video_session(
        user_id
    )

    if not session:
        return

    if session["reroll_count"] >= 3:

        await callback.answer(
            "🫟 Лимит перевыборов уже достигнут.",
            show_alert=True
        )

        return

    # Определяем, кто нажал
    if user_id < partner_id:

        if session["pressed_user1"]:

            await callback.answer(
                "🌀 Ты уже нажал. Ждём второго.",
                show_alert=True
            )

            return

        session["pressed_user1"] = True

    else:

        if session["pressed_user2"]:

            await callback.answer(
                "🌀 Ты уже нажал. Ждём второго.",
                show_alert=True
            )

            return

        session["pressed_user2"] = True

    save_pair_video_session(
        user_id,
        partner_id,
        session
    )

    count = int(
        session["pressed_user1"]
    ) + int(
        session["pressed_user2"]
    )

    if count == 1:

        await callback.message.answer(
            "🌀 <b>Перевыбор: 1/2</b>\n\n"
            "<i>Ждём, пока второй пользователь "
            "тоже согласится.</i>"
        )

        try:

            await bot.send_message(
                partner_id,
                "🌀 <b>Перевыбор: 1/2</b>\n\n"
                "<i>Твой собеседник уже нажал. "
                "Если хочешь сменить видео — "
                "нажми «Перевыбрать».</i>"
            )

        except Exception as e:
            logger.exception("Не удалось отправить партнёру: %s", e)

        return

    # ========================================================
    # 2/2
    # ========================================================

    new_video = get_random_video(
        excluded_ids=session["history"]
    )

    if not new_video:

        session["pressed_user1"] = False
        session["pressed_user2"] = False

        save_pair_video_session(
            user_id,
            partner_id,
            session
        )

        await callback.message.answer(
            "🫟 <b>Других видео пока нет.</b>\n\n"
            "Администратору нужно добавить "
            "ещё видео."
        )

        return

    session["history"].append(
        new_video["id"]
    )

    session["current_index"] = (
        len(session["history"]) - 1
    )

    session["reroll_count"] += 1

    session["pressed_user1"] = False
    session["pressed_user2"] = False

    # При перевыборе сбрасываем выборы для нового видео
    clear_session_choices(session)

    save_pair_video_session(
        user_id,
        partner_id,
        session
    )

    await bot.send_message(
        user_id,
        "🔷 <b>2/2 — оба согласились!</b>\n\n"
        f"🌀 Перевыбор "
        f"{session['reroll_count']}/3"
    )

    await bot.send_message(
        partner_id,
        "🔷 <b>2/2 — оба согласились!</b>\n\n"
        f"🌀 Перевыбор "
        f"{session['reroll_count']}/3"
    )

    await send_current_video(
        user_id
    )

    await send_current_video(
        partner_id
    )


# ============================================================
# PREVIOUS VIDEO
# ============================================================

@dp.callback_query(
    F.data == "video_previous"
)
async def video_previous(
    callback: CallbackQuery
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if not user or not user["partner_id"]:
        return

    session = get_video_session(
        user_id
    )

    if not session:
        return

    if session["current_index"] <= 0:
        return

    session["current_index"] -= 1

    # Назад не требует согласия второго.
    save_pair_video_session(
        user_id,
        user["partner_id"],
        session
    )

    await send_current_video(
        user_id
    )


# ============================================================
# VIDEO CHOICE: «Я выбрал этот звук» / «У меня своя идея»
# ============================================================

async def save_video_choice(
    user_id: int,
    choice: str
):
    """
    Сохраняет выбор пользователя для текущего видео.
    Один пользователь — один выбор. Повторное нажатие игнорируется.
    Возвращает (video_id, partner_id, session, already_chose) или None.
    """

    user = get_user(user_id)

    if not user or not user["partner_id"]:
        return None

    partner_id = user["partner_id"]
    session = get_video_session(user_id)

    if not session:
        return None

    video_id = get_current_video_id(session)

    if video_id is None:
        return None

    choices = session.get("choices") or {}
    video_key = str(video_id)
    video_choices = dict(choices.get(video_key) or {})

    # Уже выбирал — не меняем первоначальный выбор
    if str(user_id) in video_choices:
        return video_id, partner_id, session, True

    video_choices[str(user_id)] = choice
    choices[video_key] = video_choices
    session["choices"] = choices

    save_pair_video_session(user_id, partner_id, session)

    return video_id, partner_id, session, False


@dp.callback_query(
    F.data == "video_choice_selected"
)
async def video_choice_selected(
    callback: CallbackQuery
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    result = await save_video_choice(user_id, "selected")

    if not result:
        return

    video_id, partner_id, session, already = result

    if already:
        await callback.message.answer(
            "🌀 <b>Ты уже сделал выбор по этому видео.</b>"
        )
        return

    choices = (session.get("choices") or {}).get(str(video_id)) or {}
    my_choice = choices.get(str(user_id))
    partner_choice = choices.get(str(partner_id))

    if my_choice == "selected" and partner_choice == "selected":

        if not choices.get("_counted"):
            increment_video_stat(video_id, "selected_count")
            choices["_counted"] = True
            session["choices"][str(video_id)] = choices
            save_pair_video_session(user_id, partner_id, session)

        text = (
            "🔥 <b>Вы оба выбрали этот звук!</b>\n\n"
            "🦋 Этот выбор сохранён."
        )

        await bot.send_message(user_id, text)
        try:
            await bot.send_message(partner_id, text)
        except Exception as e:
            logger.exception("Не удалось отправить партнёру: %s", e)
        return

    if partner_choice and partner_choice != my_choice:
        text = (
            "🌀 <b>Мнения разошлись..</b>\n\n"
            "💙 Видео остаётся доступным — "
            "возможно, оно ещё пригодится."
        )
        await bot.send_message(user_id, text)
        try:
            await bot.send_message(partner_id, text)
        except Exception as e:
            logger.exception("Не удалось отправить партнёру: %s", e)
        return

    await callback.message.answer(
        "🌀 <b>Твой выбор сохранён.</b>\n\n"
        "<i>Ждём мнение второго пользователя...</i>"
    )


@dp.callback_query(
    F.data == "video_choice_own"
)
async def video_choice_own(
    callback: CallbackQuery
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    result = await save_video_choice(user_id, "own")

    if not result:
        return

    video_id, partner_id, session, already = result

    if already:
        await callback.message.answer(
            "🌀 <b>Ты уже сделал выбор по этому видео.</b>"
        )
        return

    choices = (session.get("choices") or {}).get(str(video_id)) or {}
    my_choice = choices.get(str(user_id))
    partner_choice = choices.get(str(partner_id))

    if my_choice == "own" and partner_choice == "own":

        if not choices.get("_counted"):
            increment_video_stat(video_id, "own_idea_count")
            choices["_counted"] = True
            session["choices"][str(video_id)] = choices
            save_pair_video_session(user_id, partner_id, session)

        text = (
            "💙 <b>Хорошо.</b>\n\n"
            "🦋 Удачных съёмок!"
        )

        await bot.send_message(user_id, text)
        try:
            await bot.send_message(partner_id, text)
        except Exception as e:
            logger.exception("Не удалось отправить партнёру: %s", e)
        return

    if partner_choice and partner_choice != my_choice:
        text = (
            "🌀 <b>Мнения разошлись..</b>\n\n"
            "💙 Видео остаётся доступным — "
            "возможно, оно ещё пригодится."
        )
        await bot.send_message(user_id, text)
        try:
            await bot.send_message(partner_id, text)
        except Exception as e:
            logger.exception("Не удалось отправить партнёру: %s", e)
        return

    await callback.message.answer(
        "🌀 <b>Твой выбор сохранён.</b>\n\n"
        "<i>Ждём мнение второго пользователя...</i>"
    )


# ============================================================
# CANCEL SEARCH
# ============================================================

@dp.callback_query(
    F.data == "cancel_search"
)
async def cancel_search(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user_id = callback.from_user.id

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if not user or not user["searching"]:

        await callback.message.answer(
            "🌀 Поиск уже не активен."
        )

        return

    update_user(
        user_id,
        "searching",
        0
    )

    await state.clear()

    await callback.message.answer(
        "❌ <b>Поиск отменён.</b>\n\n"
        "🎭 Чтобы начать снова — /start"
    )


@dp.message(Command("cancel"))
async def cancel_command(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    user_id = message.from_user.id

    user = get_user(user_id)

    if user and user["searching"]:

        update_user(
            user_id,
            "searching",
            0
        )

        await state.clear()

        await message.answer(
            "❌ <b>Поиск отменён.</b>\n\n"
            "🎭 /start — начать заново"
        )

    else:

        await message.answer(
            "🌀 Сейчас активного поиска нет."
        )


# ============================================================
# FINISH COLLAB
# ============================================================

@dp.callback_query(
    F.data == "finish_collab"
)
async def finish_collab(
    callback: CallbackQuery
):

    await callback.answer()

    user = get_user(
        callback.from_user.id
    )

    if not user or not user["partner_id"]:

        await callback.message.answer(
            "🌀 У тебя сейчас нет собеседника."
        )

        return

    await callback.message.answer(
        "❓ <b>Вы точно хотите завершить коллаб?</b>\n\n"
        "<i>Это потеряет вашего собеседника!</i>",
        reply_markup=finish_keyboard()
    )


@dp.callback_query(
    F.data == "finish_no"
)
async def finish_no(
    callback: CallbackQuery
):

    await callback.answer()

    await callback.message.answer(
        "💙 Хорошо! Я понял, что ты нажал случайно.",
        reply_markup=partner_keyboard()
    )


async def finish_collab_for_user(
    user_id: int,
    state: FSMContext
):

    user = get_user(user_id)

    if not user or not user["partner_id"]:
        return

    partner_id = user["partner_id"]

    await state.update_data(
        rated_partner_id=partner_id
    )

    disconnect_users(
        user_id,
        partner_id
    )

    try:

        await bot.send_message(
            partner_id,

            "❌ <b>Ваш собеседник завершил "
            "коллаборацию.</b>\n\n"

            "🎭 Спасибо за участие!\n\n"

            "🌀 /start — найти нового собеседника"
        )

    except Exception:
        pass

    await bot.send_message(
        user_id,

        "🦋 <b>Коллаб завершен.</b>\n\n"
        "Оцените вашего собеседника:",

        reply_markup=partner_rating_keyboard()
    )


@dp.callback_query(
    F.data == "finish_yes"
)
async def finish_yes(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    await finish_collab_for_user(
        callback.from_user.id,
        state
    )


@dp.message(Command("stop"))
async def stop_command(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    await finish_collab_for_user(
        message.from_user.id,
        state
    )


# ============================================================
# PARTNER RATING
# ============================================================

async def get_rated_partner(
    state: FSMContext
):

    data = await state.get_data()

    return data.get(
        "rated_partner_id"
    )


@dp.callback_query(
    F.data == "partner_like"
)
async def partner_like(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    partner_id = await get_rated_partner(
        state
    )

    if partner_id:

        increment_user(
            partner_id,
            "likes"
        )

    db.execute(
        """
        INSERT INTO feedback (
            telegram_id,
            feedback_type,
            text
        )
        VALUES (?, ?, ?)
        """,
        (
            callback.from_user.id,
            "partner_like",
            ""
        )
    )

    db_commit()

    await callback.message.answer(
        "💙 Вы оценили собеседника на «👍».\n\n"
        "Теперь оцените работу бота:",

        reply_markup=bot_rating_keyboard()
    )


@dp.callback_query(
    F.data == "partner_dislike"
)
async def partner_dislike(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    partner_id = await get_rated_partner(
        state
    )

    if partner_id:

        increment_user(
            partner_id,
            "dislikes"
        )

    db.execute(
        """
        INSERT INTO feedback (
            telegram_id,
            feedback_type,
            text
        )
        VALUES (?, ?, ?)
        """,
        (
            callback.from_user.id,
            "partner_dislike",
            ""
        )
    )

    db_commit()

    await callback.message.answer(
        "😢 <b>Вы оценили собеседника на 👎</b>\n\n"
        "Какая на это причина?",

        reply_markup=complaint_keyboard()
    )


# ============================================================
# COMPLAINT — TEXT
# ============================================================

@dp.callback_query(
    F.data == "complaint_text"
)
async def complaint_text(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    await callback.message.answer(
        "👀 <b>Напишите жалобу:</b> что конкретно "
        "вас не устроило в вашем собеседнике?\n\n"

        "Например: <i>спам / оскорбления / "
        "непрошенная критика.</i>\n\n"

        "⚠️ Помните, за ложную жалобу вам "
        "выдадут бан в боте."
    )

    await state.set_state(
        UserState.waiting_for_complaint
    )


# ============================================================
# COMPLAINT — BAD MOOD
# ============================================================

@dp.callback_query(
    F.data == "complaint_mood"
)
async def complaint_mood(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user_id = callback.from_user.id

    partner_id = await get_rated_partner(
        state
    )

    # Сохраняем запись о выборе
    db.execute(
        """
        INSERT INTO complaints (
            complainant_id,
            accused_id,
            reason_type,
            reason_text,
            status
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            partner_id,
            "bad_mood",
            "",
            "not_a_complaint"
        )
    )

    db_commit()

    sticker = get_random_sticker()

    if sticker:

        try:

            await bot.send_sticker(
                user_id,
                sticker["file_id"]
            )

        except Exception:
            await callback.message.answer(
                "🫪"
            )

    else:

        await callback.message.answer(
            "🫪"
        )

    await state.clear()

    # Автоматически запускаем /start
    await asyncio.sleep(0.5)

    await start_flow(
        user_id,
        None,
        callback.message
    )


# ============================================================
# COMPLAINT — RECEIVE TEXT
# ============================================================

@dp.message(
    UserState.waiting_for_complaint
)
async def receive_complaint(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    if not message.text:

        await message.answer(
            "📝 Пожалуйста, отправь жалобу текстом."
        )

        return

    text = message.text.strip()

    if len(text) < 3:

        await message.answer(
            "🫟 Жалоба слишком короткая."
        )

        return

    user_id = message.from_user.id

    partner_id = await get_rated_partner(
        state
    )

    if not partner_id:

        await message.answer(
            "⚠️ Не удалось определить собеседника."
        )

        await state.clear()

        return

    # Создаем жалобу
    cursor = db.execute(
        """
        INSERT INTO complaints (
            complainant_id,
            accused_id,
            reason_type,
            reason_text,
            status
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            partner_id,
            "text",
            text,
            "new"
        )
    )

    complaint_id = cursor.lastrowid

    db_commit()

    await message.answer(
        "💙 <b>Спасибо за обращение!</b>\n\n"
        "Если пользователь действительно нарушил "
        "правила, он получит соответствующее наказание."
    )

    # ========================================================
    # ОТПРАВЛЯЕМ АДМИНУ ИНФОРМАЦИЮ
    # ========================================================

    complainant = get_user(user_id)
    accused = get_user(partner_id)

    complainant_name = (
        "@" + complainant["telegram_username"]
        if complainant and complainant["telegram_username"]
        else "username отсутствует"
    )

    accused_name = (
        "@" + accused["telegram_username"]
        if accused and accused["telegram_username"]
        else "username отсутствует"
    )

    admin_header = (
        "🚨 <b>НОВАЯ ЖАЛОБА</b>\n\n"

        f"🆔 Жалоба: <code>{complaint_id}</code>\n\n"

        f"👤 <b>Жалобщик:</b>\n"
        f"Telegram: {html.escape(complainant_name)}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Roblox: "
        f"<code>{html.escape(complainant['roblox_username'] or 'нет')}</code>\n\n"

        f"🎭 <b>Обвиняемый:</b>\n"
        f"Telegram: {html.escape(accused_name)}\n"
        f"ID: <code>{partner_id}</code>\n"
        f"Roblox: "
        f"<code>{html.escape(accused['roblox_username'] or 'нет')}</code>\n\n"

        f"📝 <b>Причина жалобы:</b>\n"
        f"{html.escape(text)}\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📨 Ниже будет переписка пользователей."
    )

    await bot.send_message(
        ADMIN_ID,
        admin_header
    )

    # ========================================================
    # ПЕРЕСЫЛАЕМ ВСЮ СОХРАНЕННУЮ ПЕРЕПИСКУ
    # ========================================================

    logs = get_chat_logs(
        user_id,
        partner_id
    )

    await bot.send_message(
        ADMIN_ID,
        f"🗂 <b>Переписка — сообщений: {len(logs)}</b>"
    )

    for log in logs:

        try:

            await bot.forward_message(
                chat_id=ADMIN_ID,
                from_chat_id=log["sender_id"],
                message_id=log["message_id"]
            )

            await asyncio.sleep(0.05)

        except Exception as error:

            # Если Telegram не разрешил forward,
            # хотя бы сохраняем текстовую информацию.
            logger.warning(
                "Не удалось переслать сообщение %s: %s",
                log["message_id"],
                error
            )

            sender = get_user(
                log["sender_id"]
            )

            sender_name = (
                sender["nickname"]
                if sender
                else "Пользователь"
            )

            await bot.send_message(
                ADMIN_ID,

                "🫟 <b>Не удалось переслать сообщение</b>\n\n"
                f"От: {html.escape(sender_name)}\n"
                f"Тип: {html.escape(log['message_type'])}\n"
                f"Текст: {html.escape(log['message_text'] or 'нет')}"
            )

    await bot.send_message(
        ADMIN_ID,

        "⚖️ <b>Модерация:</b>\n\n"
        f"/ban {partner_id} нарушение\n"
        f"/unban {partner_id}\n"
        f"/baninfo {partner_id}\n\n"
        f"🆔 Жалоба: <code>{complaint_id}</code>"
    )

    await state.clear()


# ============================================================
# BOT RATING
# ============================================================

@dp.callback_query(
    F.data.in_({
        "bot_happy",
        "bot_good",
        "bot_neutral",
        "bot_bad"
    })
)
async def bot_rating(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    mapping = {
        "bot_happy": (
            "😄",
            "bot_happy"
        ),

        "bot_good": (
            "🙂",
            "bot_good"
        ),

        "bot_neutral": (
            "😐",
            "bot_neutral"
        ),

        "bot_bad": (
            "😬",
            "bot_bad"
        )
    }

    emoji, field = mapping[
        callback.data
    ]

    increment_user(
        callback.from_user.id,
        field
    )

    db.execute(
        """
        INSERT INTO feedback (
            telegram_id,
            feedback_type,
            text
        )
        VALUES (?, ?, ?)
        """,
        (
            callback.from_user.id,
            emoji,
            ""
        )
    )

    db_commit()

    if callback.data in {
        "bot_happy",
        "bot_good"
    }:

        await callback.message.answer(
            "💙 <b>Спасибо, что вы оценили!</b>\n\n"
            "Мы стараемся еще больше улучшать "
            "работу бота🫶"
        )

        await state.clear()

        return

    await callback.message.answer(
        "🟦 <b>Расскажите, почему вы дали "
        "такую оценку.</b>\n\n"
        "<i>Это поможет нам совершенствоваться "
        "все лучше и лучше! 🤍</i>"
    )

    await state.update_data(
        bot_rating=emoji
    )

    await state.set_state(
        UserState.waiting_for_bot_feedback
    )


# ============================================================
# BOT FEEDBACK TEXT
# ============================================================

@dp.message(
    UserState.waiting_for_bot_feedback
)
async def receive_bot_feedback(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    if not message.text:

        await message.answer(
            "🫟 Напиши отзыв текстом."
        )

        return

    user_id = message.from_user.id

    text = message.text.strip()

    data = await state.get_data()

    rating = data.get(
        "bot_rating",
        "?"
    )

    user = get_user(user_id)

    username = (
        user["telegram_username"]
        if user
        else ""
    )

    roblox = (
        user["roblox_username"]
        if user
        else ""
    )

    db.execute(
        """
        INSERT INTO feedback (
            telegram_id,
            feedback_type,
            text
        )
        VALUES (?, ?, ?)
        """,
        (
            user_id,
            f"feedback_{rating}",
            text
        )
    )

    db_commit()

    contact = (
        f"@{username}"
        if username
        else "не указан"
    )

    await bot.send_message(
        ADMIN_ID,

        "📩 <b>ОТЗЫВ О БОТЕ</b>\n\n"

        f"🎭 Оценка: {rating}\n"
        f"👤 Telegram: {html.escape(contact)}\n"
        f"🎮 Roblox: {html.escape(roblox or 'не указан')}\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"

        f"📝 <b>Отзыв:</b>\n"
        f"{html.escape(text)}"
    )

    await message.answer(
        "💙 <b>Спасибо за отзыв!</b>\n\n"
        "🦋 Он отправлен разработчику."
    )

    await state.clear()


# ============================================================
# MY PROFILE
# ============================================================

@dp.message(Command("myprofile"))
async def myprofile(
    message: Message
):

    if await ban_guard(message):
        return

    create_user(
        message.from_user.id,
        message.from_user.username
    )

    user = get_user(
        message.from_user.id
    )

    await message.answer(
        profile_text(user),
        reply_markup=profile_keyboard()
    )


@dp.callback_query(
    F.data == "my_profile"
)
async def myprofile_button(
    callback: CallbackQuery
):

    await callback.answer()

    if is_banned(callback.from_user.id):
        return

    create_user(
        callback.from_user.id,
        callback.from_user.username
    )

    user = get_user(
        callback.from_user.id
    )

    await callback.message.answer(
        profile_text(user),
        reply_markup=profile_keyboard()
    )


# ============================================================
# EDIT PROFILE
# ============================================================

@dp.message(Command("editprofile"))
async def editprofile_command(
    message: Message
):

    if await ban_guard(message):
        return

    await message.answer(
        "⚙️ <b>Редактирование профиля</b>\n\n"
        "<i>Что хочешь изменить?</i>",
        reply_markup=edit_profile_keyboard()
    )


@dp.callback_query(
    F.data == "edit_profile"
)
async def edit_profile_button(
    callback: CallbackQuery
):

    await callback.answer()

    if is_banned(callback.from_user.id):
        return

    await callback.message.answer(
        "⚙️ <b>Редактирование профиля</b>\n\n"
        "<i>Что хочешь изменить?</i>",
        reply_markup=edit_profile_keyboard()
    )


# ============================================================
# EDIT NICKNAME
# ============================================================

@dp.callback_query(
    F.data == "edit_nickname"
)
async def edit_nickname(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    await callback.message.answer(
        "🎭 <b>Введи свой новый псевдоним.</b>\n\n"
        "<i>Он будет отображаться собеседнику.</i>"
    )

    await state.set_state(
        UserState.waiting_for_nickname
    )


@dp.message(
    UserState.waiting_for_nickname
)
async def save_nickname(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    if not message.text:

        await message.answer(
            "❌ Введи псевдоним текстом."
        )

        return

    nickname = message.text.strip()

    if len(nickname) > 50:

        await message.answer(
            "❌ Псевдоним слишком длинный.\n\n"
            "Максимум — 50 символов."
        )

        return

    update_user(
        message.from_user.id,
        "nickname",
        nickname
    )

    await state.clear()

    await message.answer(
        "💙 <b>Псевдоним сохранён!</b>\n\n"
        f"🎭 {html.escape(nickname)}"
    )


# ============================================================
# EDIT DESCRIPTION
# ============================================================

@dp.callback_query(
    F.data == "edit_description"
)
async def edit_description(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    await callback.message.answer(
        "🫐 <b>Введи краткое описание.</b>\n\n"
        "<i>Максимум 100 символов.</i>"
    )

    await state.set_state(
        UserState.waiting_for_description
    )


@dp.message(
    UserState.waiting_for_description
)
async def save_description(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    if not message.text:

        await message.answer(
            "❌ Введи описание текстом."
        )

        return

    description = message.text.strip()

    if len(description) > 100:

        await message.answer(
            f"❌ Описание слишком длинное.\n\n"
            f"Сейчас: {len(description)} символов.\n"
            f"Максимум: 100."
        )

        return

    update_user(
        message.from_user.id,
        "description",
        description
    )

    await state.clear()

    await message.answer(
        "💙 <b>Описание сохранено!</b>\n\n"
        f"<i>{html.escape(description)}</i>"
    )


# ============================================================
# EDIT ROBLOX
# ============================================================

@dp.callback_query(
    F.data == "edit_roblox"
)
async def edit_roblox(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user = get_user(
        callback.from_user.id
    )

    if user and user["partner_id"]:

        await callback.message.answer(
            "⚠️ Сейчас нельзя менять Roblox "
            "во время коллаборации."
        )

        return

    await state.update_data(
        editing_roblox=True
    )

    await callback.message.answer(
        "🎮 <b>Введи новый Roblox username.</b>\n\n"
        "<i>Я проверю его существование.</i>"
    )

    await state.set_state(
        UserState.waiting_for_roblox
    )


# ============================================================
# EDIT ANONYMOUS
# ============================================================

@dp.callback_query(
    F.data == "edit_anonymous"
)
async def edit_anonymous(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user = get_user(
        callback.from_user.id
    )

    if user and user["partner_id"]:

        await callback.message.answer(
            "⚠️ Сейчас нельзя менять "
            "анонимность во время коллаба."
        )

        return

    await state.update_data(
        editing_anonymous=True
    )

    await callback.message.answer(
        "🔒 <b>Ты хочешь остаться анонимным?</b>",
        reply_markup=anonymous_keyboard()
    )


# ============================================================
# EDIT ABILITY
# ============================================================

@dp.callback_query(
    F.data == "edit_ability"
)
async def edit_ability(
    callback: CallbackQuery,
    state: FSMContext
):

    await callback.answer()

    user = get_user(
        callback.from_user.id
    )

    if user and user["partner_id"]:

        await callback.message.answer(
            "⚠️ Сейчас нельзя менять возможности "
            "во время коллаба."
        )

        return

    await state.update_data(
        editing_ability=True
    )

    await callback.message.answer(
        "🛠 <b>Выбери новые возможности:</b>",
        reply_markup=ability_keyboard()
    )



# ============================================================
# RULES
# ============================================================

@dp.message(Command("rules"))
async def rules_command(
    message: Message
):

    if await ban_guard(message):
        return

    await message.answer(
        "👾 <b>Для использования бота вы должны ознакомиться "
        "с короткими простыми правилами:</b>\n\n"

        "1. Спамить, оскорблять, грубо критиковать запрещено. "
        "Имейте уважение к своему собеседнику.\n\n"

        "2. Находить собеседника, а затем сразу его скипать запрещено.\n\n"

        "3. Вбивать ложные юзы и ники вместо своих запрещено.\n\n"

        "4. Вводить собеседника в заблуждение, специально тянуть время запрещено.\n\n"

        "‼️ Нарушение правил карается как временным, так и перманентным баном в боте.\n\n"

        "Также прошу пользователей сразу кидать жалобу на собеседника, "
        "если он нарушил правила выше.\n\n"

        "Если у вас есть вопрос к администратору бота, можете смело "
        "задать его по команде /adminmessage.\n\n"

        "За новостями: @wcuerandomiser"
    )


# ============================================================
# ADMIN MESSAGE (поддержка)
# ============================================================

@dp.message(Command("adminmessage"))
async def adminmessage_command(
    message: Message,
    state: FSMContext
):

    if await ban_guard(message):
        return

    await state.set_state(
        SupportState.waiting_for_admin_message
    )

    await message.answer(
        "📩 <b>Напишите сообщение администратору.</b>\n\n"
        "Можно отправить текст, фото или видео.\n"
        "Для отмены — /cancel"
    )


@dp.message(SupportState.waiting_for_admin_message)
async def receive_admin_message(
    message: Message,
    state: FSMContext
):

    if message.text and message.text.strip().startswith("/"):
        # другая команда — выходим из состояния
        await state.clear()
        return

    user_id = message.from_user.id
    username = message.from_user.username or "без_username"
    user = get_user(user_id)

    nickname = ""
    if user:
        nickname = user["nickname"] or user["roblox_username"] or ""

    header = (
        "📩 <b>Сообщение администратору</b>\n\n"
        f"От: @{html.escape(username)}\n"
        f"ID: <code>{user_id}</code>\n"
    )
    if nickname:
        header += f"Ник: {html.escape(str(nickname))}\n"
    header += "\nОтветьте <b>реплаем</b> на это сообщение, чтобы ответить пользователю."

    try:
        if message.text:
            sent = await bot.send_message(
                ADMIN_ID,
                header + "\n\n" + html.escape(message.text)
            )
        elif message.photo:
            sent = await bot.send_photo(
                ADMIN_ID,
                message.photo[-1].file_id,
                caption=header + (
                    "\n\n" + html.escape(message.caption)
                    if message.caption else ""
                )
            )
        elif message.video:
            sent = await bot.send_video(
                ADMIN_ID,
                message.video.file_id,
                caption=header + (
                    "\n\n" + html.escape(message.caption)
                    if message.caption else ""
                )
            )
        else:
            sent = await bot.send_message(
                ADMIN_ID,
                header + "\n\n(тип сообщения пока не поддерживается для пересылки)"
            )

        ADMIN_REPLY_MAP[sent.message_id] = user_id

        await state.clear()
        await message.answer(
            "✅ <b>Сообщение отправлено администратору.</b>\n"
            "Ожидайте ответа."
        )
    except Exception as e:
        logger.exception("adminmessage failed: %s", e)
        await message.answer(
            "⚠️ Не удалось отправить сообщение. Попробуйте позже."
        )
        await state.clear()


@dp.message(
    lambda m: (
        m.from_user
        and m.from_user.id == ADMIN_ID
        and m.reply_to_message is not None
    )
)
async def admin_reply_to_user(
    message: Message
):
    """Админ отвечает реплаем на сообщение из /adminmessage."""

    if not is_admin(message.from_user.id):
        return

    reply = message.reply_to_message
    if not reply:
        return

    target_id = ADMIN_REPLY_MAP.get(reply.message_id)

    # Также ищем в тексте заголовка ID, если map потерялся после рестарта
    if not target_id and reply.text:
        import re as _re
        m = _re.search(r"ID: <code>(\d+)</code>", reply.text or "")
        if not m:
            m = _re.search(r"ID: (\d+)", reply.text or "")
        if m:
            target_id = int(m.group(1))

    if not target_id and reply.caption:
        import re as _re
        m = _re.search(r"ID: <code>(\d+)</code>", reply.caption or "")
        if not m:
            m = _re.search(r"ID: (\d+)", reply.caption or "")
        if m:
            target_id = int(m.group(1))

    if not target_id:
        return  # не наш reply

    try:
        if message.text:
            await bot.send_message(
                target_id,
                message.text
            )
        elif message.photo:
            await bot.send_photo(
                target_id,
                message.photo[-1].file_id,
                caption=message.caption
            )
        elif message.video:
            await bot.send_video(
                target_id,
                message.video.file_id,
                caption=message.caption
            )
        else:
            await message.answer("Этот тип сообщения пока не поддерживается.")
            return

        await message.answer("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logger.exception("admin reply failed: %s", e)
        await message.answer(f"⚠️ Не удалось отправить: {e}")



# ============================================================
# HELP
# ============================================================

@dp.message(Command("help"))
async def help_command(
    message: Message
):

    if await ban_guard(message):
        return

    await message.answer(
        "💌 <b>Дорогие вкуеры!</b>\n\n"
        "🤖 Бот находится в <b>бета-версии</b>.\n\n"
        "📢 Тгк с новостями: @wcuerandomiser\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📖 <b>Команды бота:</b>\n\n"
        "🎭 <code>/start</code> — начать работу с ботом\n\n"
        "🫐 <code>/myprofile</code> — посмотреть свой профиль, "
        "оценки и статистику\n\n"
        "⚙️ <code>/editprofile</code> — редактировать профиль\n\n"
        "❌ <code>/cancel</code> — отменить поиск собеседника\n\n"
        "🛑 <code>/stop</code> — завершить текущую коллаборацию\n\n"
        "📜 <code>/rules</code> — правила бота\n\n"
        "📩 <code>/adminmessage</code> — написать администратору\n\n"
        "🌀 <code>/help</code> — открыть это меню"
    )


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(
    user_id: int
):
    return user_id == ADMIN_ID


# ============================================================
# ADMIN STATS
# ============================================================

@dp.message(Command("stats"))
async def admin_stats(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    users = db.execute(
        "SELECT COUNT(*) count FROM users"
    ).fetchone()["count"]

    searching = db.execute(
        """
        SELECT COUNT(*) count
        FROM users
        WHERE searching = 1
        """
    ).fetchone()["count"]

    active = db.execute(
        """
        SELECT COUNT(*) count
        FROM users
        WHERE partner_id IS NOT NULL
        """
    ).fetchone()["count"] // 2

    ratings = db.execute(
        """
        SELECT
            COALESCE(SUM(likes), 0) likes,
            COALESCE(SUM(dislikes), 0) dislikes
        FROM users
        """
    ).fetchone()

    bot_stats = db.execute(
        """
        SELECT
            COALESCE(SUM(bot_happy), 0) happy,
            COALESCE(SUM(bot_good), 0) good,
            COALESCE(SUM(bot_neutral), 0) neutral,
            COALESCE(SUM(bot_bad), 0) bad
        FROM users
        """
    ).fetchone()

    complaints = db.execute(
        """
        SELECT COUNT(*) count
        FROM complaints
        """
    ).fetchone()["count"]

    new_complaints = db.execute(
        """
        SELECT COUNT(*) count
        FROM complaints
        WHERE status = 'new'
        """
    ).fetchone()["count"]

    banned = db.execute(
        """
        SELECT COUNT(*) count
        FROM users
        WHERE banned = 1
        """
    ).fetchone()["count"]

    videos = len(
        get_videos()
    )

    stickers = len(
        get_stickers()
    )

    await message.answer(
        "📊 <b>СТАТИСТИКА БОТА</b>\n\n"

        f"👥 Пользователей: {users}\n"
        f"🔎 В поиске: {searching}\n"
        f"🤝 Активных коллабов: {active}\n"
        f"⛔ Заблокировано: {banned}\n\n"

        "🎭 <b>Оценки собеседников</b>\n"
        f"👍 {ratings['likes']}\n"
        f"👎 {ratings['dislikes']}\n\n"

        "🤖 <b>Оценки бота</b>\n"
        f"😄 {bot_stats['happy']}\n"
        f"🙂 {bot_stats['good']}\n"
        f"😐 {bot_stats['neutral']}\n"
        f"😬 {bot_stats['bad']}\n\n"

        "⚖️ <b>Жалобы</b>\n"
        f"Всего: {complaints}\n"
        f"Новых: {new_complaints}\n\n"

        f"🎣 Видео: {videos}\n"
        f"🫐 Стикеры: {stickers}"
    )


# ============================================================
# ADMIN BROADCAST
# ============================================================

@dp.message(Command("broadcast"))
async def admin_broadcast(
    message: Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    await state.set_state(
        AdminState.broadcasting
    )

    await message.answer(
        "📩 <b>Отправь сообщение для рассылки.</b>\n\n"
        "Можно отправить:\n"
        "• текст\n"
        "• фото\n"
        "• видео\n\n"

        "К каждому сообщению автоматически "
        "добавится только подпись:\n"
        "<b>от админа😝</b>"
    )


@dp.message(
    AdminState.broadcasting
)
async def perform_broadcast(
    message: Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):
        return

    users = db.execute(
        """
        SELECT telegram_id
        FROM users
        WHERE banned = 0
        """
    ).fetchall()

    success = 0
    failed = 0

    for row in users:

        user_id = row["telegram_id"]

        try:

            if message.text:

                await bot.send_message(
                    user_id,
                    "от админа😝\n\n"
                    + message.text
                )

            elif message.photo:

                await bot.send_photo(
                    user_id,
                    message.photo[-1].file_id,
                    caption="от админа😝"
                )

            elif message.video:

                await bot.send_video(
                    user_id,
                    message.video.file_id,
                    caption="от админа😝"
                )

            else:

                failed += 1
                continue

            success += 1

            await asyncio.sleep(0.04)

        except Exception as error:

            failed += 1

            logger.warning(
                "Broadcast failed %s: %s",
                user_id,
                error
            )

    await state.clear()

    await message.answer(
        "🎭 <b>Рассылка завершена!</b>\n\n"
        f"💙 Успешно: {success}\n"
        f"🫟 Ошибок: {failed}"
    )


# ============================================================
# ADMIN VIDEOS
# ============================================================

@dp.message(Command("addvideo"))
async def admin_add_video(
    message: Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    await state.set_state(
        AdminState.adding_video
    )

    await message.answer(
        "🎣 <b>Отправь видео.</b>\n\n"
        "Оно будет добавлено в базу идей."
    )


@dp.message(
    AdminState.adding_video
)
async def save_admin_video(
    message: Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):
        return

    if not message.video:

        await message.answer(
            "❌ Нужно отправить именно видео."
        )

        return

    video_id = add_video(
        message.video.file_id,
        ADMIN_ID
    )

    await state.clear()

    await message.answer(
        "🫐 <b>Видео добавлено!</b>\n\n"
        f"ID: <code>{video_id}</code>"
    )


@dp.message(Command("videos"))
async def admin_videos(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    videos = get_videos()

    if not videos:

        await message.answer(
            "🫟 В базе пока нет видео."
        )

        return

    text = (
        "🎣 <b>ВИДЕО В БАЗЕ</b>\n\n"
    )

    for video in videos:

        text += (
            f"🟦 ID: <code>{video['id']}</code>\n"
        )

    text += (
        "\n🗑 Удалить:\n"
        "<code>/deletevideo ID</code>"
    )

    await message.answer(
        text
    )


@dp.message(Command("deletevideo"))
async def admin_delete_video(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "🫟 Использование:\n"
            "<code>/deletevideo ID</code>"
        )

        return

    try:
        video_id = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    if delete_video(video_id):

        await message.answer(
            "💙 Видео удалено."
        )

    else:

        await message.answer(
            "❌ Видео не найдено."
        )


# ============================================================
# ADMIN STICKERS
# ============================================================

@dp.message(Command("addsticker"))
async def admin_add_sticker(
    message: Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    await state.set_state(
        AdminState.adding_sticker
    )

    await message.answer(
        "🫐 <b>Отправь стикер.</b>\n\n"
        "Он будет добавлен в случайную коллекцию "
        "для варианта «У меня плохое настроение»."
    )


@dp.message(
    AdminState.adding_sticker
)
async def save_admin_sticker(
    message: Message,
    state: FSMContext
):

    if not is_admin(message.from_user.id):
        return

    if not message.sticker:

        await message.answer(
            "❌ Нужно отправить именно стикер."
        )

        return

    sticker_id = add_sticker(
        message.sticker.file_id,
        ADMIN_ID
    )

    await state.clear()

    await message.answer(
        "🦋 <b>Стикер добавлен!</b>\n\n"
        f"ID: <code>{sticker_id}</code>"
    )


@dp.message(Command("stickers"))
async def admin_stickers(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    stickers = get_stickers()

    if not stickers:

        await message.answer(
            "🫟 В базе пока нет стикеров."
        )

        return

    text = (
        "🦋 <b>СТИКЕРЫ</b>\n\n"
    )

    for sticker in stickers:

        text += (
            f"🟦 ID: <code>{sticker['id']}</code>\n"
        )

    text += (
        "\n🗑 Удалить:\n"
        "<code>/deletesticker ID</code>"
    )

    await message.answer(
        text
    )


@dp.message(Command("deletesticker"))
async def admin_delete_sticker(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "🫟 Использование:\n"
            "<code>/deletesticker ID</code>"
        )

        return

    try:
        sticker_id = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    if delete_sticker(sticker_id):

        await message.answer(
            "💙 Стикер удалён."
        )

    else:

        await message.answer(
            "❌ Стикер не найден."
        )


# ============================================================
# ADMIN BAN
# ============================================================

@dp.message(Command("ban"))
async def admin_ban(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    parts = message.text.split(
        maxsplit=2
    )

    if len(parts) < 2:

        await message.answer(
            "⚖️ Использование:\n\n"
            "<code>/ban ID причина</code>"
        )

        return

    try:
        user_id = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    reason = (
        parts[2]
        if len(parts) >= 3
        else "Нарушение правил"
    )

    user = get_user(user_id)

    if not user:

        await message.answer(
            "❌ Пользователь не найден в базе."
        )

        return

    partner_id = user["partner_id"]

    if partner_id:

        disconnect_users(
            user_id,
            partner_id
        )

        try:

            await bot.send_message(
                partner_id,
                "❌ <b>Коллаборация завершена.</b>\n\n"
                "🌀 /start — найти нового собеседника"
            )

        except Exception as e:
            logger.exception("Не удалось отправить партнёру: %s", e)

    ban_user(
        user_id,
        reason
    )

    try:

        await bot.send_message(
            user_id,

            "⛔ <b>Вы получили бан в боте.</b>\n\n"
            f"Причина: <i>{html.escape(reason)}</i>"
        )

    except Exception:
        pass

    await message.answer(
        "⛔ <b>Пользователь заблокирован.</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Причина: {html.escape(reason)}"
    )


@dp.message(Command("unban"))
async def admin_unban(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Использование:\n"
            "<code>/unban ID</code>"
        )

        return

    try:
        user_id = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    user = get_user(user_id)

    if not user:

        await message.answer(
            "❌ Пользователь не найден."
        )

        return

    unban_user(
        user_id
    )

    await message.answer(
        "💙 <b>Пользователь разблокирован.</b>\n\n"
        f"ID: <code>{user_id}</code>"
    )

    try:

        await bot.send_message(
            user_id,
            "💙 <b>Ваш бан снят.</b>\n\n"
            "🌀 Можете снова пользоваться ботом."
        )

    except Exception:
        pass


@dp.message(Command("baninfo"))
async def admin_baninfo(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "<code>/baninfo ID</code>"
        )

        return

    try:
        user_id = int(parts[1])
    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    user = get_user(user_id)

    if not user:

        await message.answer(
            "❌ Пользователь не найден."
        )

        return

    status = (
        "⛔ ЗАБАНЕН"
        if user["banned"]
        else "💙 НЕ ЗАБАНЕН"
    )

    await message.answer(
        "⚖️ <b>Информация о пользователе</b>\n\n"

        f"🆔 ID: <code>{user_id}</code>\n"
        f"🎮 Roblox: "
        f"<code>{html.escape(user['roblox_username'] or 'нет')}</code>\n"
        f"👤 Telegram: "
        f"{html.escape(user['telegram_username'] or 'нет')}\n\n"

        f"Статус: {status}\n"
        f"Причина: "
        f"{html.escape(user['ban_reason'] or 'нет')}"
    )


@dp.message(Command("bans"))
async def admin_bans(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    users = db.execute(
        """
        SELECT *
        FROM users
        WHERE banned = 1
        ORDER BY banned_at DESC
        """
    ).fetchall()

    if not users:

        await message.answer(
            "💙 Заблокированных пользователей нет."
        )

        return

    text = (
        "⛔ <b>ЗАБЛОКИРОВАННЫЕ</b>\n\n"
    )

    for user in users:

        text += (
            f"🆔 <code>{user['telegram_id']}</code>\n"
            f"Причина: "
            f"{html.escape(user['ban_reason'] or 'не указана')}\n\n"
        )

    await message.answer(
        text
    )


# ============================================================
# ADMIN COMPLAINTS
# ============================================================

@dp.message(Command("complaints"))
async def admin_complaints(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    complaints = db.execute(
        """
        SELECT *
        FROM complaints
        ORDER BY id DESC
        LIMIT 30
        """
    ).fetchall()

    if not complaints:

        await message.answer(
            "💙 Жалоб пока нет."
        )

        return

    text = (
        "⚖️ <b>ПОСЛЕДНИЕ ЖАЛОБЫ</b>\n\n"
    )

    for complaint in complaints:

        text += (
            f"🆔 #{complaint['id']}\n"
            f"👤 От: <code>{complaint['complainant_id']}</code>\n"
            f"🎭 На: <code>{complaint['accused_id']}</code>\n"
            f"Тип: {html.escape(complaint['reason_type'])}\n"
            f"Статус: {html.escape(complaint['status'])}\n"
        )

        if complaint["reason_text"]:

            text += (
                f"📝 {html.escape(complaint['reason_text'][:300])}\n"
            )

        text += "\n"

    await message.answer(
        text
    )


# ============================================================
# ADMIN HELP
# ============================================================

@dp.message(Command("adminhelp"))
async def admin_help(
    message: Message
):

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ Нет доступа."
        )

        return

    await message.answer(
        "🛠 <b>АДМИН-ПАНЕЛЬ — КОМАНДЫ</b>\n\n"

        "📊 <code>/stats</code> — статистика\n\n"

        "📩 <code>/broadcast</code> — рассылка\n\n"

        "🎣 <code>/addvideo</code> — добавить видео\n"
        "🟦 <code>/videos</code> — список видео\n"
        "🗑 <code>/deletevideo ID</code> — удалить видео\n\n"

        "🦋 <code>/addsticker</code> — добавить стикер\n"
        "🟦 <code>/stickers</code> — список стикеров\n"
        "🗑 <code>/deletesticker ID</code> — удалить стикер\n\n"

        "⚖️ <code>/complaints</code> — жалобы\n\n"

        "⛔ <code>/ban ID причина</code> — бан\n"
        "💙 <code>/unban ID</code> — разбан\n"
        "🔎 <code>/baninfo ID</code> — информация\n"
        "📋 <code>/bans</code> — список банов"
    )


# ============================================================
# RELAY HEADER
# ============================================================

async def send_relay_header(
    partner_id: int,
    sender
):

    nickname = (
        sender["nickname"]
        or sender["roblox_username"]
        or "Пользователь"
    )

    description = (
        sender["description"]
        or ""
    )

    text = (
        f"🎭 <b>{html.escape(nickname)}</b>"
    )

    if description:

        text += (
            f"\n"
            f"<i>{html.escape(description)}</i>"
        )

    await bot.send_message(
        partner_id,
        text
    )


# ============================================================
# RELAY
# ============================================================

@dp.message()
async def relay_message(
    message: Message
):

    # ========================================================
    # САМОЕ ВАЖНОЕ:
    # команды никогда не пересылаем собеседнику.
    # ========================================================

    if message.text:

        stripped = message.text.strip()

        if stripped.startswith("/"):
            return

    user_id = message.from_user.id

    create_user(
        user_id,
        message.from_user.username
    )

    user = get_user(user_id)

    if not user:
        return

    if user["banned"]:

        await message.answer(
            "⛔ <b>Вы заблокированы в боте.</b>\n\n"
            f"Причина: "
            f"<i>{html.escape(user['ban_reason'] or 'не указана')}</i>"
        )

        return

    # ========================================================
    # FSM (aiogram 3.x — только через StorageKey)
    # ========================================================

    key = StorageKey(
        bot_id=bot.id,
        chat_id=message.chat.id,
        user_id=user_id
    )
    try:
        current_state = await dp.storage.get_state(key)
    except TypeError:
        # на всякий случай, если storage другой
        current_state = None

    if current_state:
        return

    partner_id = user["partner_id"]

    if not partner_id:

        if user["searching"]:

            await message.answer(
                "🔎 <b>Ты сейчас в поиске.</b>\n\n"
                "❌ Чтобы отменить его — /cancel"
            )

            return

        await message.answer(
            "🌀 У тебя сейчас нет собеседника.\n\n"
            "🎭 /start — найти креатора"
        )

        return

    partner = get_user(
        partner_id
    )

    if not partner:
        return

    if partner["banned"]:
        return

    # ========================================================
    # СОХРАНЯЕМ СООБЩЕНИЕ
    # ========================================================

    log_message(
        message,
        partner_id
    )

    # ========================================================
    # Ник + сообщение в одном сообщении
    # ========================================================

    nickname = (
        user["nickname"]
        or user["roblox_username"]
        or message.from_user.username
        or "Пользователь"
    )
    nick_prefix = f"🎭 <b>{html.escape(str(nickname))}</b>\n"

    # ========================================================
    # TEXT
    # ========================================================

    if message.text:

        await bot.send_message(
            partner_id,
            nick_prefix + html.escape(message.text)
        )

        return

    # ========================================================
    # PHOTO
    # ========================================================

    if message.photo:

        cap = nick_prefix
        if message.caption:
            cap += html.escape(message.caption)

        await bot.send_photo(
            partner_id,
            message.photo[-1].file_id,
            caption=cap
        )

        return

    # ========================================================
    # VIDEO
    # ========================================================

    if message.video:

        cap = nick_prefix
        if message.caption:
            cap += html.escape(message.caption)

        await bot.send_video(
            partner_id,
            message.video.file_id,
            caption=cap
        )

        return

    # ========================================================
    # DOCUMENT
    # ========================================================

    if message.document:

        await bot.send_document(
            partner_id,
            message.document.file_id,
            caption=message.caption
        )

        return

    # ========================================================
    # AUDIO
    # ========================================================

    if message.audio:

        await bot.send_audio(
            partner_id,
            message.audio.file_id,
            caption=message.caption
        )

        return

    # ========================================================
    # VOICE
    # ========================================================

    if message.voice:

        await bot.send_voice(
            partner_id,
            message.voice.file_id
        )

        return

    # ========================================================
    # VIDEO NOTE
    # ========================================================

    if message.video_note:

        await bot.send_video_note(
            partner_id,
            message.video_note.file_id
        )

        return

    # ========================================================
    # STICKER
    # ========================================================

    if message.sticker:

        await bot.send_sticker(
            partner_id,
            message.sticker.file_id
        )

        return

    # ========================================================
    # OTHER
    # ========================================================

    await message.answer(
        "🫟 Этот тип сообщения пока не поддерживается."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@dp.errors()
async def global_error_handler(
    event
):

    logger.exception(
        "Необработанная ошибка: %s",
        event.exception
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "======================================"
    )

    logger.info(
        "🎭 COLLAB BOT STARTED"
    )
    logger.info(
        "BUILD_TAG=features-2026-08-17"
    )

    logger.info(
        "======================================"
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(main())