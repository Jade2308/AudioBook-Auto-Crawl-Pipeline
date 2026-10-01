import sys
import logging
from typing import List, Dict, Optional
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class Notifier:
    """
    Module 5: Notifier (Thông báo Telegram)
    Gửi báo cáo tiến trình tự động qua Telegram Bot API (sử dụng requests.post).
    Hỗ trợ 3 loại thông báo: Thành công, Thất bại, hoặc Trạng thái rỗng.
    """
    def __init__(self, bot_token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_message(self, message_text: str, parse_mode: str = "HTML") -> bool:
        """
        Gửi 1 đoạn văn bản tới Telegram Chat ID.
        """
        if not self.bot_token or not self.chat_id:
            logging.warning("[Notifier] Chưa cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID. Thao tác gửi tin nhắn bị bỏ qua.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": message_text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            res = requests.post(self.api_url, json=payload, timeout=15)
            res.raise_for_status()
            data = res.json()
            if data.get("ok"):
                logging.info("[Notifier] Đã gửi thông báo Telegram thành công.")
                return True
            else:
                logging.error(f"[Notifier] Telegram API trả về lỗi: {data}")
                return False
        except Exception as e:
            logging.error(f"[Notifier] Lỗi khi gửi tin nhắn Telegram: {e}")
            return False

    def notify_success(self, success_items: List[Dict[str, str]], failed_count: int = 0) -> bool:
        """
        Báo cáo kết quả tải & upload thành công các tập truyện mới.
        """
        total = len(success_items)
        msg_lines = [
            "✅ <b>[AUDIOBOOK PIPELINE REPORT]</b>",
            f"🎉 <b>Tải & Upload Thành Công:</b> {total} tập",
        ]

        if failed_count > 0:
            msg_lines.append(f"⚠️ <b>Số tập bị lỗi:</b> {failed_count} tập")

        msg_lines.append("\n<b>Danh sách tập đã xử lý:</b>")
        for idx, item in enumerate(success_items[:15], 1): # Báo tối đa 15 tập trong tin nhắn
            msg_lines.append(f"• <i>{item.get('story_title')}</i> - {item.get('episode_title')}")

        if total > 15:
            msg_lines.append(f"<i>...và {total - 15} tập khác.</i>")

        msg_lines.append("\n📁 <i>File đã được tải, chuyển sang M4A và đồng bộ lên Google Drive thành công!</i>")
        return self.send_message("\n".join(msg_lines))

    def notify_failure(self, error_message: str, failed_items: Optional[List[Dict[str, str]]] = None) -> bool:
        """
        Báo cáo tiến trình bị lỗi nghiêm trọng hoặc lỗi từng tập.
        """
        msg_lines = [
            "❌ <b>[AUDIOBOOK PIPELINE ERROR]</b>",
            f"<b>Nội dung lỗi:</b> {error_message}"
        ]

        if failed_items:
            msg_lines.append(f"\n<b>Các tập bị hỏng/lỗi ({len(failed_items)}):</b>")
            for item in failed_items[:10]:
                msg_lines.append(f"• {item.get('story_title', '')} - {item.get('episode_title', '')}")

        msg_lines.append("\n⚠️ <i>Vui lòng kiểm tra lại log hệ thống trên VPS.</i>")
        return self.send_message("\n".join(msg_lines))

    def notify_empty(self) -> bool:
        """
        Thông báo khi không có truyện mới nào cần tải hôm nay.
        """
        msg_lines = [
            "ℹ️ <b>[AUDIOBOOK PIPELINE STATUS]</b>",
            "📌 <b>Trạng thái:</b> Rỗng",
            "Hôm nay không có tập truyện mới nào được phát hiện trên website. Hệ thống sẽ tiếp tục chạy lại theo lịch Cron."
        ]
        return self.send_message("\n".join(msg_lines))

    def notify_episode_success(self, story_title: str, episode_title: str, idx: int, total: int) -> bool:
        """
        Thông báo real-time ngay sau khi upload 1 tập thành công.
        """
        percent = (idx / total) * 100
        msg = (
            f"✅ <b>[UPLOAD THÀNH CÔNG]</b>\n"
            f"📖 <b>Truyện:</b> {story_title}\n"
            f"🎧 <b>Tập:</b> {episode_title}\n"
            f"📊 <b>Tiến trình:</b> {idx}/{total} ({percent:.1f}%)"
        )
        return self.send_message(msg)

    def notify_episode_failure(self, story_title: str, episode_title: str, idx: int, total: int, error: str) -> bool:
        """
        Thông báo real-time ngay khi upload 1 tập bị lỗi.
        """
        percent = (idx / total) * 100
        msg = (
            f"❌ <b>[UPLOAD LỖI]</b>\n"
            f"📖 <b>Truyện:</b> {story_title}\n"
            f"🎧 <b>Tập:</b> {episode_title}\n"
            f"📊 <b>Tiến trình:</b> {idx}/{total} ({percent:.1f}%)\n"
            f"⚠️ <b>Lỗi:</b> <code>{error[:200]}</code>"
        )
        return self.send_message(msg)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    notifier = Notifier(bot_token="SAMPLE_TOKEN", chat_id="SAMPLE_CHAT_ID")
    print("[Module 5 Notifier] Sẵn sàng gửi tin nhắn Telegram API.")
