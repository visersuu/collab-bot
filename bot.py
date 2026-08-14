"""
Главный файл для запуска Collab Bot.

Просто запустите:
    python bot.py
"""

import asyncio
import sys
from pathlib import Path

# Добавляем src в путь, чтобы импорты работали
sys.path.insert(0, str(Path(__file__).parent / "src"))

from python_project.bot import main

if __name__ == "__main__":
    asyncio.run(main())
