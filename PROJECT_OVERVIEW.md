# AudioBook Auto Crawl Pipeline - Tổng Quan Dự Án & Tiến Độ

Dự án này là hệ thống tự động cào (crawl) audio truyện từ trang web [Mê Truyện Audio](https://metruyenaudio.online/), chuyển đổi định dạng M4A, tải lên Google Drive qua `rclone`, dọn dẹp file tạm và gửi báo cáo tự động qua Telegram Bot.

---

## 🎯 1. Kiến trúc Hệ thống (6 Modules Độc lập - OOP)

1. **Module 1: State Tracker (`state_tracker.py`)**
   - Quản lý lịch sử các URL / episode ID đã tải vào file `history.txt`.
   - Cung cấp phương thức `check_downloaded(url)` và `save_downloaded(url)`.

2. **Module 2: Crawler (`crawler.py`)**
   - Đọc danh sách bộ truyện từ `/kham-pha?sort=new`.
   - Bóc tách Next.js Server Components JSON payload từ trang nghe để lấy tên truyện, tên tập và `audioUrl` (`/api/audio/...`).
   - Lọc trùng qua Module 1, hỗ trợ `SESSION_COOKIE` cho tài khoản VIP/Thành viên.

3. **Module 3: Downloader (`downloader.py`)**
   - Sử dụng `subprocess` gọi `yt-dlp` và `ffmpeg` để tải audio stream và ép kiểu sang định dạng `.m4a`.
   - Tự động truyền `--user-agent`, `--referer`, và `Cookie` header.

4. **Module 4: Uploader (`uploader.py`)**
   - Sử dụng `subprocess` gọi `rclone move` chuyển file `.m4a` lên Google Drive vào đúng thư mục tên bộ truyện (`gdrive:Audiobooks/[Tên_Bộ_Truyện]/`).
   - Tự động dọn dẹp file rác cục bộ giải phóng bộ nhớ ổ cứng VPS.

5. **Module 5: Notifier (`notifier.py`)**
   - Kết nối Telegram Bot API qua `requests.post`.
   - Báo cáo 3 loại trạng thái: Thành công (danh sách tập), Thất bại (chi tiết lỗi), hoặc Rỗng (không có tập mới).

6. **Module 6: Main Controller (`main.py`)**
   - Điều phối toàn bộ pipeline theo mô hình Producer-Consumer đa luồng (Queue trung gian).
   - Downloader Producer tải & ép kiểu M4A; Uploader Consumer chuyển file lên Drive và dọn dẹp.
   - Gửi báo cáo chi tiết từng tập và tổng kết toàn đợt qua Telegram.

7. **Module 7: Telegram Bot Controller (`telegram_bot.py` & `run_bot.py`)**
   - Lắng nghe lệnh trực tiếp qua Telegram Long-polling để điều khiển pipeline (/run, /status, /stop, /log, /history).
   - Bảo mật theo whitelist Chat ID.

8. **Module 8: Drive Verifier & Reconciler (`drive_verifier.py`)**
   - Đọc danh sách file audio trên Google Drive qua `rclone lsjson`.
   - Đối soát 2 chiều chuẩn xác với `history.txt`: tìm tập thiếu trên Drive (tải bù) và tập thiếu trong history (tránh tải trùng).
   - Hỗ trợ CLI và lệnh bot: `/check_drive`, `/sync_history`.

9. **Module 9: Pipeline Scheduler (`scheduler.py`)**
   - Quản lý lịch chạy tự động trực tiếp qua Telegram Bot:
     + Chế độ hàng ngày (Daily): chạy theo các khung giờ cố định (`/schedule set 02:00, 14:00`).
     + Chế độ chu kỳ (Interval): lặp lại mỗi N giờ (`/schedule every 6h`).
     + Chế độ hẹn giờ 1 lần (Once): chạy sau N phút (`/schedule in 45m`).
   - Tự động lưu cấu hình ra `schedule_config.json`, tự động phục hồi lịch khi khởi động lại bot hoặc reboot server.
   - Cơ chế phát hiện và phòng chống xung đột khi pipeline đang bận chạy.

---

## ✅ 2. Những Công Việc Đã Làm Được

- [x] Thiết kế hoàn chỉnh cấu trúc dự án chuẩn OOP với 9 tệp Python độc lập và 1 tệp `config.py` tập trung.
- [x] Xây dựng script thử nghiệm `test_crawl.py` cào thành công 121 bộ truyện (7 trang) từ `metruyenaudio.online`.
- [x] Xử lý bóc tách thành công regex Next.js JSON payload để trích xuất `audioUrl` và danh sách chương.
- [x] Mô hình Producer-Consumer tải và upload song song tối ưu băng thông và dung lượng đĩa.
- [x] Xây dựng Telegram Bot tương tác 2 chiều điều khiển pipeline từ xa.
- [x] Xây dựng cơ chế đọc Google Drive và đối soát 2 chiều với `history.txt` (`drive_verifier.py`), kèm lệnh bot `/check_drive` và `/sync_history`.
- [x] Xây dựng hệ thống quản lý lịch chạy tự động thông qua Telegram Bot (`scheduler.py`, lệnh `/schedule`, `/schedule on/off/set/every/in`).
- [x] Tạo tệp cấu hình mẫu `.env.example` và `requirements.txt`.

---

## 📋 3. Những Công Việc Cần Làm Tiếp Theo

### Bước 1: Trích xuất Session Cookie từ Trình duyệt (Bắt buộc để tải Audio)
Do website `metruyenaudio.online` bảo mật endpoint `/api/audio/...` (yêu cầu context phiên đăng nhập), bạn cần lấy Cookie từ trình duyệt:
1. Đăng nhập tài khoản (Miễn phí hoặc VIP) trên trình duyệt Chrome/Edge tại `https://metruyenaudio.online`.
2. Nhấn `F12` -> Chuyển sang tab **Application** (hoặc **Network**).
3. Chọn **Cookies** -> `https://metruyenaudio.online`.
4. Copy toàn bộ chuỗi Cookie (hoặc các cookie quan trọng như `authjs.session-token`, `__Host-authjs.csrf-token`).
5. Dán vào tệp `.env`:
   ```env
   SESSION_COOKIE=authjs.session-token=your_actual_token_here
   ```

### Bước 2: Chạy Thử Nghiệm Tải Trên Máy Cục Bộ (Local Test)
- Để chạy thử trên Windows trước khi đưa lên VPS:
  - Cần cài đặt `ffmpeg` trên Windows (nếu muốn ép kiểu M4A qua yt-dlp).
  - Cấu hình Telegram Token & Chat ID trong `.env` để kiểm tra nhận tin nhắn báo cáo.
  - Chạy thử lệnh: `python main.py`

### Bước 3: Triển khai lên Linux VPS & Cấu hình Cron Job
- Đẩy code lên VPS.
- Cài đặt `ffmpeg`, `yt-dlp`, và `rclone` (`rclone config` liên kết Google Drive).
- Đặt lịch Cron chạy định kỳ hàng ngày:
  ```bash
  0 2 * * * cd /path/to/AudioBook-Auto-Crawl-Pipeline && /usr/bin/python3 main.py >> cron.log 2>&1
  ```

---

## 🤖 4. Hướng Dẫn Cho AI Agent Ở Phiên Làm Việc Kế Tiếp

Khi một AI Agent bắt đầu phiên mới trong repo này, Agent cần:
1. Đọc tệp `PROJECT_OVERVIEW.md` này để nắm toàn bộ bối cảnh dự án.
2. Kiểm tra tệp `.env` xem người dùng đã cấu hình `SESSION_COOKIE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, và `RCLONE_REMOTE_BASE` hay chưa.
3. Hỗ trợ người dùng giải quyết các sự cố liên quan tới `yt-dlp`, `rclone`, hoặc xác thực `Cookie` khi cào dữ liệu.

