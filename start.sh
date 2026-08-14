#!/bin/bash
set -e

if [ ! -d ".venv" ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

if [ ! -f ".env" ]; then
    echo "Файл .env не найден!"
    echo "Скопируйте .env.example в .env и заполните BOT_TOKEN и ADMIN_ID"
    exit 1
fi

echo "Запускаю Collab Bot..."
python bot.py
