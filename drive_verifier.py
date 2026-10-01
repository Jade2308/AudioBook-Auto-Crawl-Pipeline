"""
drive_verifier.py — Module 8: Google Drive Verification & History Reconciliation

Cơ chế đọc danh sách các file audio trên Google Drive (thông qua rclone),
trích xuất thông tin bộ truyện và số tập, sau đó đối soát 2 chiều với history.txt:
1. Phát hiện các tập có trong history nhưng THIẾU trên Google Drive (cần tải bù).
2. Phát hiện các file đã CÓ trên Google Drive nhưng THIẾU trong history (cần ghi nhận để tránh tải trùng).
3. Hỗ trợ tự động đồng bộ (sync) hoặc dọn dẹp (prune) an toàn (tự động tạo backup .bak).
4. Hỗ trợ chạy dòng lệnh (CLI) độc lập và tích hợp vào Telegram Bot.
"""

import os
import sys
import re
import json
import time
import shutil
import logging
import argparse
import subprocess
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set

from config import (
    RCLONE_REMOTE_BASE,
    HISTORY_FILE,
    SITE_BASE_URL,
    MIN_AUDIO_FILE_SIZE_MB,
    MIN_AUDIO_FILE_SIZE_BYTES,
    STORY_MAPPINGS_FILE,
)



logger = logging.getLogger("DriveVerifier")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ─── Utility Functions: Chuẩn Hóa Tên & Bóc Tách ─────────────────────────────

