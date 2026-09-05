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
| **Module 6** | `main.py` | **Main Controller**: Điều phối tiến trình theo luồng tuyến tính, xử lý ngoại lệ `try...except` và nghỉ 3s giữa các lượt tải. |

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

## 📜 6. Giấy phép & Đóng góp

Dự án phát triển phục vụ mục đích học tập và tự động hóa cá nhân.