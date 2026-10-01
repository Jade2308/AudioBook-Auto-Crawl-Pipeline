import os
import sys
import time
import logging
import queue
import threading
from typing import List, Dict

from config import (
    HISTORY_FILE,
    TEMP_DIR,
    RCLONE_REMOTE_BASE,
    RCLONE_TRANSFERS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    CRAWL_DELAY,
    SITE_BASE_URL,
    MAX_CONCURRENT_TEMP_FILES,
    LOGS_DIR,
)
from state_tracker import StateTracker
from crawler import Crawler
from downloader import Downloader
from uploader import Uploader
from notifier import Notifier

# Configure Logging: vừa in ra console, vừa ghi file để Telegram Bot có thể đọc bất kỳ lúc nào
latest_log_path = LOGS_DIR / "pipeline_latest.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(latest_log_path, encoding="utf-8", mode="w")
    ]
)

class MainController:
    """
    Module 6: Main Controller (Điều phối & Kịch bản chính)
    Ghép nối 5 module theo mô hình Producer-Consumer (Đa luồng):
    - Downloader Producer: Tải & Convert M4A song song.
    - Uploader Consumer: Đẩy file M4A lên Google Drive & dọn dẹp.
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
        Thực thi quy trình tự động bất đồng bộ / song song:
        Step 1: Quét danh sách link mới từ Crawler.
        Step 2: Nếu rỗng -> Gửi thông báo Telegram -> Thoát.
        Step 3: Chạy song song: Downloader Producer -> Queue -> Uploader Consumer.
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
        logging.info(f"[MainController] Khởi tạo mô hình Producer-Consumer (Max queue size: {MAX_CONCURRENT_TEMP_FILES}).")

        succeeded_items = []
        failed_items = []
        total_items = len(new_episodes)
        results_lock = threading.Lock()

        # Hàng đợi trung gian chứa các file đã download xong chờ upload
        upload_queue: queue.Queue = queue.Queue(maxsize=MAX_CONCURRENT_TEMP_FILES)

        # --- Producer Thread: Downloader ---
        def downloader_producer():
            for idx, item in enumerate(new_episodes, 1):
                story_title = item.get("story_title", "Unknown Story")
                episode_title = item.get("episode_title", "Unknown Episode")
                overall_percent = (idx / total_items) * 100

                print("\n" + "=" * 80)
                logging.info(f"📊 TIẾN TRÌNH TỔNG THỂ: Tập [{idx}/{total_items}] ({overall_percent:.2f}%)")
                logging.info(f"📖 Bộ truyện : {story_title}")
                logging.info(f"🎧 Tập/Chương: {episode_title}")
                print("=" * 80)

                try:
                    logging.info(f"⬇️ [PRODUCER] Bắt đầu tải audio về VPS (Downloader)...")
                    m4a_path = self.downloader.download_episode(item)

                    # Đưa vào queue để Uploader xử lý (nếu queue đầy sẽ tạm dừng chờ Uploader dọn bớt file)
                    logging.info(f"📥 [PRODUCER] Tải xong '{m4a_path.name}'. Đẩy vào Queue chờ Upload...")
                    upload_queue.put({
                        "item": item,
                        "m4a_path": m4a_path,
                        "idx": idx,
                        "total_items": total_items
                    })

                except Exception as ep_error:
                    logging.error(f"❌ [LỖI DOWNLOAD {idx}/{total_items}] Thất bại tại tập '{story_title} - {episode_title}': {ep_error}")
                    with results_lock:
                        failed_items.append({
                            "story_title": story_title,
                            "episode_title": episode_title,
                            "error": f"Lỗi download: {ep_error}"
                        })

                # Tránh bị web chặn IP: Nghỉ CRAWL_DELAY giây giữa các lượt tải
                if idx < total_items:
                    logging.info(f"⏳ Tạm dừng {CRAWL_DELAY}s trước khi sang tập tiếp theo...")
                    time.sleep(CRAWL_DELAY)

            # Báo hiệu cho Uploader Consumer dừng lại khi đã tải hết
            upload_queue.put(None)
            logging.info("🏁 [PRODUCER] Đã hoàn thành tải toàn bộ danh sách tập.")

        # --- Consumer Thread: Uploader ---
        def uploader_consumer():
            while True:
                task = upload_queue.get()
                if task is None:
                    upload_queue.task_done()
                    break

                item = task["item"]
                m4a_path = task["m4a_path"]
                idx = task["idx"]
                total = task["total_items"]

                story_title = item.get("story_title", "Unknown Story")
                episode_title = item.get("episode_title", "Unknown Episode")
                unique_id = item.get("unique_id", item.get("audio_url"))

                try:
                    logging.info(f"⬆️ [CONSUMER {idx}/{total}] Đang đẩy file lên Google Drive: {m4a_path.name}")
                    upload_success = self.uploader.upload_and_cleanup(
                        file_path=m4a_path,
                        story_title=story_title
                    )

                    if not upload_success:
                        raise RuntimeError(f"Upload rclone move thất bại cho '{m4a_path.name}'")

                    # Ghi nhận lịch sử đã tải & upload thành công
                    self.state_tracker.save_downloaded(unique_id)
                    with results_lock:
                        succeeded_items.append(item)

                    logging.info(f"✅ [THÀNH CÔNG {idx}/{total}] Đã upload và ghi history.txt: {story_title} - {episode_title}")

                    # 🔔 Thông báo Telegram: mỗi tập thành công
                    self.notifier.notify_episode_success(
                        story_title=story_title,
                        episode_title=episode_title,
                        idx=idx,
                        total=total
                    )

                except Exception as up_error:
                    logging.error(f"❌ [LỖI UPLOAD {idx}/{total}] Thất bại tại tập '{story_title} - {episode_title}': {up_error}")
                    with results_lock:
                        failed_items.append({
                            "story_title": story_title,
                            "episode_title": episode_title,
                            "error": f"Lỗi upload: {up_error}"
                        })
                    # 🔔 Thông báo Telegram: upload lỗi ngay lập tức
                    self.notifier.notify_episode_failure(
                        story_title=story_title,
                        episode_title=episode_title,
                        idx=idx,
                        total=total,
                        error=str(up_error)
                    )

                finally:
                    upload_queue.task_done()

            logging.info("🏁 [CONSUMER] Đã hoàn thành upload toàn bộ các tập trong Queue.")

        # Step 3: Tạo và kích hoạt các luồng
        producer_thread = threading.Thread(target=downloader_producer, name="DownloaderProducer")
        consumer_thread = threading.Thread(target=uploader_consumer, name="UploaderConsumer")

        producer_thread.start()
        consumer_thread.start()

        # Chờ cả 2 luồng hoàn thành tác vụ
        producer_thread.join()
        consumer_thread.join()


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
