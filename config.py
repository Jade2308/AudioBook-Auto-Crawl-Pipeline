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

# Download, Logs & Temp Directory Configs
TEMP_DIR = Path(os.getenv("TEMP_DIR", BASE_DIR / "temp_downloads"))
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", BASE_DIR / "history.txt"))
LOGS_DIR = Path(os.getenv("LOGS_DIR", BASE_DIR / "logs"))
LOGS_DIR.mkdir(parents=True, exist_ok=True)
PIPELINE_STATE_FILE = Path(os.getenv("PIPELINE_STATE_FILE", BASE_DIR / "pipeline_state.json"))
SCHEDULE_CONFIG_FILE = Path(os.getenv("SCHEDULE_CONFIG_FILE", BASE_DIR / "schedule_config.json"))
STORY_MAPPINGS_FILE = Path(os.getenv("STORY_MAPPINGS_FILE", BASE_DIR / "story_mappings.json"))



# Rclone Remote Configs (e.g. 'gdrive:Audiobooks')
RCLONE_REMOTE_BASE = os.getenv("RCLONE_REMOTE_BASE", "gdrive:Audiobooks")
RCLONE_TRANSFERS = int(os.getenv("RCLONE_TRANSFERS", "4"))

# Telegram Bot Configs
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Danh sách chat_id được phép dùng bot (comma-separated). Mặc định dùng TELEGRAM_CHAT_ID.
TELEGRAM_ALLOWED_CHAT_IDS = [
    cid.strip()
    for cid in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", TELEGRAM_CHAT_ID).split(",")
    if cid.strip()
]

# Operational Configs
CRAWL_DELAY = int(os.getenv("CRAWL_DELAY") or "3")
MAX_STORIES_TO_CRAWL = int(os.getenv("MAX_STORIES_TO_CRAWL") or "0")
MAX_PAGES_TO_CRAWL = int(os.getenv("MAX_PAGES_TO_CRAWL") or "0")
MAX_CONCURRENT_TEMP_FILES = int(os.getenv("MAX_CONCURRENT_TEMP_FILES") or "2")
AUTO_RESUME_ON_REBOOT = (os.getenv("AUTO_RESUME_ON_REBOOT") or "true").lower() in ("true", "1", "yes")
NOTIFY_ON_BOT_STARTUP = (os.getenv("NOTIFY_ON_BOT_STARTUP") or "true").lower() in ("true", "1", "yes")

# Audio Integrity & Validation Configs (Dành cho file truyện dài ~4 tiếng)
# Ngưỡng dung lượng tối thiểu của 1 tập audio hợp lệ (MB)
MIN_AUDIO_FILE_SIZE_MB = float(os.getenv("MIN_AUDIO_FILE_SIZE_MB") or "20")
MIN_AUDIO_FILE_SIZE_BYTES = int(MIN_AUDIO_FILE_SIZE_MB * 1024 * 1024)