def remove_vn_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt chuẩn xác bằng Unicode NFKD."""
    text = re.sub(r'[đĐ]', 'd', text)
    normalized = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in normalized if not unicodedata.combining(c))


def slugify_vn(text: str) -> str:
    """
    Chuyển tên tiếng Việt / tiêu đề truyện thành URL slug chuẩn.
    Ví dụ: 'Toàn Cầu Băng Phong: Ta Chế Tạo...' -> 'toan-cau-bang-phong-ta-che-tao'
    """
    cleaned = remove_vn_accents(text).lower()
    slug = re.sub(r'[^a-z0-9]+', '-', cleaned).strip('-')
    slug = re.sub(r'-+', '-', slug)
    return slug


def extract_episode_number(filename: str) -> Optional[int]:
    """
    Trích xuất số tập (hoặc số chương) từ tên file audio.
    Ví dụ:
        'Toàn Cầu Băng Phong - Tập 1.m4a' -> 1
        'Đại Phụng - Chương 102.m4a'      -> 102
        'Truyện VIP - Tap 05.m4a'         -> 5
        'Truyện Hay - Ep 3.m4a'           -> 3
        'Bộ Truyện - 42.m4a'              -> 42
    """
    stem = Path(filename).stem

    # Pattern 1: Tìm rõ chữ Tập/Chương/Ep + số
    match = re.search(r'(?:t[aậ]p|ch[uư][oơ]ng|ep|episode)\s*(\d+)', stem, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Pattern 2: Tìm số nằm sau dấu gạch ngang ở cuối tên file (e.g. 'Tên Truyện - 12')
    hyphen_match = re.search(r'-\s*(\d+)\s*$', stem)
    if hyphen_match:
        return int(hyphen_match.group(1))

    # Pattern 3: Lấy số cuối cùng xuất hiện trong tên file
    all_numbers = re.findall(r'\d+', stem)
    if all_numbers:
        return int(all_numbers[-1])

    return None


def parse_history_entry(line: str) -> Tuple[Optional[str], Optional[int], str]:
    """
    Bóc tách 1 dòng trong history.txt.
    Returns:
        (slug, episode_number, clean_line)
    """
    clean_line = line.split('|')[0].strip()
    if not clean_line:
        return None, None, clean_line

    # Định dạng URL chuẩn: .../truyen/<slug>/nghe/<no>
    match = re.search(r'/truyen/([^/]+)/nghe/(\d+)', clean_line)
    if match:
        return match.group(1), int(match.group(2)), clean_line

    # Dự phòng: nếu chỉ ghi slug-tap-no hoặc text khác
    num_match = re.search(r'(\d+)$', clean_line)
    ep_num = int(num_match.group(1)) if num_match else None
    return None, ep_num, clean_line


def build_episode_url(slug: str, ep_no: int, base_url: str = SITE_BASE_URL) -> str:
    """Tạo lại URL tập truyện chuẩn từ slug và số tập."""
    return f"{base_url.rstrip('/')}/truyen/{slug}/nghe/{ep_no}"


# ─── Data Structures ──────────────────────────────────────────────────────────

class VerifyResult:
    """Lưu kết quả đối soát giữa Google Drive và history.txt."""
    def __init__(self):
        self.total_drive_files: int = 0
        self.total_history_records: int = 0
        self.matched: List[Dict] = []
        self.missing_on_drive: List[Dict] = []
        self.missing_in_history: List[Dict] = []
        self.corrupted_on_drive: List[Dict] = []
        self.drive_stories: Set[str] = set()
        self.history_stories: Set[str] = set()
        self.scan_time: float = 0.0
        self.error_message: Optional[str] = None


# ─── DriveVerifier Core Class ─────────────────────────────────────────────────

class DriveVerifier:
    """
    Quét danh sách file từ Google Drive qua rclone và đối soát với history.txt.
    """
    def __init__(
        self,
        remote_base: str = RCLONE_REMOTE_BASE,
        history_file: str | Path = HISTORY_FILE,
        site_base_url: str = SITE_BASE_URL,
        min_size_mb: float = MIN_AUDIO_FILE_SIZE_MB,
        mappings_file: str | Path = STORY_MAPPINGS_FILE,
    ):
        self.remote_base = remote_base.rstrip("/")
        self.history_file = Path(history_file)
        self.site_base_url = site_base_url.rstrip("/")
        self.min_size_mb = float(min_size_mb)
        self.min_size_bytes = int(self.min_size_mb * 1024 * 1024)
        self.mappings_file = Path(mappings_file)
        self.url_to_folder: Dict[str, str] = {}
        self.folder_to_url: Dict[str, str] = {}
        self.story_titles: Dict[str, str] = {}
        self.load_mappings()

    def load_mappings(self) -> None:
        """Đọc ánh xạ giữa URL slug trên web và folder slug trên Google Drive từ story_mappings.json."""
        if not self.mappings_file.exists():
            return
        try:
            with open(self.mappings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for url_slug, info in data.items():
                if isinstance(info, dict):
                    f_slug = info.get("folder_slug", url_slug)
                    title = info.get("title", "")
                else:
                    f_slug = str(info)
                    title = ""
                self.url_to_folder[url_slug] = f_slug
                self.folder_to_url[f_slug] = url_slug
                if title:
                    self.story_titles[f_slug] = title
                    self.story_titles[url_slug] = title
            logger.info(f"[DriveVerifier] Đã nạp {len(self.url_to_folder)} ánh xạ bộ truyện từ {self.mappings_file.name}")
        except Exception as e:
            logger.warning(f"[DriveVerifier] Lỗi đọc {self.mappings_file}: {e}")

    def get_canonical_slug(self, slug: str) -> str:
        """Trả về slug chuẩn hóa (dùng folder_slug làm canonical) để đối soát đồng nhất."""
        return self.url_to_folder.get(slug, slug)

    def get_url_slug_for_folder(self, folder_slug: str) -> str:
        """Trả về URL slug chuẩn trên website cho một folder trên Drive."""
        return self.folder_to_url.get(folder_slug, folder_slug)

    def get_story_title(self, slug: str) -> Optional[str]:
        """Lấy tên tiếng Việt đầy đủ của bộ truyện nếu có trong mapping."""
        return self.story_titles.get(slug)

    def resolve_missing_mapping(self, url_slug: str) -> Optional[str]:
        """
        Nếu gặp một URL slug chưa có trong mapping, tự động bóc tách từ website và lưu lại.
        """
        try:
            import requests
            from bs4 import BeautifulSoup
            story_url = f"{self.site_base_url}/truyen/{url_slug}"
            res = requests.get(story_url, timeout=6)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                h1 = soup.find("h1")
                if h1 and h1.get_text(strip=True):
                    title = h1.get_text(strip=True)
                    safe_folder = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_", ".")).strip()
                    f_slug = slugify_vn(safe_folder)
                    self.url_to_folder[url_slug] = f_slug
                    self.folder_to_url[f_slug] = url_slug
                    self.story_titles[f_slug] = title
                    self.story_titles[url_slug] = title
                    self._save_mapping(url_slug, title, f_slug)
                    logger.info(f"[DriveVerifier] Đã tự động nhận diện ánh xạ mới: '{url_slug}' -> '{f_slug}' ({title})")
                    return f_slug
        except Exception as ex:
            logger.debug(f"[DriveVerifier] Không thể tự động resolve slug '{url_slug}': {ex}")
        return None

    def _save_mapping(self, url_slug: str, title: str, folder_slug: str) -> None:
        """Lưu ánh xạ mới vào story_mappings.json."""
        try:
            data = {}
            if self.mappings_file.exists() and self.mappings_file.stat().st_size > 0:
                try:
                    with open(self.mappings_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            data[url_slug] = {"title": title, "folder_slug": folder_slug}
            with open(self.mappings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[DriveVerifier] Không thể ghi {self.mappings_file}: {e}")



    def check_rclone_available(self) -> bool:
        """Kiểm tra xem lệnh rclone có khả dụng trên hệ thống không."""
        try:
            res = subprocess.run(
                ["rclone", "version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
            return res.returncode == 0
        except FileNotFoundError:
            return False

    def list_drive_files(self, remote_target: Optional[str] = None) -> List[Dict]:
        """
        Dùng rclone lsjson để lấy danh sách toàn bộ file trong thư mục Google Drive.
        Returns:
            List các dict chứa {Path, Name, Size, ModTime, IsDir, ...}
        """
        target = (remote_target or self.remote_base).rstrip("/")
        if not self.check_rclone_available():
            raise RuntimeError(
                "Lệnh 'rclone' không tồn tại trên hệ thống. "
                "Vui lòng cài đặt rclone hoặc kiểm tra biến môi trường PATH."
            )

        cmd = [
            "rclone", "lsjson",
            target,
            "--recursive",
            "--files-only",
            "--no-modtime"
        ]

        logger.info(f"[DriveVerifier] Đang đọc danh sách file từ Google Drive qua lệnh: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True
            )
            raw_json = result.stdout.strip()
            if not raw_json:
                return []
            items = json.loads(raw_json)
            logger.info(f"[DriveVerifier] Đã lấy thành công {len(items)} file từ {target}")
            return items

        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() or str(e)
            if "didn't find section in config file" in err_msg or "Config file" in err_msg and "not found" in err_msg:
                raise RuntimeError(
                    f"Không tìm thấy cấu hình remote cho '{target}' trong rclone.conf. "
                    f"Vui lòng chạy 'rclone config' để liên kết Google Drive, hoặc kiểm tra lại RCLONE_REMOTE_BASE trong file .env."
                ) from e
            raise RuntimeError(f"Lỗi rclone lsjson: {err_msg}") from e
        except json.JSONDecodeError as je:
            raise RuntimeError(f"Lỗi phân tích JSON trả về từ rclone: {je}") from je

    def list_local_files(self, local_dir: str | Path) -> List[Dict]:
        """
        Đọc danh sách file từ thư mục cục bộ (dùng để kiểm thử giả lập Drive).
        """
        path_obj = Path(local_dir)
        if not path_obj.exists():
            raise FileNotFoundError(f"Thư mục cục bộ không tồn tại: {local_dir}")

        items = []
        for file_path in path_obj.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(path_obj).as_posix()
                items.append({
                    "Path": rel_path,
                    "Name": file_path.name,
                    "Size": file_path.stat().st_size,
                    "IsDir": False
                })
        logger.info(f"[DriveVerifier] Đã đọc {len(items)} file từ thư mục cục bộ: {local_dir}")
        return items

    def load_history(self) -> List[str]:
        """Đọc danh sách dòng trong history.txt."""
        if not self.history_file.exists():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8", errors="ignore") as f:
                return [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"[DriveVerifier] Lỗi khi đọc {self.history_file}: {e}")
            return []

    def reconcile(
        self,
        drive_files: Optional[List[Dict]] = None,
        local_dir: Optional[str | Path] = None
    ) -> VerifyResult:
        """
        Thực hiện đối soát toàn diện giữa Google Drive và history.txt.
        """
        start_time = time.time()
        res = VerifyResult()

        # 1. Thu thập danh sách file từ Drive hoặc Local
        try:
            if drive_files is not None:
                files = drive_files
            elif local_dir is not None:
                files = self.list_local_files(local_dir)
            else:
                files = self.list_drive_files()
        except Exception as e:
            res.error_message = str(e)
            res.scan_time = time.time() - start_time
            return res

        # Lọc chỉ lấy các file audio (.m4a, .mp3, .aac, v.v.)
        audio_extensions = {".m4a", ".mp3", ".aac", ".flac", ".ogg", ".wav"}
        valid_files = [
            f for f in files
            if Path(f.get("Path", "")).suffix.lower() in audio_extensions
        ]
        res.total_drive_files = len(valid_files)

        # 2. Xử lý danh sách file trên Drive: bóc tách folder truyện & số tập
        # Key đại diện: (slug, ep_no)
        # Drive map: key -> file_info
        drive_map: Dict[Tuple[str, int], Dict] = {}

        for f in valid_files:
            rel_path = f.get("Path", "")
            size = f.get("Size", 0)

            path_parts = Path(rel_path).parts
            # Thông thường cấu trúc là: [Tên_Bộ_Truyện]/[Tên_Bộ_Truyện] - [Tên_Tập].m4a
            if len(path_parts) >= 2:
                story_folder = path_parts[0]
            else:
                story_folder = Path(rel_path).stem.split(" - ")[0] if " - " in Path(rel_path).stem else "Unsorted"

            res.drive_stories.add(story_folder)
            folder_slug = slugify_vn(story_folder)
            canonical_slug = self.get_canonical_slug(folder_slug)
            ep_no = extract_episode_number(f.get("Name", rel_path))
            real_url_slug = self.get_url_slug_for_folder(folder_slug)
            expected_url = build_episode_url(real_url_slug, ep_no, self.site_base_url) if ep_no is not None else ""

            # Kiểm tra file 0-byte hoặc dung lượng quá nhỏ so với ngưỡng tối thiểu
            is_corrupted = False
            corrupt_reason = ""
            if size == 0:
                is_corrupted = True
                corrupt_reason = "File 0 bytes (rỗng/hỏng)"
            elif size < self.min_size_bytes:
                is_corrupted = True
                size_mb = size / (1024 * 1024)
                corrupt_reason = f"Dung lượng quá nhỏ ({size_mb:.2f} MB < ngưỡng {self.min_size_mb:.1f} MB)"

            if is_corrupted:
                res.corrupted_on_drive.append({
                    "path": rel_path,
                    "name": f.get("Name", ""),
                    "size": size,
                    "size_mb": round(size / (1024 * 1024), 2),
                    "reason": corrupt_reason,
                    "story_folder": story_folder,
                    "story_slug": folder_slug,
                    "canonical_slug": canonical_slug,
                    "episode_no": ep_no,
                    "expected_url": expected_url,
                })
                # Không đưa file hỏng vào drive_map để tránh ghi nhận là tập đã hoàn thành
                continue

            if ep_no is not None:
                key = (canonical_slug, ep_no)
                file_info = {
                    "path": rel_path,
                    "name": f.get("Name", ""),
                    "size": size,
                    "size_mb": round(size / (1024 * 1024), 2),
                    "story_folder": story_folder,
                    "story_slug": folder_slug,
                    "canonical_slug": canonical_slug,
                    "url_slug": real_url_slug,
                    "episode_no": ep_no,
                    "expected_url": expected_url,
                }
                drive_map[key] = file_info

        # 3. Đọc và xử lý history.txt
        history_lines = self.load_history()
        res.total_history_records = len(history_lines)

        # History map: key -> dict info
        history_map: Dict[Tuple[str, int], Dict] = {}
        unparsed_history: List[str] = []

        for line in history_lines:
            slug, ep_no, clean_line = parse_history_entry(line)
            if slug and ep_no is not None:
                canonical_slug = self.get_canonical_slug(slug)
                key = (canonical_slug, ep_no)
                history_map[key] = {
                    "url": clean_line,
                    "url_slug": slug,
                    "canonical_slug": canonical_slug,
                    "episode_no": ep_no,
                }
                res.history_stories.add(canonical_slug)
            else:
                unparsed_history.append(clean_line)

        # 4. Đối soát 2 chiều
        # TH 1 & 2: Duyệt qua history_map
        for key, hist_item in history_map.items():
            canonical_slug, ep_no = key
            hist_url = hist_item["url"]
            url_slug = hist_item.get("url_slug", canonical_slug)

            if key in drive_map:
                drive_item = drive_map[key]
                res.matched.append({
                    "slug": canonical_slug,
                    "url_slug": url_slug,
                    "episode_no": ep_no,
                    "history_url": hist_url,
                    "drive_path": drive_item["path"],
                    "size": drive_item["size"],
                    "story_folder": drive_item["story_folder"],
                })
            else:
                # Thử tự động resolve nếu slug trên web chưa có trong mapping
                resolved = self.resolve_missing_mapping(url_slug)
                if resolved and (resolved, ep_no) in drive_map:
                    drive_item = drive_map[(resolved, ep_no)]
                    res.matched.append({
                        "slug": resolved,
                        "url_slug": url_slug,
                        "episode_no": ep_no,
                        "history_url": hist_url,
                        "drive_path": drive_item["path"],
                        "size": drive_item["size"],
                        "story_folder": drive_item["story_folder"],
                    })
                else:
                    # Có trong History nhưng THIẾU trên Drive
                    st_name = self.get_story_title(canonical_slug) or url_slug
                    res.missing_on_drive.append({
                        "slug": canonical_slug,
                        "url_slug": url_slug,
                        "story_title": st_name,
                        "episode_no": ep_no,
                        "history_url": hist_url,
                    })

        # TH 3: Duyệt qua drive_map tìm file có trên Drive nhưng THIẾU trong history
        matched_keys = {
            (it["slug"], it["episode_no"])
            for it in res.matched
        }
        for key, drive_item in drive_map.items():
            if key not in matched_keys and key not in history_map:
                res.missing_in_history.append(drive_item)

        res.scan_time = time.time() - start_time
        return res


    def sync_drive_to_history(
        self,
        missing_in_history: List[Dict],
        backup: bool = True
    ) -> int:
        """
        Bổ sung các tập đã có trên Drive nhưng thiếu trong history.txt.
        Tự động tạo file backup history.txt.bak.<timestamp> trước khi ghi.
        Returns:
            Số lượng bản ghi mới được bổ sung vào history.txt.
        """
        if not missing_in_history:
            return 0

        # Backup file hiện tại
        if backup and self.history_file.exists():
            backup_path = self.history_file.with_name(
                f"{self.history_file.name}.bak.{int(time.time())}"
            )
            shutil.copy2(self.history_file, backup_path)
            logger.info(f"[DriveVerifier] Đã tạo file sao lưu: {backup_path}")

        # Đọc các URL hiện tại để tránh ghi trùng
        existing_urls = set(self.load_history())
        urls_to_add = []

        for item in missing_in_history:
            url = item.get("expected_url")
            if url and url not in existing_urls:
                urls_to_add.append(url)
                existing_urls.add(url)

        if urls_to_add:
            with open(self.history_file, "a", encoding="utf-8") as f:
                for u in urls_to_add:
                    f.write(f"{u}\n")
            logger.info(f"[DriveVerifier] Đã bổ sung {len(urls_to_add)} bản ghi mới vào {self.history_file}")

        return len(urls_to_add)

    def prune_orphaned_history(
        self,
        missing_on_drive: List[Dict],
        backup: bool = True
    ) -> int:
        """
        Loại bỏ khỏi history.txt các URL mà Google Drive KHÔNG CÓ file tương ứng.
        Giúp pipeline tự động tải bù các tập bị thiếu trong lần chạy tiếp theo.
        Returns:
            Số lượng bản ghi đã được loại bỏ.
        """
        if not missing_on_drive:
            return 0

        urls_to_remove = {item["history_url"] for item in missing_on_drive}
        current_lines = self.load_history()

        if backup and self.history_file.exists():
            backup_path = self.history_file.with_name(
                f"{self.history_file.name}.bak.{int(time.time())}"
            )
            shutil.copy2(self.history_file, backup_path)
            logger.info(f"[DriveVerifier] Đã tạo file sao lưu: {backup_path}")

        remaining_lines = [ln for ln in current_lines if ln not in urls_to_remove]
        removed_count = len(current_lines) - len(remaining_lines)

        with open(self.history_file, "w", encoding="utf-8") as f:
            for ln in remaining_lines:
                f.write(f"{ln}\n")

        logger.info(f"[DriveVerifier] Đã loại bỏ {removed_count} bản ghi mồ côi khỏi {self.history_file}")
        return removed_count

    def purge_corrupted_files(
        self,
        corrupted_items: Optional[List[Dict]] = None,
        backup: bool = True,
        local_dir: Optional[str | Path] = None
    ) -> int:
        """
        Xóa các file audio bị hỏng / thiếu dung lượng trên Google Drive và
        đồng thời gỡ bỏ URL tương ứng khỏi history.txt để pipeline tự động tải lại bản chuẩn.
        Returns:
            Số lượng file hỏng đã được xóa và dọn dẹp.
        """
        if corrupted_items is None:
            res = self.reconcile(local_dir=local_dir)
            corrupted_items = res.corrupted_on_drive

        if not corrupted_items:
            logger.info("[DriveVerifier] Không có file hỏng nào cần xử lý.")
            return 0

        # 1. Xóa file trên Google Drive hoặc local
        deleted_count = 0
        urls_to_remove = set()

        for item in corrupted_items:
            rel_path = item.get("path")
            if not rel_path:
                continue

            if item.get("expected_url"):
                urls_to_remove.add(item["expected_url"])

            if local_dir is not None:
                local_file = Path(local_dir) / rel_path
                try:
                    if local_file.exists():
                        local_file.unlink()
                        deleted_count += 1
                        logger.info(f"[DriveVerifier] Đã xóa file hỏng cục bộ: {local_file}")
                except Exception as ex:
                    logger.warning(f"[DriveVerifier] Lỗi xóa file cục bộ {local_file}: {ex}")
            else:
                target_remote = f"{self.remote_base}/{rel_path}"
                cmd = ["rclone", "deletefile", target_remote]
                logger.info(f"[DriveVerifier] Đang xóa file hỏng trên Drive: {' '.join(cmd)}")
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    deleted_count += 1
                    logger.info(f"[DriveVerifier] Đã xóa thành công file hỏng trên Drive: {target_remote}")
                except Exception as ex:
                    logger.error(f"[DriveVerifier] Lỗi xóa file trên Drive '{target_remote}': {ex}")

        # 2. Xóa các URL tương ứng khỏi history.txt
        if self.history_file.exists():
            current_lines = self.load_history()
            corrupted_keys = {
                (it["story_slug"], it["episode_no"])
                for it in corrupted_items
                if it.get("story_slug") and it.get("episode_no") is not None
            }

            remaining_lines = []
            for ln in current_lines:
                slug, ep_no, clean_line = parse_history_entry(ln)
                is_corrupted_line = False
                if clean_line in urls_to_remove:
                    is_corrupted_line = True
                elif slug and ep_no is not None and (slug, ep_no) in corrupted_keys:
                    is_corrupted_line = True

                if not is_corrupted_line:
                    remaining_lines.append(ln)

            removed_history_count = len(current_lines) - len(remaining_lines)
            if removed_history_count > 0:
                if backup:
                    backup_path = self.history_file.with_name(
                        f"{self.history_file.name}.bak.{int(time.time())}"
                    )
                    shutil.copy2(self.history_file, backup_path)
                    logger.info(f"[DriveVerifier] Đã tạo file sao lưu: {backup_path}")

                with open(self.history_file, "w", encoding="utf-8") as f:
                    for ln in remaining_lines:
                        f.write(f"{ln}\n")

                logger.info(
                    f"[DriveVerifier] Đã loại bỏ {removed_history_count} bản ghi tương ứng khỏi {self.history_file}"
                )

        return deleted_count

    def format_report(
        self,
        res: VerifyResult,
        max_details: int = 10,
        is_html: bool = False
    ) -> str:
        """
        Tạo báo cáo kết quả đối soát dưới dạng văn bản đẹp (hỗ trợ HTML cho Telegram hoặc plain text cho Console).
        """
        if res.error_message:
            if is_html:
                return (
                    f"⚠️ <b>LỖI ĐỐI SOÁT GOOGLE DRIVE:</b>\n\n"
                    f"<code>{res.error_message}</code>"
                )
            return f"[ERROR] Lỗi đối soát Google Drive:\n{res.error_message}"

        # Tính toán tỷ lệ khớp
        total_unique = len(res.matched) + len(res.missing_on_drive) + len(res.missing_in_history)
        match_rate = (len(res.matched) / total_unique * 100) if total_unique > 0 else 100.0

        if is_html:
            lines = [
                "📊 <b>BÁO CÁO ĐỐI SOÁT GOOGLE DRIVE VS HISTORY</b>",
                "──────────────────────────",
                f"⏱ Thời gian quét: <code>{res.scan_time:.2f}s</code>",
                f"📁 Tổng file audio trên Drive: <b>{res.total_drive_files}</b>",
                f"📜 Tổng bản ghi trong history: <b>{res.total_history_records}</b>",
                f"📚 Số bộ truyện nhận diện: <b>{len(res.drive_stories)}</b> (Drive) | <b>{len(res.history_stories)}</b> (History)",
                f"🎯 Độ chuẩn xác khớp: <b>{match_rate:.1f}%</b>\n",
                f"✅ <b>Khớp chuẩn (Matched):</b> {len(res.matched)} tập",
                f"⚠️ <b>Có trong History nhưng THIẾU trên Drive:</b> {len(res.missing_on_drive)} tập",
                f"📥 <b>Có trên Drive nhưng THIẾU trong History:</b> {len(res.missing_in_history)} tập",
            ]
            if res.corrupted_on_drive:
                lines.append(f"❌ <b>File hỏng / dưới chuẩn dung lượng (&lt; {self.min_size_mb:.1f} MB):</b> {len(res.corrupted_on_drive)} file")
                sample_c = res.corrupted_on_drive[:max_details]
                for it in sample_c:
                    st_name = it.get('story_folder') or it.get('story_slug') or 'Truyện'
                    ep_str = f"Tập {it['episode_no']}" if it.get('episode_no') is not None else it.get('name', 'N/A')
                    lines.append(f"  • {st_name} — {ep_str} (<code>{it['size_mb']} MB</code> — {it['reason']})")
                if len(res.corrupted_on_drive) > max_details:
                    lines.append(f"  <i>...và còn {len(res.corrupted_on_drive) - max_details} file hỏng nữa.</i>")

            # Chi tiết thiếu trên Drive
            if res.missing_on_drive:
                lines.append("\n⚠️ <b>Chi tiết thiếu trên Drive (Có thể cần tải bù):</b>")
                sample = res.missing_on_drive[:max_details]
                for it in sample:
                    st_name = it.get('story_title') or it.get('slug') or 'Truyện'
                    lines.append(f"  • {st_name} — Tập {it['episode_no']}")

                if len(res.missing_on_drive) > max_details:
                    lines.append(f"  <i>...và còn {len(res.missing_on_drive) - max_details} tập nữa.</i>")

            # Chi tiết thiếu trong history
            if res.missing_in_history:
                lines.append("\n📥 <b>Chi tiết thiếu trong History (Có thể cần đồng bộ):</b>")
                sample = res.missing_in_history[:max_details]
                for it in sample:
                    lines.append(f"  • {it['story_folder']} — Tập {it['episode_no']} (<code>{it['name']}</code>)")
                if len(res.missing_in_history) > max_details:
                    lines.append(f"  <i>...và còn {len(res.missing_in_history) - max_details} tập nữa.</i>")

            # Gợi ý thao tác
            lines.append("\n💡 <i>Gợi ý lệnh bot:</i>")
            if res.corrupted_on_drive:
                lines.append("  👉 Gõ /fix_corrupted để xóa các file hỏng trên Drive và gỡ history để tải lại bản chuẩn")
            if res.missing_in_history:
                lines.append("  👉 Gõ /sync_history để nạp các file trên Drive vào history")
            if res.missing_on_drive:
                lines.append("  👉 Các tập thiếu trên Drive có thể được tải lại khi dọn dẹp history mồ côi.")

            return "\n".join(lines)

        else:
            # Plain text report for console
            sep = "=" * 65
            sub_sep = "-" * 65
            lines = [
                sep,
                "     BÁO CÁO ĐỐI SOÁT GOOGLE DRIVE VS HISTORY.TXT",
                sep,
                f"Thời gian quét       : {res.scan_time:.2f}s",
                f"Ngưỡng kích thước    : >= {self.min_size_mb:.1f} MB (file dưới chuẩn coi là lỗi)",
                f"File audio trên Drive: {res.total_drive_files}",
                f"Bản ghi trong History: {res.total_history_records}",
                f"Số bộ truyện         : {len(res.drive_stories)} (Drive) | {len(res.history_stories)} (History)",
                f"Tỷ lệ khớp chuẩn     : {match_rate:.1f}%\n",
                f"[+] Khớp chuẩn (Matched)            : {len(res.matched)}",
                f"[-] Trong History nhưng THIẾU Drive : {len(res.missing_on_drive)}",
                f"[?] Trên Drive nhưng THIẾU History  : {len(res.missing_in_history)}",
            ]
            if res.corrupted_on_drive:
                lines.append(f"[!] File hỏng / dung lượng < {self.min_size_mb:.1f}MB: {len(res.corrupted_on_drive)}")

            if res.corrupted_on_drive:
                lines.append(f"\n{sub_sep}")
                lines.append(f"DANH SÁCH FILE HỎNG / DƯỚI CHUẨN TRÊN DRIVE ({len(res.corrupted_on_drive)} mục):")
                lines.append(sub_sep)
                for it in res.corrupted_on_drive[:max_details]:
                    st_name = it.get('story_folder') or it.get('story_slug') or 'Truyện'
                    ep_str = f"Tập {it['episode_no']}" if it.get('episode_no') is not None else it.get('name', 'N/A')
                    lines.append(f"  * {st_name} -> {ep_str} | Dung lượng: {it['size_mb']} MB ({it['reason']})")
                if len(res.corrupted_on_drive) > max_details:
                    lines.append(f"  ...và {len(res.corrupted_on_drive) - max_details} mục khác.")

            if res.missing_on_drive:
                lines.append(f"\n{sub_sep}")
                lines.append(f"DANH SÁCH THIẾU TRÊN DRIVE ({len(res.missing_on_drive)} mục):")
                lines.append(sub_sep)
                for it in res.missing_on_drive[:max_details]:
                    st_name = it.get('story_title') or it.get('slug') or 'Truyện'
                    lines.append(f"  * {st_name} -> Tập {it['episode_no']} ({it['history_url']})")

                if len(res.missing_on_drive) > max_details:
                    lines.append(f"  ...và {len(res.missing_on_drive) - max_details} mục khác.")

            if res.missing_in_history:
                lines.append(f"\n{sub_sep}")
                lines.append(f"DANH SÁCH THIẾU TRONG HISTORY ({len(res.missing_in_history)} mục):")
                lines.append(sub_sep)
                for it in res.missing_in_history[:max_details]:
                    lines.append(f"  * {it['story_folder']} -> Tập {it['episode_no']} | File: {it['name']}")
                if len(res.missing_in_history) > max_details:
                    lines.append(f"  ...và {len(res.missing_in_history) - max_details} mục khác.")

            if res.corrupted_on_drive:
                lines.append(f"\n[Gợi ý] Chạy 'python drive_verifier.py --fix-corrupted' để xóa file hỏng trên Drive và gỡ history tải lại.")

            lines.append(sep)
            return "\n".join(lines)


# ─── CLI Entrypoint ──────────────────────────────────────────────────────────

def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Kiểm tra và đối soát tính chuẩn xác giữa Google Drive và history.txt."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Kiểm tra đối soát và in báo cáo ra màn hình (mặc định)."
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Tự động đồng bộ các file từ Drive vào history.txt."
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Tự động loại bỏ khỏi history.txt các mục không tồn tại trên Drive."
    )
    parser.add_argument(
        "--fix-corrupted",
        action="store_true",
        help="Tự động xóa các file audio hỏng (< MIN_SIZE) trên Drive và gỡ khỏi history.txt để tải lại."
    )
    parser.add_argument(
        "--min-size-mb",
        type=float,
        default=MIN_AUDIO_FILE_SIZE_MB,
        help=f"Ngưỡng dung lượng tối thiểu (MB) để coi là file hợp lệ (mặc định: {MIN_AUDIO_FILE_SIZE_MB})."
    )
    parser.add_argument(
        "--remote",
        type=str,
        default=RCLONE_REMOTE_BASE,
        help=f"Rclone remote base path (mặc định: {RCLONE_REMOTE_BASE})"
    )
    parser.add_argument(
        "--history",
        type=str,
        default=str(HISTORY_FILE),
        help=f"Đường dẫn file history.txt (mặc định: {HISTORY_FILE})"
    )
    parser.add_argument(
        "--local-dir",
        type=str,
        default=None,
        help="Đường dẫn thư mục cục bộ để giả lập quét Drive (phục vụ test offline)."
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=15,
        help="Số lượng mục chi tiết tối đa hiển thị trong báo cáo (mặc định: 15)."
    )

    args = parser.parse_args()

    verifier = DriveVerifier(
        remote_base=args.remote,
        history_file=args.history,
        min_size_mb=args.min_size_mb
    )

    print("\n[DriveVerifier] Đang khởi động tiến trình đối soát...")
    result = verifier.reconcile(local_dir=args.local_dir)
    print(verifier.format_report(result, max_details=args.max_details, is_html=False))

    if args.fix_corrupted:
        if result.corrupted_on_drive:
            purged = verifier.purge_corrupted_files(result.corrupted_on_drive, local_dir=args.local_dir)
            print(f"\n=> ĐÃ XỬ LÝ TẬP HỎNG: Đã xóa {purged} file lỗi trên Drive và gỡ bản ghi trong {verifier.history_file}.")
            print("=> Lần chạy pipeline tiếp theo (python main.py) sẽ tự động tải lại các tập này hoàn chỉnh!")
        else:
            print(f"\n=> KHÔNG CÓ FILE HỎNG: Tất cả file trên Drive đều đạt chuẩn dung lượng (>= {args.min_size_mb} MB).")

    if args.sync:
        if result.missing_in_history:
            added = verifier.sync_drive_to_history(result.missing_in_history)
            print(f"\n=> ĐÃ ĐỒNG BỘ: Đã bổ sung {added} bản ghi vào {verifier.history_file}")
        else:
            print("\n=> KHÔNG CẦN ĐỒNG BỘ: Tất cả các file trên Drive đã có đầy đủ trong history.txt.")

    if args.prune:
        if result.missing_on_drive:
            removed = verifier.prune_orphaned_history(result.missing_on_drive)
            print(f"\n=> ĐÃ DỌN DẸP: Đã loại bỏ {removed} bản ghi mồ côi khỏi {verifier.history_file}")
        else:
            print("\n=> KHÔNG CẦN DỌN DẸP: Tất cả các bản ghi trong history đều có file tương ứng trên Drive.")


if __name__ == "__main__":
    main()
