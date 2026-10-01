"""
run_bot.py — Entry Point cho Telegram Bot Controller

Cách dùng:
    python run_bot.py

Bot sẽ chạy liên tục, lắng nghe lệnh từ Telegram.
Nhấn Ctrl+C để dừng.
"""

import sys
import logging

from telegram_bot import TelegramBotController

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    bot = TelegramBotController()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[run_bot] Bot đã dừng bởi người dùng.")

