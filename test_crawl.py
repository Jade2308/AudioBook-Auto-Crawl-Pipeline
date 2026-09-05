import sys
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

# Đảm bảo in tiếng Việt chuẩn trên Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://metruyenaudio.online"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def crawl_test():
    print("==================================================")
    print("   BẮT ĐẦU CRAWL THỬ TRÊN METRUYENAUDIO.ONLINE    ")
    print("==================================================\n")
    
    # 1. Quét trang Khám Phá - Tự động quét toàn bộ tất cả các trang
    story_links = []
    page = 1
    
    print(f"[1] Đang tự động quét tất cả các trang danh sách truyện...")
    while True:
        list_url = f"{BASE_URL}/kham-pha?sort=new&page={page}"
        print(f"    -> Đang quét trang {page}: {list_url}")
        try:
            res = requests.get(list_url, headers=headers, timeout=15)
            if res.status_code != 200:
                print(f"       Trang {page} trả về status {res.status_code}. Dừng quét.")
                break

            soup = BeautifulSoup(res.text, 'html.parser')
            
            page_links = 0
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.startswith('/truyen/') and '/nghe/' not in href:
                    full_url = urljoin(BASE_URL, href)
                    if full_url not in story_links:
                        story_links.append(full_url)
                        page_links += 1
            
            if page_links == 0:
                print(f"       Trang {page} không còn truyện mới nào. Đã chạm tới trang cuối.")
                break
                
            print(f"       Tìm thấy thêm {page_links} truyện ở trang {page} (Tích lũy: {len(story_links)} truyện).")
            page += 1
        except Exception as e:
            print(f"       LỖI khi quét trang {page}: {e}")
            break
            
    total_scanned_pages = page - 1 if page > 1 else 1
    print(f"\n    => TỔNG CỘNG HOÀN THÀNH: Tìm thấy {len(story_links)} bộ truyện trên toàn bộ {total_scanned_pages} trang của website!\n")
    if not story_links:
        print("Không tìm thấy bộ truyện nào. Vui lòng kiểm tra lại cấu trúc trang web.")
        return

    # Lấy 3 bộ truyện đầu tiên để crawl thử chi tiết chương
    max_test_stories = min(3, len(story_links))
    print(f"[2] Đang lấy chi tiết chương của {max_test_stories} bộ truyện đầu tiên:")
    
    for i, s_url in enumerate(story_links[:max_test_stories], 1):
        print(f"\n--- Bộ truyện {i}: {s_url} ---")
        try:
            res_story = requests.get(s_url, headers=headers, timeout=15)
            res_story.raise_for_status()
            soup_story = BeautifulSoup(res_story.text, 'html.parser')
            
            # Lấy tên truyện
            h1 = soup_story.find('h1')
            story_title = h1.get_text(strip=True) if h1 else 'Không rõ tên'
            print(f"📖 Tên truyện: {story_title}")
            
            # Tìm link trang nghe đầu tiên
            listen_urls = []
            for a in soup_story.find_all('a', href=True):
                href = a['href']
                if '/nghe/' in href:
                    listen_urls.append(urljoin(BASE_URL, href))
            
            if not listen_urls:
                print("    ⚠️ Không tìm thấy liên kết tập nghe nào trên trang truyện.")
                continue
                
            first_listen = listen_urls[0]
            print(f"🎧 Trang nghe mẫu: {first_listen}")
            
            # Ghé thăm trang nghe và trích xuất danh sách chương + audio link
            res_listen = requests.get(first_listen, headers=headers, timeout=15)
            res_listen.raise_for_status()
            
            # Sử dụng regex tìm cấu trúc chương trong Next.js payload
            chapter_blocks = re.findall(
                r'\{\\?"no\\?":\s*(\d+),\s*\\?"title\\?":\s*\\?"([^"\\]+)\\?",[^\}]*\\?"audioUrl\\?":\s*\\?"([^"\\]+)\\?"',
                res_listen.text
            )
            
            if chapter_blocks:
                print(f"    ✅ Tìm thấy {len(chapter_blocks)} chương:")
                # In ra 5 chương đầu
                for no, title, audio_path in chapter_blocks[:5]:
                    full_audio_url = urljoin(BASE_URL, audio_path)
                    print(f"      - Chương {no}: {title}")
                    print(f"        Link Audio: {full_audio_url}")
                if len(chapter_blocks) > 5:
                    print(f"      ... và {len(chapter_blocks) - 5} chương khác.")
            else:
                # Thử tìm trực tiếp 1 audioUrl đơn lẻ trên trang nghe
                single_audio = re.findall(r'audioUrl\\?":\s*\\?"([^"\\]+)', res_listen.text)
                if single_audio:
                    full_audio_url = urljoin(BASE_URL, single_audio[0])
                    print("    ✅ Tìm thấy 1 chương:")
                    print(f"      - Chương 1: Tập mặc định")
                    print(f"        Link Audio: {full_audio_url}")
                else:
                    print("    ⚠️ Không trích xuất được link audio stream từ trang nghe này.")
                    
        except Exception as e:
            print(f"    ⚠️ Gặp lỗi khi xử lý bộ truyện này: {e}")

if __name__ == '__main__':
    crawl_test()
