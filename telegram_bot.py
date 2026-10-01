"""
telegram_bot.py — Module 7: Telegram Bot Controller

Lắng nghe lệnh từ Telegram bằng long-polling (getUpdates API).
Cho phép điều khiển toàn bộ pipeline qua tin nhắn Telegram.

Lệnh hỗ trợ:
    /start   — Chào mừng & hiển thị danh sách lệnh
    /help    — Danh sách lệnh
    /run     — Chạy toàn bộ pipeline (main.py)
    /status  — Xem trạng thái pipeline (đang chạy / idle)
    /log     — Xem 30 dòng log gần nhất của pipeline
    /history — Xem 15 dòng cuối history.txt
    /stop    — Dừng pipeline đang chạy

Bảo mật:
    Chỉ chấp nhận lệnh từ các chat_id trong TELEGRAM_ALLOWED_CHAT_IDS.
"""

import os
import sys
import time
import html
import re
import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    HISTORY_FILE,
    LOGS_DIR,
    PIPELINE_STATE_FILE,
    SCHEDULE_CONFIG_FILE,
    AUTO_RESUME_ON_REBOOT,
    NOTIFY_ON_BOT_STARTUP,
)
from drive_verifier import DriveVerifier
from scheduler import PipelineScheduler


# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─── Hằng số ─────────────────────────────────────────────────────────────────

POLLING_TIMEOUT = 30       # seconds — long-polling timeout mỗi request getUpdates
POLLING_INTERVAL = 1       # seconds — nghỉ giữa các vòng polling
MAX_HISTORY_LINES = 15     # số dòng cuối history.txt sẽ hiển thị

HELP_TEXT = (
    "🤖 <b>AudioBook Pipeline Bot</b>\n\n"
    "<b>Danh sách lệnh:</b>\n"
    "  /run          — Chạy pipeline tải truyện mới\n"
    "  /resume       — Tiếp tục phiên tải (tương đương /run)\n"
    "  /schedule     — Cài đặt & quản lý lịch chạy tự động\n"
    "  /status       — Xem trạng thái pipeline & lịch chạy\n"
    "  /check_drive  — Đối soát file Google Drive vs history.txt\n"
    "  /sync_history — Đồng bộ file Drive vào history.txt\n"
    "  /fix_corrupted — Dọn file hỏng trên Drive & reset để tải lại\n"
    "  /log          — Xem 30 dòng log gần nhất\n"
    "  /logfile      — Tải toàn bộ file log (.log)\n"
    "  /history      — Xem lịch sử đã tải (15 dòng cuối)\n"
    "  /stop         — Dừng pipeline đang chạy\n"
    "  /help         — Hiển thị hướng dẫn này\n\n"
    "💡 <i>Bạn có thể bấm vào nút [Menu] ở góc trái dưới màn hình để chọn lệnh nhanh!</i>"
)

# Gỡ bỏ hoàn toàn bàn phím nút bấm cố định dưới màn hình để tiết kiệm diện tích
REMOVE_KEYBOARD = {"remove_keyboard": True}

# ─── Bot Class ────────────────────────────────────────────────────────────────

