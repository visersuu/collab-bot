#!/bin/bash
cd "$(dirname "$0")"
pip3 install -q aiogram httpx python-dotenv 2>/dev/null || true
echo "Запуск бота с автоперезапуском..."
exec ./watchdog.sh
