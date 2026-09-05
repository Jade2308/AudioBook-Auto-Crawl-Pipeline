import os
import sys
import glob
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict

from config import TEMP_DIR, SESSION_COOKIE, USER_AGENT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class Downloader:
    """
    Module 3: Downloader (Tải & Xử lý Audio)
    Nhận URL audio stream, gọi yt-dlp và ffmpeg thông qua subprocess để tải
    và ép kiểu âm thanh sang định dạng .m4a vào thư mục tạm (TEMP_DIR).
    Hỗ trợ gửi Cookie tài khoản VIP nếu được cấu hình.
    """
    def __init__(self, temp_dir: str | Path = TEMP_DIR, session_cookie: str = SESSION_COOKIE):
        self.temp_dir = Path(temp_dir)
        self.session_cookie = session_cookie.strip()
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def download_episode(self, episode_item: Dict[str, str], custom_filename: Optional[str] = None) -> Path:
        """
        Tải 1 tập truyện và chuyển đổi sang M4A.
        Args:
            episode_item: Dict chứa 'audio_url', 'story_title', 'episode_title', v.v.
            custom_filename: Tên file tùy chỉnh (không cần đuôi .m4a)
        Returns:
            Path tới file .m4a vừa được tạo ra trong thư mục tạm.
        Raises:
            RuntimeError nếu quá trình tải thất bại hoặc không tìm thấy file xuất ra.
        """
        audio_url = episode_item.get("audio_url") or episode_item.get("episode_url")
        story_title = episode_item.get("story_title", "UnknownStory")
        episode_title = episode_item.get("episode_title", "UnknownEpisode")

        if not audio_url:
            raise ValueError("[Downloader] Không tìm thấy URL hợp lệ để tải.")

        # Tạo tên file sạch nếu có custom_filename hoặc tự động tạo theo TênBộTruyện - TênTập
        if not custom_filename:
            safe_story = "".join(c for c in story_title if c.isalnum() or c in (" ", "-", "_")).strip()
            safe_episode = "".join(c for c in episode_title if c.isalnum() or c in (" ", "-", "_")).strip()
            out_filename_template = f"{safe_story} - {safe_episode}.%(ext)s"
        else:
            safe_name = "".join(c for c in custom_filename if c.isalnum() or c in (" ", "-", "_")).strip()
            out_filename_template = f"{safe_name}.%(ext)s"

        logging.info(f"[Downloader] Bắt đầu tải tập: '{story_title} - {episode_title}' từ URL: {audio_url}")

        # Lệnh subprocess gọi yt-dlp chính xác theo yêu cầu dự án
        # yt-dlp -x --audio-format m4a --paths [TEMP_DIR] -o "%(title)s.%(ext)s" [URL]
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format", "m4a",
            "--paths", str(self.temp_dir),
            "-o", out_filename_template,
            "--user-agent", USER_AGENT
        ]

        if self.session_cookie:
            cmd.extend(["--add-header", f"Cookie:{self.session_cookie}"])

        cmd.append(audio_url)

        logging.info(f"[Downloader] Đang thực thi lệnh subprocess: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logging.info("[Downloader] yt-dlp đã hoàn thành tải xuống.")
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr or e.stdout or str(e)
            logging.error(f"[Downloader] Lỗi subprocess khi chạy yt-dlp: {err_msg}")
            raise RuntimeError(f"Tải thất bại qua yt-dlp: {err_msg}") from e

        # Tìm file .m4a vừa được tạo trong TEMP_DIR
        # Ưu tiên tìm file match với pattern tên file vừa tạo
        expected_stem = out_filename_template.rsplit(".", 1)[0]
        matching_files = list(self.temp_dir.glob(f"{expected_stem}*.m4a"))
        
        if not matching_files:
            # Fallback tìm file .m4a mới nhất được tạo trong temp_dir
            all_m4a_files = sorted(self.temp_dir.glob("*.m4a"), key=lambda p: p.stat().st_mtime, reverse=True)
            if all_m4a_files:
                matching_files = [all_m4a_files[0]]

        if not matching_files:
            raise RuntimeError(f"[Downloader] Thao tác thành công nhưng không tìm thấy file .m4a trong {self.temp_dir}")

        downloaded_file = matching_files[0]
        logging.info(f"[Downloader] File audio M4A hoàn chỉnh: {downloaded_file}")
        return downloaded_file


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        
    dl = Downloader()
    sample_item = {
        "story_title": "Test Story",
        "episode_title": "Tap 1",
        "audio_url": "https://metruyenaudio.online/api/audio/cmtfmsoji2e1aictyam6jopk3"
    }
    try:
        file_path = dl.download_episode(sample_item)
        print(f"Đã tải thành công file: {file_path}")
    except Exception as err:
        print(f"Lỗi tải thử nghiệm: {err}")
