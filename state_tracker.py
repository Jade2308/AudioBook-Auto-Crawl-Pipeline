import os
from pathlib import Path
from typing import Set
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class StateTracker:
    """
    Module 1: State Tracker (Bộ nhớ trạng thái)
    Quản lý danh sách các URL/tập truyện đã tải thành công vào file history.txt
    nhằm đảm bảo không bị tải trùng lặp.
    """
    def __init__(self, history_file: str | Path = "history.txt"):
        self.history_file = Path(history_file)
        self.downloaded_urls: Set[str] = set()
        self.load_history()

    def load_history(self) -> Set[str]:
        """Đọc danh sách các URL đã tải từ file history.txt."""
        if not self.history_file.exists():
            # Tạo file rỗng nếu chưa tồn tại
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            self.history_file.touch()
            logging.info(f"[StateTracker] Đã tạo file lịch sử mới: {self.history_file}")

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                self.downloaded_urls = {line.strip() for line in f if line.strip()}
            logging.info(f"[StateTracker] Đã tải {len(self.downloaded_urls)} bản ghi từ {self.history_file}")
        except Exception as e:
            logging.error(f"[StateTracker] Lỗi khi đọc file lịch sử: {e}")
            self.downloaded_urls = set()

        return self.downloaded_urls

    def check_downloaded(self, url: str) -> bool:
        """
        Kiểm tra xem URL đã được tải thành công trước đó hay chưa.
        Returns:
            True nếu đã tải, False nếu chưa tải.
        """
        clean_url = url.strip()
        is_downloaded = clean_url in self.downloaded_urls
        if is_downloaded:
            logging.debug(f"[StateTracker] Đã tồn tại trong lịch sử: {clean_url}")
        return is_downloaded

    def save_downloaded(self, url: str) -> None:
        """
        Ghi URL của tập truyện đã tải thành công vào history.txt và lưu vào RAM memory.
        """
        clean_url = url.strip()
        if clean_url and clean_url not in self.downloaded_urls:
            self.downloaded_urls.add(clean_url)
            try:
                with open(self.history_file, "a", encoding="utf-8") as f:
                    f.write(f"{clean_url}\n")
                logging.info(f"[StateTracker] Đã lưu lịch sử: {clean_url}")
            except Exception as e:
                logging.error(f"[StateTracker] Lỗi khi ghi lịch sử vào {self.history_file}: {e}")


if __name__ == "__main__":
    # Test Module 1
    tracker = StateTracker("test_history.txt")
    test_url = "https://metruyenaudio.online/truyen/test-story/nghe/1"
    print("Check 1 (False expected):", tracker.check_downloaded(test_url))
    tracker.save_downloaded(test_url)
    print("Check 2 (True expected):", tracker.check_downloaded(test_url))
    if os.path.exists("test_history.txt"):
        os.remove("test_history.txt")
