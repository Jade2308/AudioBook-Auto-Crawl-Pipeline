import os
import sys
import time
import glob
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Tuple

from config import (
    TEMP_DIR,
    SESSION_COOKIE,
    USER_AGENT,
    MIN_AUDIO_FILE_SIZE_MB,
    MIN_AUDIO_FILE_SIZE_BYTES,
)


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
        self.cleanup_stale_temp_files()

    def cleanup_stale_temp_files(self) -> int:
        """Dọn dẹp các file rác dở dang (*.tmp, *.part, *.ytdl) nếu phiên trước bị gián đoạn đột ngột."""
        stale_patterns = ["*.tmp", "*.part", "*.ytdl", "raw_temp_*"]
        cleaned_count = 0
        for pattern in stale_patterns:
            for file_path in self.temp_dir.glob(pattern):
                try:
                    if file_path.is_file():
                        file_path.unlink()
                        cleaned_count += 1
                        logging.info(f"[Downloader] Đã dọn file tạm sót lại: {file_path.name}")
                except Exception as e:
                    logging.warning(f"[Downloader] Không thể xóa file tạm {file_path.name}: {e}")
        return cleaned_count

    def validate_downloaded_audio(self, file_path: Path) -> Tuple[bool, str]:
        """
        Kiểm tra tính toàn vẹn và dung lượng tối thiểu của file audio vừa tải về.
        Returns:
            (is_valid, error_reason)
        """
        if not file_path.exists():
            return False, f"File không tồn tại: {file_path}"

        size = file_path.stat().st_size
        size_mb = size / (1024 * 1024)

        if size == 0:
            return False, "File rỗng (0 bytes)"

        if size < MIN_AUDIO_FILE_SIZE_BYTES:
            return False, (
                f"Dung lượng file quá nhỏ ({size_mb:.2f} MB), "
                f"không đạt ngưỡng tối thiểu {MIN_AUDIO_FILE_SIZE_MB:.1f} MB (nghi ngờ đứt mạng hoặc tải dở)"
            )

        # Kiểm tra tính toàn vẹn container M4A qua ffprobe (nếu có sẵn ffprobe)
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path)
            ]
            res = subprocess.run(
                probe_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
            if res.returncode != 0:
                err_text = res.stderr.strip() or "lỗi container audio / thiếu moov atom"
                return False, f"ffprobe phát hiện file audio hỏng: {err_text}"

            duration_str = res.stdout.strip()
            if duration_str:
                duration_sec = float(duration_str)
                if duration_sec <= 0:
                    return False, "Thời lượng audio không hợp lệ (<= 0 giây)"
                logging.info(
                    f"[Downloader] ffprobe kiểm tra OK: thời lượng {duration_sec/3600:.2f} giờ "
                    f"({duration_sec/60:.1f} phút)"
                )
        except FileNotFoundError:
            # ffprobe không cài hoặc không có trong PATH, bỏ qua kiểm tra sâu thời lượng
            pass
        except Exception as ex:
            logging.warning(f"[Downloader] Không thể kiểm tra thời lượng qua ffprobe: {ex}")

        return True, ""

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
            "--user-agent", USER_AGENT,
            "--no-progress",
            "--add-header", "Sec-Fetch-Dest:audio",
            "--add-header", "Sec-Fetch-Mode:no-cors",
            "--add-header", "Sec-Fetch-Site:same-origin",
            "--add-header", "Range:bytes=0-"
        ]

        if episode_item.get("episode_url"):
            cmd.extend(["--referer", episode_item["episode_url"]])

        if self.session_cookie:
            cmd.extend(["--add-header", f"Cookie:{self.session_cookie}"])

        cookie_file = self.temp_dir.parent / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            cmd.extend(["--cookies", str(cookie_file)])

        cmd.append(audio_url)


        logging.info(f"[Downloader] Đang thực thi lệnh subprocess: {' '.join(cmd)}")
        logging.info("🔄 [CHUYỂN ĐỔI AUDIO] yt-dlp đang tải & ép kiểu định dạng sang .M4A bằng ffmpeg...")

        try:
            # Chạy yt-dlp trực tiếp xuất log thời gian thực (phần trăm %, tốc độ, ETA) ra màn hình
            result = subprocess.run(
                cmd,
                check=True
            )
            logging.info("[Downloader] yt-dlp và ffmpeg đã hoàn thành chuyển đổi M4A thành công.")
        except subprocess.CalledProcessError as e:
            err_msg = f"yt-dlp mã lỗi {e.returncode}"
            logging.warning(f"[Downloader] yt-dlp gặp lỗi ({err_msg}). Thử tải và chuyển đổi M4A trực tiếp bằng HTTP Requests + ffmpeg fallback...")
            
            # Fallback: Tải trực tiếp bằng requests + chuyển đổi M4A bằng ffmpeg
            try:
                import requests
                req_headers = {
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                    "Accept-Encoding": "identity;q=1, *;q=0",
                    "Range": "bytes=0-",
                    "Sec-Fetch-Dest": "audio",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "same-origin"
                }
                if episode_item.get("episode_url"):
                    req_headers["Referer"] = episode_item["episode_url"]
                if self.session_cookie:
                    req_headers["Cookie"] = self.session_cookie
                
                resp = requests.get(audio_url, headers=req_headers, stream=True, timeout=30)
                resp.raise_for_status()
                
                total_size = int(resp.headers.get('content-length', 0))
                downloaded_bytes = 0
                last_logged_percent = -10
                
                raw_temp_file = self.temp_dir / f"raw_temp_{int(time.time())}.tmp"
                target_m4a = self.temp_dir / out_filename_template.replace("%(ext)s", "m4a")
                
                with open(raw_temp_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=131072):
                        if chunk:
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            if total_size > 0:
                                percent = (downloaded_bytes / total_size) * 100
                                sys.stdout.write(f"\r[Downloader Requests] Đã tải: {downloaded_bytes / (1024*1024):.2f} MB / {total_size / (1024*1024):.2f} MB ({percent:.1f}%)")
                                sys.stdout.flush()
                print() # Xuống dòng sau khi tải xong


                # Gọi ffmpeg để ép kiểu sang M4A chuẩn
                logging.info(f"🔄 [CHUYỂN ĐỔI AUDIO] Đang gọi ffmpeg chuyển đổi file sang định dạng .m4a...")
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(raw_temp_file),
                    "-c:a", "aac",
                    "-b:a", "128k",
                    str(target_m4a)
                ]
                subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
                
                if raw_temp_file.exists():
                    os.remove(raw_temp_file)

                logging.info(f"[Downloader] Tải & chuyển đổi M4A thành công: {target_m4a}")
            except Exception as req_err:
                if raw_temp_file and raw_temp_file.exists():
                    os.remove(raw_temp_file)
                logging.error(f"[Downloader] Lỗi subprocess yt-dlp: {err_msg}")
                logging.error(f"[Downloader] Lỗi HTTP requests/ffmpeg fallback: {req_err}")
                raise RuntimeError(f"Tải thất bại qua yt-dlp ({err_msg}) và Requests ({req_err})") from req_err





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

        # Kiểm tra tính toàn vẹn và dung lượng tối thiểu
        is_valid, reason = self.validate_downloaded_audio(downloaded_file)
        if not is_valid:
            try:
                if downloaded_file.exists():
                    downloaded_file.unlink()
                    logging.warning(f"[Downloader] Đã xóa file hỏng vừa tải: {downloaded_file.name}")
            except Exception as del_err:
                logging.warning(f"[Downloader] Không thể xóa file hỏng {downloaded_file.name}: {del_err}")
            raise RuntimeError(f"Tập tải về không hợp lệ: {reason}")

        size_mb = downloaded_file.stat().st_size / (1024 * 1024)
        logging.info(f"[Downloader] File audio M4A hoàn chỉnh & đạt chuẩn ({size_mb:.2f} MB): {downloaded_file}")
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
