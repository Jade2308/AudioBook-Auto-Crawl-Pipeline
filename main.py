import os
import sys
import time
import logging
from typing import List, Dict

from config import (
    HISTORY_FILE,
    TEMP_DIR,
    RCLONE_REMOTE_BASE,
    RCLONE_TRANSFERS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    CRAWL_DELAY,
    SITE_BASE_URL
)
from state_tracker import StateTracker
from crawler import Crawler
from downloader import Downloader
from uploader import Uploader
from notifier import Notifier

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

class MainController:
    """
    Module 6: Main Controller (Điều phối & Kịch bản chính)
    Ghép nối 5 module độc lập theo quy trình tuyến tính tự động.
    """
    def __init__(self):
        logging.info("==================================================")
        logging.info("   AUDIOBOOK AUTO CRAWL PIPELINE STARTING...      ")
        logging.info("==================================================")

        # 1. Khởi tạo các Modules
        self.state_tracker = StateTracker(history_file=HISTORY_FILE)
        self.crawler = Crawler(base_url=SITE_BASE_URL)
        self.downloader = Downloader(temp_dir=TEMP_DIR)
        self.uploader = Uploader(remote_base=RCLONE_REMOTE_BASE, transfers=RCLONE_TRANSFERS)
        self.notifier = Notifier(bot_token=TELEGRAM_BOT_TOKEN, chat_id=TELEGRAM_CHAT_ID)

    def run(self):
        """
        Thực thi quy trình tự động tuyến tính chính:
        Step 1: Quét danh sách link mới từ Crawler.
        Step 2: Nếu rỗng -> Gửi thông báo Telegram -> Thoát.
        Step 3: Duyệt vòng lặp: Downloader -> Uploader -> Save State -> Sleep.
        Step 4: Tổng hợp kết quả và báo cáo Telegram.
        """
        # Step 1: Lấy danh sách link mới (đã lọc trùng bằng State Tracker)
        try:
            new_episodes: List[Dict[str, str]] = self.crawler.fetch_new_episodes(state_tracker=self.state_tracker)
        except Exception as err:
            logging.error(f"[MainController] Lỗi khi quét dữ liệu website: {err}")
            self.notifier.notify_failure(f"Lỗi khi cào web: {err}")
            return

        # Step 2: Nếu không có tập mới nào
        if not new_episodes:
            logging.info("[MainController] Không tìm thấy tập truyện mới nào hôm nay.")
            self.notifier.notify_empty()
            logging.info("[MainController] Kết thúc tiến trình thành công (Empty).")
            return

        logging.info(f"[MainController] Tìm thấy {len(new_episodes)} tập mới cần xử lý.")

        succeeded_items = []
        failed_items = []

        # Step 3: Chạy vòng lặp qua từng tập truyện mới
        for idx, item in enumerate(new_episodes, 1):
            story_title = item.get("story_title", "Unknown Story")
            episode_title = item.get("episode_title", "Unknown Episode")
            unique_id = item.get("unique_id", item.get("audio_url"))

            logging.info(f"\n---> [{idx}/{len(new_episodes)}] Đang xử lý: {story_title} - {episode_title}")

            try:
                # 3a. Downloader: Tải file audio và xuất dạng M4A
                m4a_path = self.downloader.download_episode(item)

                # 3b. Uploader: Đẩy file M4A lên Google Drive và tự động xóa file cục bộ
                upload_success = self.uploader.upload_and_cleanup(
                    file_path=m4a_path,
                    story_title=story_title
                )

                if not upload_success:
                    raise RuntimeError(f"Tải file thành công nhưng upload rclone move thất bại cho '{m4a_path.name}'")

                # 3c. State Tracker: Ghi nhận lịch sử đã tải xong
                self.state_tracker.save_downloaded(unique_id)
                succeeded_items.append(item)
                logging.info(f"===> [{idx}/{len(new_episodes)}] Hoàn tất thành công: {story_title} - {episode_title}")

            except Exception as ep_error:
                logging.error(f"!!! [{idx}/{len(new_episodes)}] Lỗi xử lý tập '{story_title} - {episode_title}': {ep_error}")
                failed_items.append({
                    "story_title": story_title,
                    "episode_title": episode_title,
                    "error": str(ep_error)
                })

            # 3d. Tránh bị web chặn IP: Nghỉ CRAWL_DELAY giây giữa các lượt tải
            if idx < len(new_episodes):
                logging.info(f"[MainController] Tạm dừng {CRAWL_DELAY} giây trước khi tải tập tiếp theo...")
                time.sleep(CRAWL_DELAY)

        # Step 4: Tổng hợp kết quả & gửi thông báo Telegram
        logging.info("\n==================================================")
        logging.info(f"   BÁO CÁO TỔNG KẾT: Thành công={len(succeeded_items)}, Lỗi={len(failed_items)}")
        logging.info("==================================================")

        if succeeded_items:
            self.notifier.notify_success(succeeded_items, failed_count=len(failed_items))
        elif failed_items:
            self.notifier.notify_failure("Tất cả các tập trong danh sách tải đều gặp lỗi.", failed_items=failed_items)


def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        
    controller = MainController()
    controller.run()


if __name__ == "__main__":
    main()
