import os
import sys
import logging
import subprocess
from pathlib import Path

from config import RCLONE_REMOTE_BASE, RCLONE_TRANSFERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class Uploader:
    """
    Module 4: Uploader (Lưu trữ Drive & Dọn dẹp)
    Nhận đường dẫn file M4A cục bộ và Tên bộ truyện, gọi rclone move qua subprocess
    để đẩy file lên Google Drive vào đúng folder bộ truyện, đồng thời tự động xóa file
    cục bộ giải phóng dung lượng ổ cứng VPS.
    """
    def __init__(self, remote_base: str = RCLONE_REMOTE_BASE, transfers: int = RCLONE_TRANSFERS):
        # remote_base ví dụ: "gdrive:Audiobooks" hoặc "gdrive:/Audiobooks"
        self.remote_base = remote_base.rstrip("/")
        self.transfers = transfers

    def upload_and_cleanup(self, file_path: str | Path, story_title: str) -> bool:
        """
        Di chuyển (move) file audio M4A lên Google Drive.
        Args:
            file_path: Đường dẫn tới file .m4a cần đẩy lên.
            story_title: Tên bộ truyện (được dùng làm tên folder trên Google Drive).
        Returns:
            True nếu rclone move thành công, False nếu thất bại.
        """
        path_obj = Path(file_path)
        if not path_obj.exists():
            logging.error(f"[Uploader] File không tồn tại để upload: {file_path}")
            return False

        # Làm sạch tên bộ truyện để làm tên folder trên Drive
        safe_story_folder = "".join(c for c in story_title if c.isalnum() or c in (" ", "-", "_", ".")).strip()
        if not safe_story_folder:
            safe_story_folder = "Audiobooks_Unsorted"

        # Đường dẫn đích rclone: e.g. "gdrive:Audiobooks/DIỄN VAI TỘI PHẠM..."
        remote_target_dir = f"{self.remote_base}/{safe_story_folder}"

        # Lệnh rclone move chính xác theo yêu cầu dự án:
        # rclone move [TEMP_DIR/file] gdrive:/[BASE_DIR]/[Tên_Bộ_Truyện]/ --transfers 4
        cmd = [
            "rclone", "move",
            str(path_obj),
            f"{remote_target_dir}/",
            "--transfers", str(self.transfers),
            "--drive-chunk-size", "128M",
            "--tpslimit", "10",
            "--timeout", "5m",
            "--contimeout", "1m",
            "--low-level-retries", "10",
            "-P",
            "-v",
            "--stats", "10s",
            "--stats-one-line"
        ]

        logging.info(f"[Uploader] Bắt đầu đẩy file '{path_obj.name}' lên Remote: {remote_target_dir}")
        logging.info(f"[Uploader] Đang thực thi lệnh subprocess: {' '.join(cmd)}")

        try:
            # Chạy rclone move trực tiếp xuất thanh tiến trình phần trăm % real-time
            result = subprocess.run(
                cmd,
                check=True
            )
            logging.info(f"[Uploader] rclone move hoàn tất thành công cho '{path_obj.name}'.")

            
            # Kiểm tra xem rclone move đã xóa file chưa (nếu rclone move chưa xóa do cấu hình, chủ động xóa)
            if path_obj.exists():
                try:
                    os.remove(path_obj)
                    logging.info(f"[Uploader] Đã chủ động dọn dẹp xóa file rác cục bộ: {path_obj}")
                except Exception as del_err:
                    logging.warning(f"[Uploader] Không thể xóa file cục bộ sau upload: {del_err}")

            return True

        except subprocess.CalledProcessError as e:
            err_msg = e.stderr or e.stdout or str(e)
            logging.error(f"[Uploader] Lỗi subprocess rclone move: {err_msg}")
            return False
        except Exception as ex:
            logging.error(f"[Uploader] Lỗi không xác định khi upload: {ex}")
            return False


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        
    uploader = Uploader()
    print(f"[Module 4 Uploader] Đã khởi tạo với remote_base={uploader.remote_base}")
