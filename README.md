# AudioBook-Auto-Crawl-Pipeline

Hệ thống tự động cào (crawl) audio truyện từ website [Mê Truyện Audio](https://metruyenaudio.online/), chuyển đổi định dạng M4A, phân loại và tải lên Google Drive qua Rclone, dọn dẹp bộ nhớ cục bộ và gửi báo cáo qua Telegram Bot.

---

## 📌 1. Cấu trúc Hệ thống (6 Modules Độc lập)

Hệ thống được thiết kế chuẩn hướng đối tượng (OOP) và module hóa thành 6 thành phần chính:

| Module | Tên File | Nhiệm vụ |
| :--- | :--- | :--- |
| **Module 1** | `state_tracker.py` | **State Tracker**: Quản lý lịch sử các URL đã tải vào `history.txt` để chống tải trùng lặp. |
| **Module 2** | `crawler.py` | **Crawler**: Truy cập trang "Truyện mới cập nhật", bóc tách HTML/JSON để lấy Tên bộ truyện, Tên tập và URL audio stream. |
| **Module 3** | `downloader.py` | **Downloader**: Gọi `yt-dlp` kết hợp `ffmpeg` qua `subprocess` để tải và ép kiểu âm thanh sang định dạng `.m4a`. |
| **Module 4** | `uploader.py` | **Uploader**: Gọi `rclone move` qua `subprocess` để đẩy file lên Google Drive theo cấu trúc thư mục bộ truyện và tự động xóa file tạm giải phóng ổ cứng. |
| **Module 5** | `notifier.py` | **Notifier**: Kết nối Telegram Bot API (`requests.post`) để gửi báo cáo trạng thái (Thành công, Thất bại, hoặc Rỗng). |
| **Module 6** | `main.py` | **Main Controller**: Điều phối tiến trình theo mô hình Producer-Consumer đa luồng (Queue trung gian) tối ưu hiệu năng. |
| **Module 7** | `telegram_bot.py` | **Telegram Bot Controller**: Nhận lệnh điều khiển từ xa qua Telegram (`/run`, `/status`, `/stop`, `/log`, `/history`). |
| **Module 8** | `drive_verifier.py` | **Drive Verifier**: Đọc Google Drive qua `rclone`, đối soát 2 chiều với `history.txt`, hỗ trợ đồng bộ (`/check_drive`, `/sync_history`). |

---

## 🛠️ 2. Yêu cầu Hệ thống & Đơn vị phụ thuộc

- **OS Target**: Linux VPS (Ubuntu/Debian) hoặc Windows/macOS.
- **Python**: 3.9+
- **Công cụ hệ thống bắt buộc**:
  - `ffmpeg` (Dùng cho `yt-dlp` xử lý/chuyển đổi audio sang `.m4a`).
  - `yt-dlp` (Tải media stream).
  - `rclone` (Đẩy file lên Google Drive).

---

## 🚀 3. Hướng dẫn Cài đặt & Cấu hình trên Linux VPS

### Bước 1: Cài đặt công cụ hệ thống

```bash
# Cập nhật hệ thống
sudo apt update && sudo apt install -y python3 python3-pip ffmpeg rclone curl

# Tải bản yt-dlp mới nhất
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
```

### Bước 2: Cấu hình Rclone với Google Drive

Chạy lệnh cấu hình rclone trên VPS:
```bash
rclone config
```
1. Chọn `n` (New remote).
2. Đặt tên remote: `gdrive`.
3. Chọn type: `drive` (Google Drive).
4. Làm theo hướng dẫn xác thực tài khoản Google Drive của bạn.
5. Kiểm tra kết nối thành công bằng lệnh: `rclone lsd gdrive:`

### Bước 3: Cài đặt thư viện Python & Cấu hình biến môi trường

```bash
# Clone hoặc copy mã nguồn dự án vào VPS
cd /path/to/AudioBook-Auto-Crawl-Pipeline

# Cài đặt các thư viện Python
pip3 install -r requirements.txt

# Tạo file cấu hình môi trường .env từ mẫu .env.example
cp .env.example .env
```

Chỉnh sửa file `.env` bằng `nano .env`:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=987654321
RCLONE_REMOTE_BASE=gdrive:Audiobooks
CRAWL_DELAY=3

# Cấu hình quét sâu nhiều trang truyện (Phục vụ crawl lần đầu)
MAX_PAGES_TO_CRAWL=2       # Số trang muốn quét qua (mỗi trang có 20 bộ truyện mới)
MAX_STORIES_TO_CRAWL=40    # Số bộ truyện tối đa muốn xử lý
```

---

## 🧪 4. Chạy Thử nghiệm (Manual Run)

Bạn có thể chạy trực tiếp pipeline để kiểm tra:

```bash
python3 main.py
```

Luồng chạy:
1. `main.py` gọi Crawler tìm truyện mới.
2. Kiểm tra `history.txt`, lọc ra các tập chưa tải.
3. Tải audio stream -> Convert M4A -> Rclone Move lên Drive `gdrive:Audiobooks/[Tên_Bộ_Truyện]/` -> Xóa file tạm -> Ghi `history.txt`.
4. Nghỉ 3 giây giữa các tập.
5. Gửi thông báo tổng hợp về Telegram.

---

## ⏰ 5. Cấu hình Chạy Tự động Hàng ngày (Cron Job)

Mở bảng cấu hình cron:
```bash
crontab -e
```

Thêm dòng sau để chạy tự động lúc **02:00 sáng mỗi ngày**:
```bash
0 2 * * * cd /path/to/AudioBook-Auto-Crawl-Pipeline && /usr/bin/python3 main.py >> cron.log 2>&1
```

*(Lưu ý: Thay `/path/to/AudioBook-Auto-Crawl-Pipeline` bằng đường dẫn tuyệt đối thư mục dự án của bạn trên VPS).*

---

## 🔍 6. Đối Soát Google Drive Với History (`drive_verifier.py`)

Hệ thống cung cấp cơ chế quét thư mục Google Drive qua `rclone`, bóc tách số tập và đối soát 2 chiều với `history.txt`:
- **Phát hiện tập có trong history nhưng THIẾU trên Drive:** Giúp xác định các tập cần tải bù.
- **Phát hiện tập có trên Drive nhưng THIẾU trong history:** Giúp nạp vào history để không bị tải lặp khi dọn file history.

### Các lệnh CLI hỗ trợ:
```bash
# 1. Kiểm tra đối soát và xuất báo cáo chi tiết
python3 drive_verifier.py --check

# 2. Tự động đồng bộ các file từ Google Drive vào history.txt (tự động tạo backup .bak)
python3 drive_verifier.py --sync

# 3. Loại bỏ khỏi history.txt những mục không có file trên Drive (để pipeline tải bù)
python3 drive_verifier.py --prune

# 4. Kiểm tra giả lập với thư mục cục bộ (dành cho test offline)
python3 drive_verifier.py --check --local-dir ./temp_downloads
```

### Điều khiển qua Telegram Bot:
Bạn có thể chạy trực tiếp từ Telegram:
- `/check_drive`: Chạy đối soát ngầm và gửi bảng báo cáo thống kê qua chat.
- `/sync_history`: Đồng bộ file Drive vào `history.txt` trực tiếp qua 1 chạm.

---

## 🤖 7. Điều Khiển Qua Telegram Bot (`telegram_bot.py`)

Khởi động bot thường trực (daemon):
```bash
python3 run_bot.py
```
Các lệnh bot hỗ trợ:
- `/run` — Khởi động pipeline cào truyện ngầm.
- `/schedule` — Cài đặt & quản lý lịch chạy tự động trực tiếp trên Telegram:
  + `/schedule` — Xem trạng thái & thời gian chạy tiếp theo.
  + `/schedule on` / `/schedule off` — Bật / tắt lịch chạy tự động.
  + `/schedule set 02:00, 14:00` — Cài đặt các mốc giờ chạy cố định hàng ngày (HH:MM).
  + `/schedule every 6h` — Cài đặt chạy lặp lại sau mỗi chu kỳ N giờ (hoặc 30m).
  + `/schedule in 45m` — Hẹn giờ chạy 1 lần duy nhất sau N phút (hoặc 2h).
  + `/schedule clear` — Xóa và reset cấu hình lịch về mặc định.
- `/status` — Kiểm tra trạng thái pipeline & thông tin lịch chạy tự động.
- `/check_drive` — Đối soát file Google Drive với history.txt.
- `/sync_history` — Nạp các file trên Drive vào history.txt.
- `/fix_corrupted` — Dọn tập hỏng/thiếu dung lượng trên Drive để tải lại.
- `/log` — Xem 30 dòng log mới nhất thời gian thực.
- `/logfile` — Tải toàn bộ file `.log` về máy.
- `/history` — Xem danh sách 15 tập gần nhất đã tải.
- `/stop` — Dừng pipeline khẩn cấp.

---

## 📜 8. Giấy phép & Đóng góp

Dự án phát triển phục vụ mục đích học tập và tự động hóa cá nhân.