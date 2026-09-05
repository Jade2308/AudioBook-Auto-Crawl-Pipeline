import os
from pathlib import Path

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Base workspace directory
BASE_DIR = Path(__file__).resolve().parent

# Crawler Configs
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "https://metruyenaudio.online")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# VIP Account Cookie Authentication (Trích xuất từ F12 Trình duyệt)
SESSION_COOKIE = os.getenv("SESSION_COOKIE", "")

# Download & Temp Directory Configs
TEMP_DIR = Path(os.getenv("TEMP_DIR", BASE_DIR / "temp_downloads"))
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", BASE_DIR / "history.txt"))

# Rclone Remote Configs (e.g. 'gdrive:Audiobooks')
RCLONE_REMOTE_BASE = os.getenv("RCLONE_REMOTE_BASE", "gdrive:Audiobooks")
RCLONE_TRANSFERS = int(os.getenv("RCLONE_TRANSFERS", "4"))

# Telegram Bot Configs
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Operational Configs
CRAWL_DELAY = int(os.getenv("CRAWL_DELAY", "3"))  # Seconds delay between downloads
MAX_STORIES_TO_CRAWL = int(os.getenv("MAX_STORIES_TO_CRAWL", "0"))  # 0 = không giới hạn số truyện
MAX_PAGES_TO_CRAWL = int(os.getenv("MAX_PAGES_TO_CRAWL", "0"))      # 0 = quét tất cả các trang cho tới trang cuối