class TelegramBotController:
    """
    Telegram Bot Controller dùng requests long-polling.
    Nhận lệnh và điều khiển AudioBook Pipeline.
    """

    def __init__(
        self,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        allowed_chat_ids: Optional[list] = None,
    ):
        if not bot_token:
            raise ValueError(
                "[TelegramBot] TELEGRAM_BOT_TOKEN chưa được cấu hình trong .env"
            )

        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

        # Whitelist chat ID từ config hoặc tham số truyền vào
        if allowed_chat_ids:
            self.allowed_chat_ids = [str(cid) for cid in allowed_chat_ids]
        else:
            raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", TELEGRAM_CHAT_ID)
            self.allowed_chat_ids = [cid.strip() for cid in raw.split(",") if cid.strip()]

        if not self.allowed_chat_ids:
            raise ValueError(
                "[TelegramBot] Chưa cấu hình TELEGRAM_CHAT_ID hoặc TELEGRAM_ALLOWED_CHAT_IDS."
            )

        logger.info(f"[TelegramBot] Whitelist chat IDs: {self.allowed_chat_ids}")

        # Trạng thái pipeline
        self._pipeline_process: Optional[subprocess.Popen] = None
        self._pipeline_lock = threading.Lock()
        self._current_log_path: Optional[Path] = None
        self._pipeline_log_file = None

        # Điều phối Auto-Resume
        self._auto_resume_thread: Optional[threading.Thread] = None
        self._cancel_auto_resume: bool = False

        # Offset để tránh xử lý lại tin cũ
        self._offset: int = 0

        # Scheduler quản lý lịch chạy tự động
        self.scheduler = PipelineScheduler(config_file=SCHEDULE_CONFIG_FILE)
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop_event = threading.Event()

        # Flag dừng bot
        self._running = False

    # ─── Telegram API Helpers ────────────────────────────────────────────────

    def _api_call(self, method: str, payload: dict = None, timeout: int = 15) -> Optional[dict]:
        """Gọi Telegram Bot API, trả về JSON response hoặc None nếu lỗi."""
        url = f"{self.base_url}/{method}"
        try:
            resp = requests.post(url, json=payload or {}, timeout=timeout)
            try:
                data = resp.json()
            except Exception:
                data = {}

            if resp.ok and data.get("ok"):
                return data

            err_desc = data.get("description", f"HTTP {resp.status_code}")
            logger.warning(f"[TelegramBot] API lỗi ({method}): {err_desc}")
            return None
        except requests.exceptions.Timeout:
            return None
        except Exception as e:
            logger.error(f"[TelegramBot] Lỗi kết nối API ({method}): {e}")
            return None

    def _setup_bot_commands(self):
        """Đăng ký menu danh sách lệnh [/] hiển thị trên giao diện chat Telegram."""
        commands = [
            {"command": "run", "description": "🚀 Chạy pipeline tải truyện"},
            {"command": "resume", "description": "▶️ Tiếp tục phiên tải truyện"},
            {"command": "schedule", "description": "⏰ Quản lý lịch chạy tự động"},
            {"command": "status", "description": "📊 Xem trạng thái pipeline & lịch"},
            {"command": "check_drive", "description": "🔍 Đối soát Google Drive vs History"},
            {"command": "sync_history", "description": "🔄 Đồng bộ Drive vào history.txt"},
            {"command": "fix_corrupted", "description": "🛠 Dọn tập hỏng trên Drive để tải lại"},
            {"command": "log", "description": "📄 Xem 30 dòng log gần nhất"},
            {"command": "logfile", "description": "📥 Tải toàn bộ file log (.log)"},
            {"command": "history", "description": "📜 Xem 15 dòng lịch sử tải"},
            {"command": "stop", "description": "🛑 Dừng pipeline đang chạy"},
            {"command": "help", "description": "❓ Hướng dẫn sử dụng"},
        ]
        self._api_call("setMyCommands", {"commands": commands})

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: Optional[str] = "HTML",
        remove_keyboard: bool = True,
    ) -> bool:
        """Gửi tin nhắn tới chat ID. Tự động fallback sang plain text nếu bị lỗi HTML parser."""
        # Giới hạn an toàn chiều dài tin nhắn (Telegram max 4096 ký tự)
        if len(text) > 4000:
            text = text[:3990] + "..."

        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if remove_keyboard:
            payload["reply_markup"] = REMOVE_KEYBOARD

        result = self._api_call("sendMessage", payload)
        if result is not None:
            return True

        # FALLBACK: Nếu HTML parse mode bị lỗi (e.g. 400 Bad Request do thẻ không hợp lệ trong log/history),
        # tự động gỡ HTML tags và gửi lại dưới dạng plain text để đảm bảo tin nhắn KHÔNG BAO GIỜ bị nuốt chửng!
        if parse_mode is not None:
            logger.warning("[TelegramBot] Gửi tin nhắn HTML thất bại, tự động chuyển sang plain text fallback...")
            plain_text = re.sub(r"<[^>]+>", "", text)
            if len(plain_text) > 4000:
                plain_text = plain_text[:3990] + "..."
            payload["text"] = plain_text
            payload.pop("parse_mode", None)
            fallback_res = self._api_call("sendMessage", payload)
            return fallback_res is not None

        return False

    def send_document(
        self,
        chat_id: str,
        file_path: Path,
        caption: str = "",
    ) -> bool:
        """Gửi file đính kèm tới Telegram chat."""
        url = f"{self.base_url}/sendDocument"
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption[:1024]},
                    files={"document": (file_path.name, f, "text/plain")},
                    timeout=30,
                )
                try:
                    data = resp.json()
                except Exception:
                    data = {}

                if resp.ok and data.get("ok"):
                    return True
                logger.warning(f"[TelegramBot] Lỗi sendDocument: {data.get('description', resp.text)}")
                return False
        except Exception as e:
            logger.error(f"[TelegramBot] Ngoại lệ khi gửi document: {e}")
            return False

    def _get_updates(self) -> list:
        """Lấy danh sách update mới bằng long-polling."""
        result = self._api_call(
            "getUpdates",
            {
                "offset": self._offset,
                "timeout": POLLING_TIMEOUT,
                "allowed_updates": ["message"],
            },
            timeout=POLLING_TIMEOUT + 5,
        )
        if result:
            return result.get("result", [])
        return []

    # ─── Authorization ───────────────────────────────────────────────────────

    def _is_authorized(self, chat_id: str) -> bool:
        """Kiểm tra chat_id có trong whitelist không."""
        return str(chat_id) in self.allowed_chat_ids

    # ─── State Persistence & Auto-Resume ───────────────────────────────────────

    def _load_pipeline_state(self) -> dict:
        """Đọc trạng thái pipeline từ file JSON."""
        try:
            if PIPELINE_STATE_FILE.exists():
                with open(PIPELINE_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[TelegramBot] Không thể đọc {PIPELINE_STATE_FILE}: {e}")
        return {}

    def _save_pipeline_state(self, state: dict):
        """Lưu trạng thái pipeline ra file JSON an toàn."""
        try:
            PIPELINE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp_file = PIPELINE_STATE_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            temp_file.replace(PIPELINE_STATE_FILE)
        except Exception as e:
            logger.error(f"[TelegramBot] Lỗi ghi {PIPELINE_STATE_FILE}: {e}")

    def _update_pipeline_state(self, **kwargs):
        """Cập nhật các trường dữ liệu trong file trạng thái."""
        state = self._load_pipeline_state()
        state.update(kwargs)
        self._save_pipeline_state(state)

    def _check_startup_recovery(self):
        """
        Kiểm tra trạng thái khi bot vừa khởi động lại.
        - Nếu phát hiện phiên trước đang 'running' khi VPS bị tắt đột ngột:
          + Báo động ngay lập tức qua Telegram.
          + Tự động tiếp tục chạy (Auto-Resume) nếu AUTO_RESUME_ON_REBOOT=True.
        - Nếu khởi động bình thường:
          + Gửi tin nhắn Bot Online nếu NOTIFY_ON_BOT_STARTUP=True.
        """
        state = self._load_pipeline_state()
        status = state.get("status")

        target_chat_id = state.get("chat_id") or (self.allowed_chat_ids[0] if self.allowed_chat_ids else None)
        if not target_chat_id:
            return

        if status == "running":
            started_at = state.get("started_at", "Không xác định")
            log_file = state.get("log_file", "N/A")
            pid = state.get("pid", "N/A")

            logger.warning(
                f"[TelegramBot] PHÁT HIỆN SỰ CỐ REBOOT! Phiên trước (PID={pid}, bắt đầu={started_at}) chưa hoàn thành."
            )

            # Cập nhật trạng thái thành 'interrupted'
            self._update_pipeline_state(
                status="interrupted",
                interrupted_at=time.strftime("%Y-%m-%d %H:%M:%S")
            )

            alert_msg = (
                "⚠️ <b>CẢNH BÁO: PHÁT HIỆN VPS / BOT VỪA KHỞI ĐỘNG LẠI!</b>\n\n"
                f"🚨 <b>Sự cố:</b> Phiên chạy pipeline trước đó đã bị gián đoạn đột ngột (do VPS restart, OOM hoặc tiến trình bị tắt).\n"
                f"⏱️ <b>Thời gian bắt đầu:</b> <code>{started_at}</code>\n"
                f"📄 <b>File log:</b> <code>{log_file}</code>\n\n"
                "🛡️ <i>Tất cả các tập đã upload trước lúc gián đoạn đều được lưu an toàn trên Drive và trong history.txt.</i>"
            )

            if AUTO_RESUME_ON_REBOOT:
                alert_msg += (
                    "\n\n🔄 <b>Chế độ Auto-Resume: ĐANG BẬT</b>\n"
                    "Bot sẽ tự động tiếp tục chạy pipeline sau <b>10 giây</b> để hoàn tất các tập còn lại...\n"
                    "💡 <i>Gõ /stop nếu bạn muốn hủy kích hoạt lần này.</i>"
                )
                self.send_message(target_chat_id, alert_msg)

                self._cancel_auto_resume = False

                def auto_resume_worker():
                    for _ in range(10):
                        if self._cancel_auto_resume:
                            logger.info("[TelegramBot] Auto-Resume đã bị hủy bởi người dùng.")
                            return
                        time.sleep(1)

                    if self._cancel_auto_resume:
                        return

                    current_st = self._load_pipeline_state().get("status")
                    if current_st in ("interrupted", "idle") and not self._is_pipeline_running():
                        logger.info("[TelegramBot] Bắt đầu Auto-Resume pipeline...")
                        self.send_message(
                            target_chat_id,
                            "🚀 <b>Auto-Resume: Đang tiếp tục chạy pipeline từ điểm bị dừng...</b>"
                        )
                        self._start_pipeline(target_chat_id)

                self._auto_resume_thread = threading.Thread(
                    target=auto_resume_worker,
                    name="AutoResumeWorker",
                    daemon=True
                )
                self._auto_resume_thread.start()
            else:
                alert_msg += (
                    "\n\n👉 <i>Gõ /run hoặc bấm [Menu] để tiếp tục tải các tập còn lại.</i>"
                )
                self.send_message(target_chat_id, alert_msg)

        elif NOTIFY_ON_BOT_STARTUP:
            logger.info("[TelegramBot] Gửi thông báo Bot Online đến admin...")
            sched_status = "🟢 ĐANG BẬT" if self.scheduler.enabled else "🔴 ĐANG TẮT"
            sched_line = f"• Lịch chạy tự động: <b>{sched_status}</b>"
            if self.scheduler.enabled and self.scheduler.next_run:
                try:
                    dt = datetime.fromisoformat(self.scheduler.next_run)
                    sched_line += f" (Lần tới: <code>{dt.strftime('%H:%M %d/%m')}</code>)"
                except Exception:
                    pass
            self.send_message(
                target_chat_id,
                "🤖 <b>Telegram Bot đã trực tuyến!</b>\n"
                "Hệ thống đã khởi động và sẵn sàng nhận lệnh.\n\n"
                f"📊 <b>Cấu hình hiện tại:</b>\n"
                f"{sched_line}\n\n"
                "💡 <i>Gõ /schedule để quản lý lịch hoặc /run để tải ngay.</i>"
            )

    # ─── Pipeline Management ─────────────────────────────────────────────────

    def _is_pipeline_running(self) -> bool:
        """Kiểm tra subprocess pipeline còn đang chạy không."""
        with self._pipeline_lock:
            if self._pipeline_process is None:
                return False
            return self._pipeline_process.poll() is None

    def _start_pipeline(self, chat_id: str):
        """Khởi động main.py như một subprocess độc lập, ghi log ra file."""
        with self._pipeline_lock:
            # Hủy đếm ngược Auto-Resume nếu có
            self._cancel_auto_resume = True

            if self._pipeline_process and self._pipeline_process.poll() is None:
                self.send_message(
                    chat_id,
                    "⚠️ <b>Pipeline đang chạy!</b>\n"
                    "Vui lòng chờ lần chạy hiện tại hoàn thành hoặc dùng /stop để dừng.\n\n"
                    "📄 Dùng /log để xem log hiện tại.",
                )
                return

            main_script = Path(__file__).resolve().parent / "main.py"

            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            start_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
            self._current_log_path = LOGS_DIR / f"pipeline_{timestamp}.log"

            try:
                # Mở file ghi output và đóng handle ở process cha ngay sau khi spawn để tránh leak
                with open(self._current_log_path, "w", encoding="utf-8") as log_file:
                    self._pipeline_process = subprocess.Popen(
                        [sys.executable, "-u", str(main_script)],
                        cwd=str(main_script.parent),
                        stdout=log_file,
                        stderr=log_file,
                    )

                pid = self._pipeline_process.pid

                # Lưu trạng thái 'running' ra file JSON
                self._save_pipeline_state({
                    "status": "running",
                    "pid": pid,
                    "started_at": start_time_str,
                    "chat_id": str(chat_id),
                    "log_file": self._current_log_path.name,
                })

                logger.info(
                    f"[TelegramBot] Pipeline khởi động (PID={pid}). "
                    f"Log: {self._current_log_path}"
                )

                # Watchdog Thread theo dõi khi tiến trình kết thúc
                proc = self._pipeline_process
                def watchdog():
                    ret = proc.wait()
                    with self._pipeline_lock:
                        cur_st = self._load_pipeline_state()
                        if cur_st.get("pid") == pid and cur_st.get("status") == "running":
                            new_st = "completed" if ret == 0 else "failed"
                            self._update_pipeline_state(
                                status=new_st,
                                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                                exit_code=ret
                            )
                            logger.info(
                                f"[TelegramBot] Watchdog: Pipeline (PID={pid}) kết thúc "
                                f"({new_st}, exit_code={ret})."
                            )

                threading.Thread(target=watchdog, name=f"Watchdog_{pid}", daemon=True).start()

                self.send_message(
                    chat_id,
                    f"✅ <b>Pipeline đã khởi động!</b>\n"
                    f"🔄 Đang quét và xử lý truyện mới...\n"
                    f"📊 PID: <code>{pid}</code>\n"
                    f"📁 Log: <code>{self._current_log_path.name}</code>\n\n"
                    f"💬 <i>Gõ /log để theo dõi tiến trình. Bạn sẽ nhận thông báo khi hoàn thành.</i>",
                )
            except Exception as e:
                logger.error(f"[TelegramBot] Không thể khởi động pipeline: {e}")
                self._update_pipeline_state(
                    status="failed",
                    error=str(e),
                    failed_at=time.strftime("%Y-%m-%d %H:%M:%S")
                )
                self.send_message(
                    chat_id,
                    f"❌ <b>Lỗi khởi động pipeline:</b>\n<code>{html.escape(str(e))}</code>",
                )

    def _stop_pipeline(self, chat_id: str):
        """Dừng subprocess pipeline đang chạy."""
        self._cancel_auto_resume = True

        with self._pipeline_lock:
            st = self._load_pipeline_state()
            if st.get("status") in ("running", "interrupted"):
                self._update_pipeline_state(
                    status="stopped",
                    stopped_at=time.strftime("%Y-%m-%d %H:%M:%S")
                )

            if self._pipeline_process is None or self._pipeline_process.poll() is not None:
                self.send_message(chat_id, "ℹ️ Không có pipeline nào đang chạy.")
                return
            try:
                pid = self._pipeline_process.pid
                self._pipeline_process.terminate()
                try:
                    self._pipeline_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._pipeline_process.kill()
                self._update_pipeline_state(
                    status="stopped",
                    stopped_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    exit_code=-9
                )
                logger.warning(f"[TelegramBot] Pipeline (PID={pid}) đã bị dừng theo lệnh người dùng.")
                self.send_message(
                    chat_id,
                    f"🛑 <b>Pipeline đã dừng!</b>\n"
                    f"📊 PID <code>{pid}</code> đã bị terminate.",
                )
            except Exception as e:
                self.send_message(
                    chat_id,
                    f"❌ <b>Lỗi khi dừng pipeline:</b>\n<code>{html.escape(str(e))}</code>",
                )

    def _get_status(self, chat_id: str):
        """Gửi thông báo trạng thái hiện tại của pipeline."""
        state = self._load_pipeline_state()
        st_status = state.get("status", "idle")

        # Tóm tắt thông tin lịch chạy tự động
        if self.scheduler.enabled and self.scheduler.next_run:
            try:
                nxt_dt = datetime.fromisoformat(self.scheduler.next_run)
                from scheduler import _format_time_delta
                rem = _format_time_delta(nxt_dt - datetime.now())
                sched_summary = f"• Lịch tự động: 🟢 ĐANG BẬT — Chạy tiếp: <code>{nxt_dt.strftime('%H:%M %d/%m')}</code> (còn {rem})"
            except Exception:
                sched_summary = "• Lịch tự động: 🟢 ĐANG BẬT"
        else:
            sched_summary = "• Lịch tự động: 🔴 ĐANG TẮT (Gõ /schedule để cài đặt)"

        if self._is_pipeline_running():
            pid = self._pipeline_process.pid
            started_at = state.get("started_at", "N/A")
            log_file = state.get("log_file", "pipeline_latest.log")
            self.send_message(
                chat_id,
                f"🟢 <b>Pipeline đang chạy</b>\n"
                f"📊 PID: <code>{pid}</code>\n"
                f"⏱️ Bắt đầu lúc: <code>{started_at}</code>\n"
                f"📁 Log: <code>{log_file}</code>\n"
                f"⏰ {sched_summary}\n\n"
                f"Gõ /log để xem log tiến trình hoặc /stop để dừng.",
            )
        else:
            status_desc = {
                "completed": "✅ Hoàn thành bình thường",
                "stopped": "🛑 Đã dừng theo yêu cầu (/stop)",
                "interrupted": "⚠️ Bị gián đoạn (VPS reboot/crash)",
                "failed": "❌ Gặp lỗi kết thúc",
                "idle": "Chưa có lượt chạy nào"
            }.get(st_status, st_status)

            last_run_info = ""
            if state.get("started_at"):
                last_run_info = (
                    f"\n\n<b>Thông tin phiên chạy trước:</b>\n"
                    f"• Trạng thái: <b>{status_desc}</b>\n"
                    f"• Bắt đầu: <code>{state.get('started_at')}</code>\n"
                )
                if state.get("finished_at"):
                    last_run_info += f"• Kết thúc: <code>{state.get('finished_at')}</code>\n"
                elif state.get("stopped_at"):
                    last_run_info += f"• Dừng lúc: <code>{state.get('stopped_at')}</code>\n"
                elif state.get("interrupted_at"):
                    last_run_info += f"• Gián đoạn lúc: <code>{state.get('interrupted_at')}</code>\n"
                if state.get("log_file"):
                    last_run_info += f"• File log: <code>{state.get('log_file')}</code>\n"

            self.send_message(
                chat_id,
                f"🔵 <b>Pipeline đang nghỉ (Idle)</b>"
                f"{last_run_info}\n"
                f"⏰ <b>Cấu hình lịch chạy:</b>\n{sched_summary}\n\n"
                f"Gõ /run hoặc bấm [Menu] để bắt đầu tải truyện mới.",
            )

    def _get_history(self, chat_id: str):
        """Gửi N dòng cuối của history.txt."""
        history_path = Path(HISTORY_FILE)
        if not history_path.exists() or history_path.stat().st_size == 0:
            self.send_message(
                chat_id,
                "📂 <b>History rỗng</b>\n"
                "Chưa có tập nào được tải và upload thành công.",
            )
            return

        try:
            lines = history_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            valid_lines = [ln.strip() for ln in lines if ln.strip()]
            last_lines = valid_lines[-MAX_HISTORY_LINES:] if len(valid_lines) > MAX_HISTORY_LINES else valid_lines
            total = len(valid_lines)
            preview = "\n".join(f"• {ln}" for ln in last_lines)
            escaped_preview = html.escape(preview)
            self.send_message(
                chat_id,
                f"📜 <b>History ({len(last_lines)} mục gần nhất / Tổng {total} mục):</b>\n\n"
                f"<pre><code>{escaped_preview}</code></pre>",
            )
        except Exception as e:
            self.send_message(
                chat_id,
                f"❌ <b>Lỗi đọc history.txt:</b>\n<code>{html.escape(str(e))}</code>",
            )

    def _find_latest_log_file(self) -> Optional[Path]:
        """Tìm file log mới nhất có dữ liệu trong thư mục logs/."""
        if self._current_log_path and self._current_log_path.exists() and self._current_log_path.stat().st_size > 0:
            return self._current_log_path

        logs_dir = LOGS_DIR
        if not logs_dir.exists():
            return None

        # 1. Tìm các file .log có dữ liệu (> 0 bytes), sắp xếp theo mtime giảm dần
        log_files = sorted(
            [f for f in logs_dir.glob("*.log") if f.is_file() and f.stat().st_size > 0],
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        if log_files:
            return log_files[0]

        # 2. Nếu chỉ có file rỗng (mới tạo)
        all_logs = sorted(
            [f for f in logs_dir.glob("*.log") if f.is_file()],
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        return all_logs[0] if all_logs else None

    def _get_pipeline_log(self, chat_id: str):
        """Gửi N dòng cuối của file log pipeline gần nhất."""
        MAX_LOG_LINES = 30
        MAX_RAW_CHARS = 2800

        log_path = self._find_latest_log_file()
        if log_path is None or not log_path.exists():
            self.send_message(
                chat_id,
                "📂 <b>Chưa có file log nào.</b>\n"
                "Gõ /run hoặc chọn từ [Menu] để khởi động pipeline trước.",
            )
            return

        self._current_log_path = log_path

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            lines = [line for line in content.splitlines() if line.strip()]
            if not lines:
                if self._is_pipeline_running():
                    self.send_message(
                        chat_id,
                        "⏳ <b>Pipeline đang khởi động!</b>\n"
                        "Đang chờ dữ liệu log đầu tiên, vui lòng thử lại sau vài giây...",
                    )
                else:
                    self.send_message(chat_id, "📄 <b>File log rỗng.</b> Pipeline chưa có output.")
                return

            last_lines = lines[-MAX_LOG_LINES:]
            raw_preview = "\n".join(last_lines)

            if len(raw_preview) > MAX_RAW_CHARS:
                raw_preview = "...(đã lược bớt phần trước)...\n" + raw_preview[-MAX_RAW_CHARS:]

            # html.escape bảo đảm không bao giờ lỗi HTML parser của Telegram
            escaped_preview = html.escape(raw_preview)

            status = "🟢 Đang chạy" if self._is_pipeline_running() else "🔴 Đã kết thúc"
            self.send_message(
                chat_id,
                f"📄 <b>Log Pipeline</b> [{status}]\n"
                f"📁 <code>{log_path.name}</code>\n"
                f"({len(lines)} dòng, hiển thị {len(last_lines)} dòng cuối)\n\n"
                f"<pre><code>{escaped_preview}</code></pre>\n\n"
                f"💡 <i>Gõ /logfile để tải file log đầy đủ (.log)</i>",
            )
        except Exception as e:
            logger.error(f"[TelegramBot] Lỗi đọc file log {log_path}: {e}")
            self.send_message(
                chat_id,
                f"❌ <b>Lỗi đọc log:</b>\n<code>{html.escape(str(e))}</code>",
            )

    def _send_log_file(self, chat_id: str):
        """Gửi toàn bộ file log (.log) dưới dạng tài liệu đính kèm."""
        log_path = self._find_latest_log_file()
        if log_path is None or not log_path.exists():
            self.send_message(
                chat_id,
                "📂 <b>Chưa có file log nào.</b>\n"
                "Gõ /run hoặc chọn từ [Menu] để khởi động pipeline trước.",
            )
            return

        status = "🟢 Đang chạy" if self._is_pipeline_running() else "🔴 Đã kết thúc"
        caption = f"📄 Log Pipeline [{status}] — {log_path.name}"
        success = self.send_document(chat_id, log_path, caption=caption)
        if not success:
            self.send_message(chat_id, f"❌ Không thể gửi file {log_path.name}. Vui lòng thử lại sau.")

    def _handle_check_drive(self, chat_id: str):
        """Chạy đối soát Google Drive vs history trong background thread."""
        self.send_message(
            chat_id,
            "⏳ <b>Đang đọc danh sách file Google Drive và đối soát với history...</b>\n"
            "Vui lòng đợi vài giây, kết quả sẽ được gửi ngay khi hoàn tất.",
        )

        def worker():
            try:
                verifier = DriveVerifier()
                res = verifier.reconcile()
                report_html = verifier.format_report(res, max_details=10, is_html=True)
                self.send_message(chat_id, report_html)
            except Exception as e:
                logger.error(f"[TelegramBot] Lỗi đối soát Drive: {e}")
                self.send_message(
                    chat_id,
                    f"❌ <b>Lỗi khi đối soát Google Drive:</b>\n<code>{html.escape(str(e))}</code>"
                )

        threading.Thread(target=worker, name="DriveCheckWorker", daemon=True).start()

    def _handle_sync_history(self, chat_id: str):
        """Đồng bộ các file có trên Google Drive vào history.txt."""
        self.send_message(
            chat_id,
            "⏳ <b>Đang quét Google Drive để đồng bộ vào history.txt...</b>\n"
            "Tiến trình đang chạy ngầm, vui lòng đợi...",
        )

        def worker():
            try:
                verifier = DriveVerifier()
                res = verifier.reconcile()
                if res.error_message:
                    self.send_message(
                        chat_id,
                        f"❌ <b>Không thể đồng bộ do lỗi đọc Drive:</b>\n<code>{html.escape(res.error_message)}</code>"
                    )
                    return

                if not res.missing_in_history:
                    self.send_message(
                        chat_id,
                        "✅ <b>Không cần đồng bộ!</b>\n"
                        f"Tất cả {res.total_drive_files} file audio trên Drive đã có đầy đủ trong history.txt."
                    )
                    return

                added_count = verifier.sync_drive_to_history(res.missing_in_history, backup=True)
                self.send_message(
                    chat_id,
                    f"🎉 <b>ĐỒNG BỘ THÀNH CÔNG!</b>\n\n"
                    f"• Đã bổ sung: <b>{added_count}</b> tập mới vào history.txt\n"
                    f"• Tổng bản ghi history hiện tại: <b>{res.total_history_records + added_count}</b>\n"
                    f"• File backup an toàn đã được tạo tự động.\n\n"
                    f"💡 <i>Pipeline sẽ không tải lại các tập này trong lần chạy tới.</i>"
                )
            except Exception as e:
                logger.error(f"[TelegramBot] Lỗi đồng bộ Drive -> History: {e}")
                self.send_message(
                    chat_id,
                    f"❌ <b>Lỗi đồng bộ history:</b>\n<code>{html.escape(str(e))}</code>"
                )

        threading.Thread(target=worker, name="DriveSyncWorker", daemon=True).start()

    def _handle_fix_corrupted(self, chat_id: str):
        """Quét và xóa các file audio hỏng / thiếu dung lượng trên Google Drive, gỡ history để tải lại."""
        self.send_message(
            chat_id,
            "⏳ <b>Đang quét tìm các tập audio bị hỏng / thiếu dung lượng trên Google Drive...</b>\n"
            "Vui lòng đợi vài giây...",
        )

        def worker():
            try:
                verifier = DriveVerifier()
                res = verifier.reconcile()
                if res.error_message:
                    self.send_message(
                        chat_id,
                        f"❌ <b>Lỗi quét Drive:</b>\n<code>{html.escape(res.error_message)}</code>"
                    )
                    return

                if not res.corrupted_on_drive:
                    self.send_message(
                        chat_id,
                        f"✅ <b>Không phát hiện tập hỏng nào!</b>\n\n"
                        f"Tất cả <b>{res.total_drive_files}</b> file audio trên Drive đều đạt chuẩn dung lượng "
                        f"(>= {verifier.min_size_mb:.1f} MB)."
                    )
                    return

                corrupted_count = len(res.corrupted_on_drive)
                details = []
                for it in res.corrupted_on_drive[:10]:
                    st_name = it.get('story_folder') or it.get('story_slug') or 'Truyện'
                    ep_str = f"Tập {it['episode_no']}" if it.get('episode_no') is not None else it.get('name', 'N/A')
                    details.append(f"  • {st_name} — {ep_str} ({it['size_mb']} MB - {it['reason']})")
                if corrupted_count > 10:
                    details.append(f"  <i>...và còn {corrupted_count - 10} file hỏng nữa.</i>")

                purged = verifier.purge_corrupted_files(res.corrupted_on_drive, backup=True)

                msg = (
                    f"🛠 <b>ĐÃ XỬ LÝ {purged} TẬP BỊ HỎNG / THIẾU DUNG LƯỢNG!</b>\n\n"
                    f"🔍 <b>Danh sách tập đã dọn dẹp:</b>\n" + "\n".join(details) + "\n\n"
                    f"🗑️ Đã xóa sạch các file lỗi trên Google Drive.\n"
                    f"📜 Đã gỡ bỏ bản ghi tương ứng khỏi history.txt (đã tạo backup an toàn).\n\n"
                    f"👉 <b>Bây giờ bạn chỉ cần gõ /run hoặc bấm [Menu] -> [Chạy pipeline] để tự động tải lại bản chuẩn hoàn chỉnh!</b>"
                )
                self.send_message(chat_id, msg)

            except Exception as e:
                logger.error(f"[TelegramBot] Lỗi khi xử lý file hỏng: {e}")
                self.send_message(
                    chat_id,
                    f"❌ <b>Lỗi khi xử lý file hỏng:</b>\n<code>{html.escape(str(e))}</code>"
                )

        threading.Thread(target=worker, name="DriveFixCorruptedWorker", daemon=True).start()

    # ─── Scheduler Handlers ──────────────────────────────────────────────────

    def _handle_schedule(self, chat_id: str, args_str: str):
        """Xử lý lệnh /schedule và cấu hình lịch chạy tự động."""
        ok, response_msg = self.scheduler.parse_command(args_str, default_chat_id=chat_id)
        self.send_message(chat_id, response_msg)

    def _scheduler_loop(self):
        """Vòng lặp background thread kiểm tra và kích hoạt lịch chạy định kỳ mỗi 10 giây."""
        logger.info("[TelegramBot] Scheduler background loop đã khởi động.")
        while not self._scheduler_stop_event.is_set():
            try:
                trigger_info = self.scheduler.check_trigger()
                if trigger_info:
                    self._handle_schedule_trigger(trigger_info)
            except Exception as e:
                logger.error(f"[TelegramBot] Lỗi trong scheduler loop: {e}")

            # Chờ 10 giây (hoặc dừng sớm nếu nhận stop event)
            self._scheduler_stop_event.wait(timeout=10)

        logger.info("[TelegramBot] Scheduler background loop đã kết thúc.")

    def _handle_schedule_trigger(self, trigger_info: dict):
        """Xử lý khi đến thời điểm kích hoạt từ Scheduler."""
        chat_id = trigger_info.get("chat_id") or (self.allowed_chat_ids[0] if self.allowed_chat_ids else None)
        if not chat_id:
            logger.warning("[TelegramBot] Scheduler trigger nhưng không tìm thấy target chat_id!")
            return

        trigger_time = trigger_info.get("trigger_time", "")
        mode = trigger_info.get("mode", "")

        if self._is_pipeline_running():
            logger.warning(
                f"[TelegramBot] Scheduler kích hoạt lúc {trigger_time} nhưng pipeline đang chạy (PID={self._pipeline_process.pid}). Bỏ qua lần này."
            )
            self.send_message(
                chat_id,
                f"⏰ <b>[Lịch chạy tự động]</b>\n"
                f"Đã đến lịch chạy tự động (<code>{trigger_time}</code>), tuy nhiên pipeline trước đó vẫn đang xử lý "
                f"(PID=<code>{self._pipeline_process.pid}</code>).\n\n"
                f"⚠️ Lần chạy này sẽ được bỏ qua để tránh xung đột dữ liệu."
            )
            return

        logger.info(f"[TelegramBot] Scheduler kích hoạt pipeline lúc {trigger_time} (Mode={mode})")
        self.send_message(
            chat_id,
            f"⏰ <b>[Lịch chạy tự động]</b>\n"
            f"Đã đến khung giờ hẹn: <code>{trigger_time}</code>!\n"
            f"🚀 Bot đang tự động kích hoạt pipeline cào truyện..."
        )
        self._start_pipeline(chat_id)

    # ─── Command Router ───────────────────────────────────────────────────────


    def _handle_update(self, update: dict):
        """Phân tích và xử lý một Telegram update."""
        message = update.get("message")
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        user = message.get("from", {})
        username = user.get("username") or user.get("first_name", "unknown")

        if not chat_id or not text:
            return

        if not self._is_authorized(chat_id):
            logger.warning(
                f"[TelegramBot] Từ chối lệnh từ chat_id={chat_id} (@{username}): '{text}'"
            )
            self.send_message(
                chat_id,
                "🚫 <b>Truy cập bị từ chối.</b>\n"
                "Bot này chỉ dành cho người quản trị được cấp phép.",
            )
            return

        # Chuẩn hóa lệnh hoặc nút bấm
        raw_cmd = text.split("@")[0].strip()
        cmd_lower = raw_cmd.lower()

        # Ánh xạ chữ trên nút bấm thành mã lệnh
        button_map = {
            "🚀 chạy pipeline": "/run",
            "chạy pipeline": "/run",
            "📊 trạng thái": "/status",
            "trạng thái": "/status",
            "🔍 đối soát drive": "/check_drive",
            "đối soát drive": "/check_drive",
            "kiểm tra drive": "/check_drive",
            "check drive": "/check_drive",
            "check_drive": "/check_drive",
            "đối soát": "/check_drive",
            "🔄 đồng bộ history": "/sync_history",
            "đồng bộ history": "/sync_history",
            "sync history": "/sync_history",
            "sync_history": "/sync_history",
            "đồng bộ": "/sync_history",
            "🛠 sửa tập hỏng": "/fix_corrupted",
            "sửa tập hỏng": "/fix_corrupted",
            "sua tap hong": "/fix_corrupted",
            "xóa tập hỏng": "/fix_corrupted",
            "fix corrupted": "/fix_corrupted",
            "fix_corrupted": "/fix_corrupted",
            "tải lại tập hỏng": "/fix_corrupted",
            "📄 xem log": "/log",
            "xem log": "/log",
            "tải log": "/logfile",
            "tải file log": "/logfile",
            "📥 tải file log": "/logfile",
            "logfile": "/logfile",
            "📜 lịch sử": "/history",
            "lịch sử": "/history",
            "🛑 dừng pipeline": "/stop",
            "dừng pipeline": "/stop",
            "▶️ tiếp tục": "/resume",
            "tiếp tục": "/resume",
            "resume": "/resume",
            "chạy tiếp": "/resume",
            "❓ trợ giúp": "/help",
            "trợ giúp": "/help",
            "⏰ lịch chạy": "/schedule",
            "lịch chạy": "/schedule",
            "cài lịch": "/schedule",
            "đặt lịch": "/schedule",
            "xem lịch": "/schedule",
            "lịch": "/schedule",
            "schedule": "/schedule",
            "cron": "/schedule",
            "bật lịch": "/schedule on",
            "tắt lịch": "/schedule off",
            "hẹn giờ": "/schedule",
            "xóa lịch": "/schedule clear",
        }

        command = button_map.get(cmd_lower, cmd_lower)

        # Nhận diện cú pháp tiếng Việt tự nhiên: "cài lịch 02:00, 14:00" hoặc "đặt lịch 02:00"
        if cmd_lower.startswith("cài lịch ") or cmd_lower.startswith("cai lich ") or \
           cmd_lower.startswith("đặt lịch ") or cmd_lower.startswith("dat lich ") or \
           cmd_lower.startswith("hẹn giờ ") or cmd_lower.startswith("hen gio "):
            time_part = re.sub(r"^(cài lịch|cai lich|đặt lịch|dat lich|hẹn giờ|hen gio)\s+", "", raw_cmd, flags=re.IGNORECASE).strip()
            command = f"/schedule set {time_part}"

        logger.info(f"[TelegramBot] Nhận lệnh '{command}' (gốc: '{text}') từ @{username} (chat_id={chat_id})")

        if command in ("/start", "/help"):
            self.send_message(chat_id, HELP_TEXT)

        elif command.startswith("/schedule") or command.startswith("/cron"):
            parts = command.split(maxsplit=1)
            args_str = parts[1] if len(parts) > 1 else ""
            self._handle_schedule(chat_id, args_str)

        elif command in ("/run", "/resume"):
            self._start_pipeline(chat_id)

        elif command == "/status":
            self._get_status(chat_id)

        elif command in ("/check_drive", "/verify"):
            self._handle_check_drive(chat_id)

        elif command in ("/sync_history", "/sync"):
            self._handle_sync_history(chat_id)

        elif command in ("/fix_corrupted", "/fix"):
            self._handle_fix_corrupted(chat_id)

        elif command in ("/log", "log"):
            self._get_pipeline_log(chat_id)

        elif command in ("/logfile", "/log_file", "logfile"):
            self._send_log_file(chat_id)

        elif command == "/history":
            self._get_history(chat_id)

        elif command == "/stop":
            self._stop_pipeline(chat_id)

        else:
            self.send_message(
                chat_id,
                f"❓ Lệnh <code>{html.escape(text)}</code> không được nhận dạng.\n"
                "Bấm vào nút [Menu] hoặc gõ /help để xem hướng dẫn.",
            )

    # ─── Main Polling Loop ────────────────────────────────────────────────────

    def run(self):
        """Bắt đầu vòng lặp long-polling chính."""
        self._running = True
        logger.info("=" * 56)
        logger.info("   TELEGRAM BOT CONTROLLER STARTED")
        logger.info(f"   Allowed Chat IDs: {self.allowed_chat_ids}")
        logger.info("=" * 56)

        try:
            self._setup_bot_commands()
            logger.info("[TelegramBot] Đã đăng ký danh sách menu lệnh với Telegram.")
        except Exception as err:
            logger.warning(f"[TelegramBot] Không thể đăng ký commands menu: {err}")

        # Kiểm tra phục hồi phiên chạy bị gián đoạn do VPS Reboot / Crash
        try:
            self._check_startup_recovery()
        except Exception as err:
            logger.error(f"[TelegramBot] Lỗi kiểm tra startup recovery: {err}")

        # Khởi động thread chạy lịch tự động (Scheduler)
        self._scheduler_stop_event.clear()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="PipelineSchedulerThread",
            daemon=True,
        )
        self._scheduler_thread.start()

        logger.info("[TelegramBot] Đang lắng nghe lệnh từ Telegram...")

        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    update_id = update.get("update_id", 0)
                    self._offset = update_id + 1
                    self._handle_update(update)

                if not updates:
                    time.sleep(POLLING_INTERVAL)

            except KeyboardInterrupt:
                logger.info("[TelegramBot] Nhận tín hiệu dừng (KeyboardInterrupt).")
                self._running = False
                break
            except Exception as e:
                logger.error(f"[TelegramBot] Lỗi trong polling loop: {e}")
                time.sleep(5)

        self._scheduler_stop_event.set()
        logger.info("[TelegramBot] Bot đã dừng.")

    def stop(self):
        """Dừng vòng lặp polling từ bên ngoài."""
        self._running = False
        self._scheduler_stop_event.set()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    bot = TelegramBotController()
    try:
        bot.run()
    except KeyboardInterrupt:
        pass
