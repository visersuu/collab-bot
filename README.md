# Collab Bot

Telegram-бот для поиска коллабораций (креаторы, Roblox и т.д.).

Бот помогает пользователям:
- создать профиль (ник, описание, Roblox-аккаунт, навык)
- искать партнёра для коллаба
- общаться (включая анонимный режим)
- проводить видео-сессии
- оставлять оценки и жалобы

Администратор может банить пользователей, добавлять видео/стикеры и управлять системой.

## Требования

- Python 3.12+
- Telegram Bot Token (получить у [@BotFather](https://t.me/BotFather))

## Быстрый старт

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/visersuu/collab-bot.git
   cd collab-bot
   ```

2. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Linux/macOS
   # или .venv\Scripts\activate  на Windows
   pip install -r requirements.txt
   ```

3. Создайте файл `.env` (или экспортируйте переменные):
   ```bash
   cp .env.example .env
   ```
   Заполните:
   - `BOT_TOKEN` — токен бота от BotFather
   - `ADMIN_ID` — ваш Telegram ID (можно узнать у @userinfobot)

4. Запустите бота:
   ```bash
   python -m python_project.bot
   ```
   или
   ```bash
   python -c "from python_project.bot import main; import asyncio; asyncio.run(main())"
   ```

После запуска бот начнёт отвечать на `/start`.

## Структура

```
.
├── src/python_project/
│   ├── bot.py          # основной код бота
│   ├── cli.py
│   ├── __init__.py
│   └── __main__.py
├── tests/
├── pyproject.toml
├── requirements.txt
├── .env.example
└── README.md
```

## Переменные окружения

| Переменная   | Описание                          | Обязательно |
|--------------|-----------------------------------|-------------|
| `BOT_TOKEN`  | Токен Telegram-бота               | Да          |
| `ADMIN_ID`   | Telegram ID администратора        | Да          |

## База данных

Бот использует SQLite (`collab_bot.db`). Файл создаётся автоматически при первом запуске.

## Замечания

- Не коммитьте файл `.env` и базу данных.
- Токен бота держите в секрете.
- При необходимости можно добавить `python-dotenv` для удобной загрузки `.env`.
