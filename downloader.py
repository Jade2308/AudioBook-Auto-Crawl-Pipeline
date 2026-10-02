import os
import sys
import time
import glob
import re
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Tuple
from http.cookiejar import MozillaCookieJar
import requests

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
    Tải audio stream đa phân đoạn (HTTP 206 Multi-Range Streaming) trực tiếp từ máy chủ,
    vượt qua cơ chế giới hạn 8MB/request của Cloudflare R2 Edge, và ép kiểu sang định dạng .m4a
    thông qua ffmpeg vào thư mục tạm (TEMP_DIR).
    Hỗ trợ Cookie tài khoản VIP và các header ngữ cảnh trình duyệt (Sec-Fetch-*).
    """

    def __init__(self, temp_dir: str | Path = TEMP_DIR, session_cookie: str = SESSION_COOKIE):
        self.temp_dir = Path(temp_dir)
        self.session_cookie = session_cookie.strip()
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale_temp_files()

    def cleanup_stale_temp_files(self) -> int:
        """Dọn dẹp các file rác dở dang (*.tmp, *.part, *.ytdl, raw_temp_*) nếu phiên trước bị gián đoạn đột ngột."""
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

    def _init_session(self, referer_url: Optional[str] = None) -> requests.Session:
        """
        Khởi tạo requests.Session chuẩn với đầy đủ Header trình duyệt (Sec-Fetch)
        và nạp Cookies từ cookies.txt hoặc chuỗi SESSION_COOKIE.
        """
        session = requests.Session()

        # 1. Nạp cookies từ cookies.txt nếu có
        cookie_file = self.temp_dir.parent / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            try:
                cj = MozillaCookieJar(str(cookie_file))
                cj.load(ignore_discard=True, ignore_expires=True)
                session.cookies.update(cj)
                logging.debug("[Downloader] Đã tải cookies từ cookies.txt")
            except Exception as e:
                logging.debug(f"[Downloader] Lỗi nạp cookies.txt qua MozillaCookieJar ({e}), phân tích dòng thủ công:")
                try:
                    with open(cookie_file, "r", encoding="utf-8", errors="ignore") as cf:
                        for line in cf:
                            parts = line.strip().split("\t")
                            if len(parts) >= 7:
                                session.cookies.set(parts[5], parts[6], domain=parts[0].lstrip("."))
                except Exception:
                    pass

        # 2. Nạp cookies từ cấu hình SESSION_COOKIE (.env)
        if self.session_cookie:
            for item in self.session_cookie.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    session.cookies.set(k.strip(), v.strip(), domain="metruyenaudio.online")

        # 3. Chuẩn hóa headers giả lập trình duyệt Chrome
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity;q=1, *;q=0",
            "Sec-Fetch-Dest": "audio",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if referer_url:
            headers["Referer"] = referer_url

        session.headers.update(headers)
        return session

    def _download_multirange_stream(
        self, audio_url: str, episode_url: Optional[str], raw_file: Path
    ) -> Tuple[Path, Optional[int]]:
        """
        Tải luồng audio đa phân đoạn (HTTP Range Loop).
        Khắc phục triệt để lỗi máy chủ Cloudflare R2 chỉ trả về tối đa 8MB mỗi lượt tải.
        Tự động nối tiếp các phân đoạn byte cho tới khi nhận đủ 100% dung lượng.
        """
        session = self._init_session(episode_url)

        # Lượt gửi đầu tiên thăm dò kích thước tổng thể
        init_headers = {"Range": "bytes=0-"}
        resp = session.get(audio_url, headers=init_headers, stream=True, timeout=30)
        resp.raise_for_status()

        # URL máy chủ edge sau khi redirect
        final_stream_url = resp.url

        # Trích xuất tổng dung lượng file từ Content-Range (e.g. bytes 0-8388607/283954407)
        total_size = None
        content_range = resp.headers.get("Content-Range")
        if content_range:
            m = re.search(r"bytes\s+\d+-\d+/(\d+|\*)", content_range)
            if m and m.group(1) != "*":
                total_size = int(m.group(1))

        if not total_size:
            cl = resp.headers.get("Content-Length")
            total_size = int(cl) if cl else None

        if total_size:
            logging.info(
                f"[Downloader] Tổng dung lượng audio thực tế: {total_size / (1024*1024):.2f} MB"
            )
        else:
            logging.info("[Downloader] Không xác định được kích thước tổng, tải luồng liên tục...")

        downloaded = 0
        start_time = time.time()
        chunk_buffer_size = 1048576  # 1 MB buffer

        with open(raw_file, "wb") as f:
            # Ghi phân đoạn đầu tiên từ phản hồi khởi tạo
            for chunk in resp.iter_content(chunk_size=chunk_buffer_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

            # Lặp tải các phân đoạn tiếp theo nếu chưa đủ total_size
            chunk_idx = 1
            max_retries = 5

            while total_size and downloaded < total_size:
                chunk_idx += 1
                retry_count = 0
                success_chunk = False

                while retry_count < max_retries:
                    try:
                        chunk_headers = {"Range": f"bytes={downloaded}-"}
                        c_resp = session.get(
                            final_stream_url, headers=chunk_headers, stream=True, timeout=30
                        )
                        c_resp.raise_for_status()

                        chunk_read = 0
                        for chunk in c_resp.iter_content(chunk_size=chunk_buffer_size):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                chunk_read += len(chunk)

                        if chunk_read == 0:
                            # Không còn dữ liệu đọc thêm
                            break

                        elapsed = time.time() - start_time
                        speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                        pct = (downloaded / total_size) * 100
                        eta_sec = (
                            int((total_size - downloaded) / (speed * 1024 * 1024))
                            if speed > 0
                            else 0
                        )
                        eta_str = f"{eta_sec // 60:02d}:{eta_sec % 60:02d}"

                        sys.stdout.write(
                            f"\r[Tải Audio] {downloaded / (1024*1024):.1f} / {total_size / (1024*1024):.1f} MB "
                            f"({pct:.1f}%) | {speed:.2f} MB/s | ETA: {eta_str}"
                        )
                        sys.stdout.flush()
                        success_chunk = True
                        break

                    except Exception as ex:
                        retry_count += 1
                        logging.warning(
                            f"\n[Downloader] Lỗi kết nối tại offset {downloaded} "
                            f"(Lần thử {retry_count}/{max_retries}): {ex}"
                        )
                        time.sleep(2 * retry_count)

                if not success_chunk and retry_count >= max_retries:
                    raise RuntimeError(
                        f"Mất kết nối tải phân đoạn tại offset {downloaded} sau {max_retries} lần thử lại."
                    )

        total_time = time.time() - start_time
        print()  # Xuống dòng sau tiến trình tải
        avg_speed = (downloaded / (1024 * 1024)) / total_time if total_time > 0 else 0
        logging.info(
            f"[Downloader] Đã tải hoàn chỉnh file raw ({downloaded / (1024*1024):.2f} MB) "
            f"trong {total_time:.1f}s (Tốc độ TB: {avg_speed:.2f} MB/s)."
        )

        # Kiểm tra tính toàn vẹn của file raw vừa tải
        actual_raw_size = raw_file.stat().st_size if raw_file.exists() else 0
        if actual_raw_size == 0:
            raise RuntimeError("File audio raw tải về rỗng (0 bytes)")
        if total_size and total_size > 0 and actual_raw_size < total_size * 0.95:
            raise RuntimeError(
                f"File raw tải về chưa hoàn tất ({actual_raw_size / (1024*1024):.2f} MB), "
                f"thấp hơn 95% dung lượng công bố từ máy chủ ({total_size / (1024*1024):.2f} MB)"
            )

        return raw_file, total_size

    def _convert_to_m4a(self, raw_audio_path: Path, target_m4a_path: Path) -> Path:
        """
        Chuyển đổi file audio raw sang định dạng chuẩn .m4a (AAC 128k) bằng ffmpeg.
        Nếu hệ thống không có sẵn ffmpeg, giữ nguyên file audio gốc với phần mở rộng phù hợp.
        """
        ffmpeg_bin = shutil.which("ffmpeg")

        if ffmpeg_bin:
            logging.info(f"🔄 [CHUYỂN ĐỔI AUDIO] Đang gọi ffmpeg chuyển đổi sang .M4A...")
            ffmpeg_cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(raw_audio_path),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(target_m4a_path),
            ]
            try:
                res = subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
                if raw_audio_path.exists():
                    raw_audio_path.unlink()
                logging.info(f"[Downloader] Chuyển đổi .M4A thành công: {target_m4a_path.name}")
                return target_m4a_path
            except subprocess.CalledProcessError as e:
                err_text = e.stderr or e.stdout or str(e)
                logging.warning(f"[Downloader] Lỗi khi ép kiểu M4A qua ffmpeg: {err_text}")
                # Giữ lại raw file nếu ffmpeg lỗi
                return raw_audio_path
        else:
            logging.warning(
                "[Downloader] ffmpeg không có trong PATH. Bỏ qua bước ép kiểu AAC, giữ nguyên file audio tải về."
            )
            # Đổi tên file raw sang đuôi mp3 nếu chưa có
            final_path = target_m4a_path.with_suffix(".mp3")
            if raw_audio_path.exists():
                if final_path.exists():
                    final_path.unlink()
                raw_audio_path.rename(final_path)
            return final_path

    def _download_ytdlp_fallback(
        self, episode_item: Dict[str, str], out_filename_template: str
    ) -> Optional[Path]:
        """Phương án dự phòng thứ 2: Sử dụng yt-dlp đối với các nguồn âm thanh ngoài thông thường."""
        audio_url = episode_item.get("audio_url") or episode_item.get("episode_url")
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format",
            "m4a",
            "--paths",
            str(self.temp_dir),
            "-o",
            out_filename_template,
            "--user-agent",
            USER_AGENT,
            "--no-progress",
            "--add-header",
            "Sec-Fetch-Dest:audio",
            "--add-header",
            "Sec-Fetch-Mode:no-cors",
            "--add-header",
            "Sec-Fetch-Site:same-origin",
        ]

        if episode_item.get("episode_url"):
            cmd.extend(["--referer", episode_item["episode_url"]])

        if self.session_cookie:
            cmd.extend(["--add-header", f"Cookie:{self.session_cookie}"])

        cookie_file = self.temp_dir.parent / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            cmd.extend(["--cookies", str(cookie_file)])

        cmd.append(audio_url)
        logging.info(f"[Downloader] Thử yt-dlp fallback: {' '.join(cmd)}")

        result = subprocess.run(cmd, check=True)

        expected_stem = out_filename_template.rsplit(".", 1)[0]
        matching_files = list(self.temp_dir.glob(f"{expected_stem}*.m4a"))
        if not matching_files:
            all_m4a_files = sorted(
                self.temp_dir.glob("*.m4a"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if all_m4a_files:
                matching_files = [all_m4a_files[0]]

        return matching_files[0] if matching_files else None

    def validate_downloaded_audio(
        self, file_path: Path, expected_size: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Kiểm tra tính toàn vẹn và dung lượng tối thiểu của file audio vừa tải về.
        Args:
            file_path: Đường dẫn file audio.
            expected_size: Dung lượng byte dự kiến từ máy chủ (nếu có).
        Returns:
            (is_valid, error_reason)
        """
        if not file_path.exists():
            return False, f"File không tồn tại: {file_path}"

        size = file_path.stat().st_size
        size_mb = size / (1024 * 1024)

        if size == 0:
            return False, "File rỗng (0 bytes)"

        # Kiểm tra tính toàn vẹn container audio qua ffprobe (nếu có sẵn ffprobe)
        has_valid_ffprobe = False
        ffprobe_bin = shutil.which("ffprobe")
        if ffprobe_bin:
            try:
                probe_cmd = [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_path),
                ]
                res = subprocess.run(
                    probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
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
                    has_valid_ffprobe = True
            except Exception as ex:
                logging.warning(f"[Downloader] Không thể kiểm tra thời lượng qua ffprobe: {ex}")

        # Kiểm tra dung lượng
        if expected_size and expected_size > 0:
            if size < expected_size * 0.95:
                return False, (
                    f"Dung lượng file chưa hoàn tất ({size_mb:.2f} MB), "
                    f"thấp hơn 95% dung lượng công bố ({expected_size / (1024*1024):.2f} MB)"
                )
        elif not has_valid_ffprobe:
            # Khi không có ffprobe để xác thực thời lượng, kiểm tra theo ngưỡng cấu hình MIN_AUDIO_FILE_SIZE_BYTES
            if size < MIN_AUDIO_FILE_SIZE_BYTES:
                return False, (
                    f"Dung lượng file quá nhỏ ({size_mb:.2f} MB), "
                    f"không đạt ngưỡng tối thiểu {MIN_AUDIO_FILE_SIZE_MB:.1f} MB (nghi ngờ đứt mạng hoặc tải dở)"
                )

        return True, ""

    def download_episode(
        self, episode_item: Dict[str, str], custom_filename: Optional[str] = None
    ) -> Path:
        """
        Tải 1 tập truyện và chuyển đổi sang M4A.
        Args:
            episode_item: Dict chứa 'audio_url', 'story_title', 'episode_title', v.v.
            custom_filename: Tên file tùy chỉnh (không cần đuôi mở rộng)
        Returns:
            Path tới file audio hoàn chỉnh vừa được tạo ra trong thư mục tạm.
        Raises:
            RuntimeError nếu quá trình tải thất bại hoặc file không hợp lệ.
        """
        audio_url = episode_item.get("audio_url") or episode_item.get("episode_url")
        episode_url = episode_item.get("episode_url")
        story_title = episode_item.get("story_title", "UnknownStory")
        episode_title = episode_item.get("episode_title", "UnknownEpisode")

        if not audio_url:
            raise ValueError("[Downloader] Không tìm thấy URL hợp lệ để tải.")

        # Tạo tên file sạch nếu có custom_filename hoặc tự động tạo theo TênBộTruyện - TênTập
        if not custom_filename:
            safe_story = "".join(
                c for c in story_title if c.isalnum() or c in (" ", "-", "_")
            ).strip()
            safe_episode = "".join(
                c for c in episode_title if c.isalnum() or c in (" ", "-", "_")
            ).strip()
            out_stem = f"{safe_story} - {safe_episode}"
        else:
            out_stem = "".join(
                c for c in custom_filename if c.isalnum() or c in (" ", "-", "_")
            ).strip()

        target_m4a = self.temp_dir / f"{out_stem}.m4a"
        raw_temp_audio = self.temp_dir / f"raw_temp_{int(time.time()*1000)}.mp3"

        logging.info(
            f"[Downloader] Bắt đầu tải tập: '{story_title} - {episode_title}' từ URL: {audio_url}"
        )

        downloaded_file = None
        expected_size = None
        raw_path = None

        # ─── BƯỚC 1: Tải trực tiếp bằng Native Multi-Range Stream Downloader ───────────
        try:
            raw_path, expected_size = self._download_multirange_stream(
                audio_url=audio_url, episode_url=episode_url, raw_file=raw_temp_audio
            )
            # Chuyển đổi sang M4A bằng ffmpeg (hoặc giữ raw nếu thiếu ffmpeg)
            downloaded_file = self._convert_to_m4a(raw_path, target_m4a)

        except Exception as stream_err:
            logging.warning(
                f"[Downloader] Native Range Stream gặp sự cố ({stream_err}). "
                f"Kích hoạt phương án yt-dlp fallback..."
            )
            if raw_temp_audio.exists():
                try:
                    raw_temp_audio.unlink()
                except Exception:
                    pass

            # ─── BƯỚC 2: Fallback qua yt-dlp ──────────────────────────────────────
            try:
                out_filename_template = f"{out_stem}.%(ext)s"
                downloaded_file = self._download_ytdlp_fallback(
                    episode_item, out_filename_template
                )
            except Exception as ytdlp_err:
                logging.error(f"[Downloader] yt-dlp fallback cũng thất bại: {ytdlp_err}")
                raise RuntimeError(
                    f"Tải thất bại qua cả Native Stream ({stream_err}) và yt-dlp ({ytdlp_err})"
                ) from stream_err

        if not downloaded_file or not downloaded_file.exists():
            raise RuntimeError(
                f"[Downloader] Tải hoàn tất nhưng không tìm thấy file audio trong {self.temp_dir}"
            )

        # ─── BƯỚC 3: Kiểm tra tính toàn vẹn và dung lượng tối thiểu ─────────────────
        # Lưu ý: Nếu file đã qua ffmpeg transcode sang .m4a (AAC 128k), dung lượng file phụ thuộc vào
        # bitrate 128k và thời lượng audio, không thể so sánh với expected_size của file raw ban đầu.
        # File raw đã được kiểm tra tính toàn vẹn đạt chuẩn >= 95% trong _download_multirange_stream.
        is_transcoded = (
            raw_path is not None
            and downloaded_file != raw_path
            and downloaded_file.suffix.lower() == ".m4a"
        )
        target_expected_size = None if is_transcoded else expected_size

        is_valid, reason = self.validate_downloaded_audio(
            downloaded_file, expected_size=target_expected_size
        )
        if not is_valid:
            try:
                if downloaded_file.exists():
                    downloaded_file.unlink()
                    logging.warning(f"[Downloader] Đã xóa file hỏng: {downloaded_file.name}")
            except Exception as del_err:
                logging.warning(
                    f"[Downloader] Không thể xóa file hỏng {downloaded_file.name}: {del_err}"
                )
            raise RuntimeError(f"Tập tải về không hợp lệ: {reason}")

        size_mb = downloaded_file.stat().st_size / (1024 * 1024)
        logging.info(
            f"[Downloader] File audio hoàn chỉnh & đạt chuẩn ({size_mb:.2f} MB): {downloaded_file.name}"
        )
        return downloaded_file


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    dl = Downloader()
    sample_item = {
        "story_title": "ĐẠI SƯ HUYNH HẬN MA NỮ, CẢ TÔNG MÔN LẠI MUỐN HẮN CƯỚI NÀNG",
        "episode_title": "Chương 1",
        "episode_url": "https://metruyenaudio.online/truyen/dai-su-huynh-han-ma-nu-ca-tong-mon-lai-muon-han-cuoi-nang/nghe/1",
        "audio_url": "https://metruyenaudio.online/api/audio/cmupdoktl073oj84s3j7k1pwd",
    }
    try:
        file_path = dl.download_episode(sample_item)
        print(f"\n✅ Đã tải thành công file: {file_path}")
    except Exception as err:
        print(f"\n❌ Lỗi tải thử nghiệm: {err}")
