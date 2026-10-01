"""
scheduler.py — Module 9: Pipeline Scheduler

Quản lý lịch chạy tự động cho AudioBook Auto Crawl Pipeline:
- Chế độ 1 (Daily): Chạy vào các khung giờ cố định mỗi ngày (ví dụ 02:00, 14:00).
- Chế độ 2 (Interval): Chạy lặp lại sau mỗi N giờ hoặc N phút (ví dụ mỗi 6 giờ).
- Chế độ 3 (Once/Countdown): Hẹn giờ chạy 1 lần sau N phút hoặc N giờ.

Tất cả cấu hình được lưu bền vững vào `schedule_config.json`, tự động khôi phục
ngay cả khi bot restart hoặc server reboot.
"""

import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Union

from config import SCHEDULE_CONFIG_FILE

logger = logging.getLogger(__name__)


def _format_time_delta(td: timedelta) -> str:
    """Định dạng timedelta thành chuỗi tiếng Việt dễ hiểu: X ngày Y giờ Z phút."""
    total_seconds = int(td.total_seconds())
    if total_seconds <= 0:
        return "đang đến hạn"

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} ngày")
    if hours > 0:
        parts.append(f"{hours} giờ")
    if minutes > 0 or not parts:
        parts.append(f"{minutes} phút")

    return " ".join(parts)


class PipelineScheduler:
    """
    Bộ điều phối lịch chạy tự động cho Pipeline.
    Hỗ trợ Daily (HH:MM), Interval (mỗi N giờ), và Once (sau N phút).
    """

    def __init__(self, config_file: Optional[Path] = None):
        self.config_file = Path(config_file or SCHEDULE_CONFIG_FILE)
        self._lock = threading.Lock()

        # Cấu hình mặc định
        self.enabled: bool = False
        self.mode: str = "daily"  # "daily", "interval", "once"
        self.times: List[str] = ["02:00"]
        self.interval_minutes: int = 360  # Mặc định 6 giờ
        self.once_at: Optional[str] = None
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.chat_id: Optional[str] = None

        self.load_config()

    def load_config(self) -> dict:
        """Đọc file cấu hình JSON. Nếu chưa có hoặc lỗi thì dùng mặc định."""
        with self._lock:
            data = {}
            if self.config_file.exists():
                try:
                    with open(self.config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    logger.warning(f"[Scheduler] Không thể đọc {self.config_file}: {e}")

            self.enabled = bool(data.get("enabled", False))
            self.mode = str(data.get("mode", "daily"))
            raw_times = data.get("times", ["02:00"])
            if isinstance(raw_times, list):
                self.times = sorted([str(t).strip() for t in raw_times if str(t).strip()])
            else:
                self.times = ["02:00"]

            self.interval_minutes = int(data.get("interval_minutes", 360))
            self.once_at = data.get("once_at")
            self.last_run = data.get("last_run")
            self.next_run = data.get("next_run")
            self.chat_id = data.get("chat_id")

            # Nếu đang bật mà next_run bị trống hoặc đã quá hạn, tính lại next_run
            if self.enabled:
                now = datetime.now()
                recalc = False
                if not self.next_run:
                    recalc = True
                else:
                    try:
                        dt = datetime.fromisoformat(self.next_run)
                        if dt < now:
                            recalc = True
                    except Exception:
                        recalc = True

                if recalc:
                    next_dt = self._calculate_next_run(now)
                    self.next_run = next_dt.isoformat() if next_dt else None
                    self._save_unlocked()

            return self._to_dict()

    def _save_unlocked(self):
        """Lưu file cấu hình (nội bộ, đã được lock bọc bên ngoài)."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.config_file.with_suffix(".tmp")
            payload = self._to_dict()
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.config_file)
        except Exception as e:
            logger.error(f"[Scheduler] Lỗi lưu cấu hình {self.config_file}: {e}")

    def save_config(self):
        """Lưu cấu hình an toàn với Thread Lock."""
        with self._lock:
            self._save_unlocked()

    def _to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "times": self.times,
            "interval_minutes": self.interval_minutes,
            "once_at": self.once_at,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "chat_id": self.chat_id,
        }

    def _calculate_next_run(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """
        Tính toán thời điểm chạy tiếp theo dựa trên chế độ hiện tại.
        Đảm bảo mốc tính toán phải lớn hơn `now` ít nhất 15 giây để tránh trùng lặp.
        """
        if now is None:
            now = datetime.now()

        buffer = now + timedelta(seconds=15)

        if self.mode == "daily":
            if not self.times:
                return None

            candidates: List[datetime] = []
            # Kiểm tra hôm nay và ngày mai
            for day_offset in (0, 1):
                target_date = (now + timedelta(days=day_offset)).date()
                for t_str in self.times:
                    try:
                        parts = t_str.split(":")
                        h, m = int(parts[0]), int(parts[1])
                        dt = datetime(target_date.year, target_date.month, target_date.day, h, m, 0)
                        if dt > buffer:
                            candidates.append(dt)
                    except Exception:
                        continue

            if candidates:
                candidates.sort()
                return candidates[0]
            return None

        elif self.mode == "interval":
            minutes = max(self.interval_minutes, 5)
            if self.last_run:
                try:
                    last_dt = datetime.fromisoformat(self.last_run)
                    cand = last_dt + timedelta(minutes=minutes)
                    if cand > buffer:
                        return cand
                except Exception:
                    pass
            return now + timedelta(minutes=minutes)

        elif self.mode == "once":
            if self.once_at:
                try:
                    dt = datetime.fromisoformat(self.once_at)
                    if dt > buffer:
                        return dt
                except Exception:
                    pass
            return None

        return None

    def set_daily(self, times_input: Union[str, List[str]], chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Cài đặt chế độ chạy hàng ngày vào các khung giờ cố định.
        Ví dụ: ['02:00', '14:00'] hoặc chuỗi '02:00, 14:00'.
        """
        if isinstance(times_input, str):
            raw_tokens = re.split(r"[,\s;]+", times_input.strip())
        else:
            raw_tokens = list(times_input)

        valid_times = []
        for token in raw_tokens:
            token = token.strip()
            if not token:
                continue
            match = re.match(r"^(\d{1,2}):(\d{2})$", token)
            if not match:
                return False, (
                    f"❌ Định dạng giờ <code>{token}</code> không hợp lệ!\n"
                    f"Vui lòng nhập định dạng <code>HH:MM</code> (ví dụ: <code>02:00</code> hoặc <code>14:30</code>)."
                )
            h, m = int(match.group(1)), int(match.group(2))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return False, f"❌ Giờ <code>{token}</code> nằm ngoài phạm vi 00:00 - 23:59!"
            valid_times.append(f"{h:02d}:{m:02d}")

        if not valid_times:
            return False, "❌ Bạn chưa nhập khung giờ nào! Ví dụ: <code>/schedule set 02:00, 14:00</code>"

        valid_times = sorted(list(set(valid_times)))

        with self._lock:
            self.mode = "daily"
            self.times = valid_times
            self.enabled = True
            if chat_id:
                self.chat_id = str(chat_id)

            next_dt = self._calculate_next_run()
            self.next_run = next_dt.isoformat() if next_dt else None
            self._save_unlocked()

        times_formatted = ", ".join(f"<code>{t}</code>" for t in valid_times)
        next_run_str = next_dt.strftime("%Y-%m-%d %H:%M:%S") if next_dt else "Chưa xác định"
        remaining_str = f" (còn {_format_time_delta(next_dt - datetime.now())})" if next_dt else ""

        return True, (
            f"✅ <b>ĐÃ CÀI ĐẶT LỊCH CHẠY HÀNG NGÀY THÀNH CÔNG!</b>\n\n"
            f"⏰ <b>Khung giờ chạy:</b> {times_formatted} mỗi ngày\n"
            f"🟢 <b>Trạng thái:</b> ĐÃ BẬT\n"
            f"⏭️ <b>Lần chạy tiếp theo:</b> <code>{next_run_str}</code>{remaining_str}\n\n"
            f"💡 <i>Bot sẽ tự động quét và tải truyện mới vào các khung giờ trên.</i>"
        )

    def set_interval(self, hours: float = 0, minutes: int = 0, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Cài đặt chế độ chạy lặp lại sau mỗi chu kỳ N giờ hoặc N phút.
        """
        total_minutes = int(hours * 60 + minutes)
        if total_minutes < 5:
            return False, "❌ Chu kỳ lặp tối thiểu là <b>5 phút</b> để bảo vệ server và tránh bị chặn kết nối."

        with self._lock:
            self.mode = "interval"
            self.interval_minutes = total_minutes
            self.enabled = True
            if chat_id:
                self.chat_id = str(chat_id)

            next_dt = self._calculate_next_run()
            self.next_run = next_dt.isoformat() if next_dt else None
            self._save_unlocked()

        next_run_str = next_dt.strftime("%Y-%m-%d %H:%M:%S") if next_dt else "Chưa xác định"
        remaining_str = f" (còn {_format_time_delta(next_dt - datetime.now())})" if next_dt else ""

        unit_str = f"{total_minutes / 60:.1f}".rstrip("0").rstrip(".") + " giờ" if total_minutes >= 60 else f"{total_minutes} phút"

        return True, (
            f"✅ <b>ĐÃ CÀI ĐẶT CHU KỲ LẶP LẠI THÀNH CÔNG!</b>\n\n"
            f"🔄 <b>Chu kỳ:</b> Chạy lặp lại sau mỗi <b>{unit_str}</b>\n"
            f"🟢 <b>Trạng thái:</b> ĐÃ BẬT\n"
            f"⏭️ <b>Lần chạy tiếp theo:</b> <code>{next_run_str}</code>{remaining_str}\n\n"
            f"💡 <i>Pipeline sẽ tự động khởi động sau mỗi chu kỳ.</i>"
        )

    def set_once(self, minutes: int = 0, hours: float = 0, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Hẹn giờ chạy 1 lần duy nhất sau N phút hoặc N giờ.
        """
        total_minutes = int(hours * 60 + minutes)
        if total_minutes <= 0:
            return False, "❌ Thời gian hẹn giờ phải lớn hơn 0 phút."

        target_dt = datetime.now() + timedelta(minutes=total_minutes)

        with self._lock:
            self.mode = "once"
            self.once_at = target_dt.isoformat()
            self.next_run = target_dt.isoformat()
            self.enabled = True
            if chat_id:
                self.chat_id = str(chat_id)
            self._save_unlocked()

        target_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")
        remaining_str = _format_time_delta(target_dt - datetime.now())

        return True, (
            f"⏱️ <b>ĐÃ HẸN GIỜ CHẠY 1 LẦN THÀNH CÔNG!</b>\n\n"
            f"⏳ <b>Thời gian hẹn:</b> Sau <b>{remaining_str}</b>\n"
            f"⏭️ <b>Thời điểm kích hoạt:</b> <code>{target_str}</code>\n"
            f"🟢 <b>Trạng thái:</b> ĐÃ BẬT\n\n"
            f"💡 <i>Sau khi chạy xong lần này, bot sẽ tự động ngắt hẹn giờ.</i>"
        )

    def enable(self, chat_id: Optional[str] = None) -> Tuple[bool, str]:
        """Bật lịch chạy tự động."""
        with self._lock:
            if self.enabled and self.next_run:
                try:
                    dt = datetime.fromisoformat(self.next_run)
                    rem = _format_time_delta(dt - datetime.now())
                    return True, (
                        f"ℹ️ <b>Lịch chạy đã đang được BẬT sẵn.</b>\n"
                        f"⏭️ Lần chạy tiếp theo: <code>{dt.strftime('%Y-%m-%d %H:%M:%S')}</code> (còn {rem})."
                    )
                except Exception:
                    pass

            self.enabled = True
            if chat_id:
                self.chat_id = str(chat_id)

            next_dt = self._calculate_next_run()
            self.next_run = next_dt.isoformat() if next_dt else None
            self._save_unlocked()

        if next_dt:
            rem = _format_time_delta(next_dt - datetime.now())
            return True, (
                f"🟢 <b>ĐÃ KÍCH HOẠT LỊCH CHẠY TỰ ĐỘNG!</b>\n\n"
                f"⏭️ <b>Lần chạy tiếp theo:</b> <code>{next_dt.strftime('%Y-%m-%d %H:%M:%S')}</code> (còn {rem})\n"
                f"💡 <i>Pipeline sẽ tự động khởi động khi đến giờ hẹn.</i>"
            )
        else:
            return True, (
                f"🟢 <b>ĐÃ BẬT LỊCH CHẠY!</b>\n\n"
                f"⚠️ Hiện tại chưa có mốc giờ chạy hợp lệ.\n"
                f"Vui lòng cài đặt mốc giờ: <code>/schedule set 02:00, 14:00</code>"
            )

    def disable(self) -> Tuple[bool, str]:
        """Tắt lịch chạy tự động."""
        with self._lock:
            if not self.enabled:
                return True, "ℹ️ <b>Lịch chạy hiện tại đã đang TẮT.</b>"

            self.enabled = False
            self.next_run = None
            self._save_unlocked()

        return True, (
            "🔴 <b>ĐÃ TẮT LỊCH CHẠY TỰ ĐỘNG!</b>\n\n"
            "Bot sẽ không tự động kích hoạt pipeline nữa.\n"
            "💡 <i>Bạn vẫn có thể chạy thủ công bằng lệnh /run hoặc bật lại bằng /schedule on.</i>"
        )

    def clear(self) -> Tuple[bool, str]:
        """Xóa cấu hình lịch chạy về mặc định."""
        with self._lock:
            self.enabled = False
            self.mode = "daily"
            self.times = ["02:00"]
            self.interval_minutes = 360
            self.once_at = None
            self.next_run = None
            self._save_unlocked()

        return True, (
            "🗑️ <b>ĐÃ XÓA VÀ ĐẶT LẠI CẤU HÌNH LỊCH!</b>\n\n"
            "Trạng thái đã chuyển về TẮT. Cấu hình mốc giờ được reset về mặc định.\n"
            "💡 <i>Dùng /schedule set 02:00 để cài đặt mốc giờ mới.</i>"
        )

    def get_status_text(self) -> str:
        """Định dạng báo cáo chi tiết trạng thái lịch trình hiện tại (HTML)."""
        with self._lock:
            now = datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")

            status_icon = "🟢" if self.enabled else "🔴"
            status_desc = "ĐANG BẬT" if self.enabled else "ĐANG TẮT"

            if self.mode == "daily":
                times_str = ", ".join(f"<code>{t}</code>" for t in self.times)
                mode_desc = f"Hàng ngày vào các mốc: {times_str}"
            elif self.mode == "interval":
                hrs = self.interval_minutes / 60
                unit_str = f"{hrs:.1f}".rstrip("0").rstrip(".") + " giờ" if self.interval_minutes >= 60 else f"{self.interval_minutes} phút"
                mode_desc = f"Lặp lại chu kỳ mỗi <b>{unit_str}</b>"
            elif self.mode == "once":
                once_dt_str = self.once_at or "N/A"
                mode_desc = f"Hẹn giờ 1 lần lúc: <code>{once_dt_str}</code>"
            else:
                mode_desc = self.mode

            next_info = "Không có (Đang tắt lịch)"
            if self.enabled and self.next_run:
                try:
                    next_dt = datetime.fromisoformat(self.next_run)
                    if next_dt > now:
                        rem = _format_time_delta(next_dt - now)
                        next_info = f"<code>{next_dt.strftime('%Y-%m-%d %H:%M:%S')}</code> (còn <b>{rem}</b>)"
                    else:
                        next_info = f"<code>{next_dt.strftime('%Y-%m-%d %H:%M:%S')}</code> (đang đến hạn)"
                except Exception:
                    next_info = f"<code>{self.next_run}</code>"

            last_info = f"<code>{self.last_run}</code>" if self.last_run else "Chưa có lượt chạy nào"

        return (
            f"⏰ <b>QUẢN LÝ LỊCH CHẠY TỰ ĐỘNG</b>\n\n"
            f"🕒 <b>Giờ hệ thống bot:</b> <code>{now_str}</code>\n"
            f"🔘 <b>Trạng thái:</b> {status_icon} <b>{status_desc}</b>\n"
            f"⚙️ <b>Chế độ:</b> {mode_desc}\n"
            f"⏭️ <b>Lần chạy tiếp theo:</b> {next_info}\n"
            f"⏮️ <b>Lần chạy gần nhất:</b> {last_info}\n\n"
            f"───────────────\n"
            f"📖 <b>CÁC LỆNH ĐIỀU KHIỂN LỊCH:</b>\n"
            f"• <code>/schedule on</code> — Bật lịch chạy tự động\n"
            f"• <code>/schedule off</code> — Tắt lịch chạy tự động\n"
            f"• <code>/schedule set 02:00, 14:00</code> — Cài mốc giờ cố định hàng ngày\n"
            f"• <code>/schedule every 6h</code> — Cài lặp lại mỗi N giờ (hoặc 30m)\n"
            f"• <code>/schedule in 45m</code> — Hẹn chạy 1 lần sau 45 phút (hoặc 2h)\n"
            f"• <code>/schedule clear</code> — Xóa và reset lịch\n\n"
            f"💡 <i>Bạn cũng có thể gõ nhanh: <code>/schedule 02:00, 14:00</code> để đổi giờ!</i>"
        )

    def check_trigger(self) -> Optional[dict]:
        """
        Được gọi bởi background thread định kỳ (ví dụ mỗi 10s).
        Kiểm tra xem thời điểm chạy đã đến chưa.
        Nếu đến -> Cập nhật next_run, last_run và trả về trigger payload.
        """
        with self._lock:
            if not self.enabled or not self.next_run:
                return None

            now = datetime.now()
            try:
                next_dt = datetime.fromisoformat(self.next_run)
            except Exception:
                return None

            if now < next_dt:
                return None

            # Đã đến hạn kích hoạt!
            trigger_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
            mode = self.mode
            chat_id = self.chat_id

            logger.info(
                f"[Scheduler] TRIGGER KÍCH HOẠT! Mode={mode}, Thời điểm={trigger_time_str}, Target ChatID={chat_id}"
            )

            # Cập nhật last_run
            self.last_run = trigger_time_str

            if self.mode == "once":
                # Chạy 1 lần -> Tắt sau khi trigger
                self.enabled = False
                self.next_run = None
            else:
                # Tính tiếp next_run cho chu kỳ tiếp theo
                next_dt = self._calculate_next_run(now)
                self.next_run = next_dt.isoformat() if next_dt else None

            self._save_unlocked()

            return {
                "mode": mode,
                "trigger_time": trigger_time_str,
                "chat_id": chat_id,
            }

    def parse_command(self, args_str: str, default_chat_id: str) -> Tuple[bool, str]:
        """
        Phân tích cú pháp lệnh từ người dùng (/schedule [arguments]).
        """
        raw = args_str.strip()
        if not raw or raw.lower() in ("status", "xem", "info"):
            return True, self.get_status_text()

        parts = raw.split(maxsplit=1)
        subcmd = parts[0].lower()
        subargs = parts[1].strip() if len(parts) > 1 else ""

        if subcmd in ("on", "enable", "bat", "start"):
            return self.enable(chat_id=default_chat_id)

        elif subcmd in ("off", "disable", "tat", "stop"):
            return self.disable()

        elif subcmd in ("clear", "reset", "xoa"):
            return self.clear()

        elif subcmd in ("set", "dat", "cai"):
            if not subargs:
                return False, (
                    "❌ Vui lòng nhập khung giờ cần cài đặt!\n"
                    "Ví dụ: <code>/schedule set 02:00</code> hoặc <code>/schedule set 02:00, 14:00</code>"
                )
            return self.set_daily(subargs, chat_id=default_chat_id)

        elif subcmd in ("every", "loop", "lap", "chu_ky"):
            if not subargs:
                return False, "❌ Vui lòng nhập khoảng thời gian lặp! Ví dụ: <code>/schedule every 6h</code> hoặc <code>/schedule every 30m</code>"
            match = re.match(r"^(\d+(?:\.\d+)?)\s*(h|giờ|gio|m|phút|phut)?$", subargs.lower())
            if not match:
                return False, "❌ Cú pháp không hợp lệ. Ví dụ: <code>/schedule every 6h</code> hoặc <code>/schedule every 45m</code>."
            num = float(match.group(1))
            unit = (match.group(2) or "h").lower()
            if unit in ("m", "phút", "phut"):
                return self.set_interval(minutes=int(num), chat_id=default_chat_id)
            else:
                return self.set_interval(hours=num, chat_id=default_chat_id)

        elif subcmd in ("in", "sau", "once", "hen"):
            if not subargs:
                return False, "❌ Vui lòng nhập thời gian hẹn giờ! Ví dụ: <code>/schedule in 30m</code> hoặc <code>/schedule in 2h</code>"
            match = re.match(r"^(\d+(?:\.\d+)?)\s*(h|giờ|gio|m|phút|phut)?$", subargs.lower())
            if not match:
                return False, "❌ Cú pháp không hợp lệ. Ví dụ: <code>/schedule in 30m</code> hoặc <code>/schedule in 2h</code>."
            num = float(match.group(1))
            unit = (match.group(2) or "m").lower()
            if unit in ("h", "giờ", "gio"):
                return self.set_once(hours=num, chat_id=default_chat_id)
            else:
                return self.set_once(minutes=int(num), chat_id=default_chat_id)

        # Trường hợp người dùng gõ trực tiếp mốc giờ, ví dụ: "/schedule 02:00, 14:00"
        if re.search(r"\d{1,2}:\d{2}", raw):
            return self.set_daily(raw, chat_id=default_chat_id)

        return False, (
            f"❓ Tham số <code>{raw}</code> không được nhận dạng.\n\n"
            f"💡 <b>Gợi ý các lệnh hợp lệ:</b>\n"
            f"• <code>/schedule</code> — Xem trạng thái\n"
            f"• <code>/schedule on</code> — Bật lịch\n"
            f"• <code>/schedule off</code> — Tắt lịch\n"
            f"• <code>/schedule set 02:00, 14:00</code> — Cài giờ hàng ngày\n"
            f"• <code>/schedule every 6h</code> — Lặp lại mỗi 6 giờ\n"
            f"• <code>/schedule in 30m</code> — Chạy sau 30 phút"
        )
