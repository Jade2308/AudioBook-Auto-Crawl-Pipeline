import re
import logging
from urllib.parse import urljoin
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup

from config import SITE_BASE_URL, USER_AGENT, MAX_STORIES_TO_CRAWL, MAX_PAGES_TO_CRAWL, SESSION_COOKIE
from state_tracker import StateTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class Crawler:
    """
    Module 2: Crawler (Quét dữ liệu web)
    Bóc tách HTML của metruyenaudio.online để lấy danh sách truyện mới cập nhật
    và danh sách các tập truyện tương ứng. Sử dụng StateTracker để lọc link trùng.
    Hỗ trợ Cookie tài khoản VIP để mở khóa toàn bộ chương truyện.
    """
    def __init__(self, base_url: str = SITE_BASE_URL, user_agent: str = USER_AGENT, session_cookie: str = SESSION_COOKIE):
        self.base_url = base_url.rstrip("/")
        self.headers = {"User-Agent": user_agent}
        if session_cookie.strip():
            self.headers["Cookie"] = session_cookie.strip()
            logging.info("[Crawler] Đã áp dụng VIP Cookie xác thực tài khoản.")
        
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def get_latest_story_urls(self, max_count: int = MAX_STORIES_TO_CRAWL, max_pages: int = MAX_PAGES_TO_CRAWL) -> List[str]:
        """
        Truy cập trang danh sách truyện mới cập nhật (/kham-pha?sort=new) phân trang
        để lấy danh sách URL các bộ truyện.
        Lưu ý: max_pages <= 0 hoặc max_count <= 0 nghĩa là tự động quét cho đến trang cuối cùng.
        """
        story_urls = []
        page = 1
        
        while True:
            # Kiểm tra điều kiện dừng số trang nếu max_pages > 0
            if max_pages > 0 and page > max_pages:
                logging.info(f"[Crawler] Đã đạt giới hạn tối đa {max_pages} trang cấu hình.")
                break

            target_url = f"{self.base_url}/kham-pha?sort=new&page={page}"
            logging.info(f"[Crawler] Đang truy cập chuyên mục truyện mới (Trang {page}): {target_url}")
            
            try:
                res = self.session.get(target_url, timeout=15)
                if res.status_code != 200:
                    logging.info(f"[Crawler] Trang {page} trả về mã {res.status_code}. Dừng quét trang.")
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                
                page_story_urls = []
                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"]
                    # Match links like /truyen/ten-truyen
                    if href.startswith("/truyen/") and "/nghe/" not in href:
                        full_url = urljoin(self.base_url, href)
                        if full_url not in story_urls and full_url not in page_story_urls:
                            page_story_urls.append(full_url)
                
                if not page_story_urls:
                    logging.info(f"[Crawler] Không tìm thấy thêm truyện mới nào ở trang {page}. Đã tới trang cuối cùng.")
                    break
                
                for url in page_story_urls:
                    story_urls.append(url)
                    # Kiểm tra điều kiện giới hạn số truyện nếu max_count > 0
                    if max_count > 0 and len(story_urls) >= max_count:
                        logging.info(f"[Crawler] Đã đạt giới hạn số lượng {max_count} bộ truyện.")
                        break

                if max_count > 0 and len(story_urls) >= max_count:
                    break
                        
            except Exception as e:
                logging.error(f"[Crawler] Lỗi khi quét danh sách truyện mới ở trang {page}: {e}")
                break
                
            page += 1
            
        logging.info(f"[Crawler] Tổng cộng tìm thấy {len(story_urls)} bộ truyện từ {page - 1} trang đã quét.")
        return story_urls

    def parse_story_episodes(self, story_url: str) -> Dict[str, any]:
        """
        Bóc tách chi tiết bộ truyện và danh sách tập truyện (chương).
        Returns:
            dict chứa 'story_title' và danh sách 'episodes'.
        """
        logging.info(f"[Crawler] Đang quét thông tin truyện tại: {story_url}")
        result = {
            "story_title": "Truyện Chưa Đặt Tên",
            "episodes": []
        }
        
        try:
            res = self.session.get(story_url, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "html.parser")
            
            # Lấy tên bộ truyện từ thẻ h1 hoặc meta
            h1 = soup.find("h1")
            if h1 and h1.get_text(strip=True):
                result["story_title"] = h1.get_text(strip=True)

            # Tìm danh sách URL các tập truyện (ví dụ: /truyen/slug/nghe/1)
            listen_urls = []
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if "/nghe/" in href:
                    full_url = urljoin(self.base_url, href)
                    if full_url not in listen_urls:
                        listen_urls.append(full_url)

            if not listen_urls:
                # Trường hợp URL chính nó là URL tập nghe (hoặc dạng rút gọn)
                listen_urls.append(story_url if "/nghe/" in story_url else f"{story_url}/nghe/1")

            # Ghé thăm trang nghe đầu tiên để trích xuất danh sách tất cả các chương & audioUrl
            first_listen_url = listen_urls[0]
            episodes_data = self._extract_chapters_from_listen_page(first_listen_url, result["story_title"])
            result["episodes"] = episodes_data

        except Exception as e:
            logging.error(f"[Crawler] Lỗi khi quét bộ truyện {story_url}: {e}")

        return result

    def _extract_chapters_from_listen_page(self, listen_url: str, default_story_title: str) -> List[Dict[str, str]]:
        """
        Trích xuất thông tin các tập (chương) và audioUrl trực tiếp từ trang nghe.
        """
        episodes = []
        try:
            res = self.session.get(listen_url, timeout=15)
            res.raise_for_status()
            html_text = res.text

            # Regex trích xuất danh sách chương từ Next.js Server Components / Props JSON payload
            # Format pattern: {"no": 1, "title": "Chương 1", ..., "audioUrl": "/api/audio/..."}
            chapter_matches = re.findall(
                r'\{\\?"no\\?":\s*(\d+),\s*\\?"title\\?":\s*\\?"([^"\\]+)\\?",[^\}]*\\?"audioUrl\\?":\s*\\?"([^"\\]+)\\?"',
                html_text
            )

            if chapter_matches:
                for no, title, audio_path in chapter_matches:
                    full_audio_url = urljoin(self.base_url, audio_path)
                    ep_url = f"{listen_url.rsplit('/nghe/', 1)[0]}/nghe/{no}"
                    episodes.append({
                        "episode_no": int(no),
                        "episode_title": title.strip() or f"Tập {no}",
                        "episode_url": ep_url,
                        "audio_url": full_audio_url,
                        "unique_id": ep_url
                    })
            else:
                # Dự phòng: tìm trực tiếp 1 audioUrl duy nhất trên trang
                audio_matches = re.findall(r'audioUrl\\?":\s*\\?"([^"\\]+)', html_text)
                if audio_matches:
                    full_audio_url = urljoin(self.base_url, audio_matches[0])
                    episodes.append({
                        "episode_no": 1,
                        "episode_title": "Tập 1",
                        "episode_url": listen_url,
                        "audio_url": full_audio_url,
                        "unique_id": listen_url
                    })

        except Exception as e:
            logging.error(f"[Crawler] Lỗi trích xuất tập từ trang nghe {listen_url}: {e}")

        return episodes

    def fetch_new_episodes(self, state_tracker: Optional[StateTracker] = None) -> List[Dict[str, str]]:
        """
        Tổng hợp tất cả truyện mới, bóc tách các tập và lọc bỏ các tập đã tải.
        Returns:
            Danh sách các dict chứa thông tin tập mới chưa được tải.
        """
        story_urls = self.get_latest_story_urls()
        new_items = []

        for s_url in story_urls:
            story_info = self.parse_story_episodes(s_url)
            story_title = story_info["story_title"]

            for ep in story_info["episodes"]:
                unique_id = ep["unique_id"]
                # Kiểm tra với StateTracker nếu có truyền vào
                if state_tracker and state_tracker.check_downloaded(unique_id):
                    logging.info(f"[Crawler] Đã bỏ qua tập trùng lặp ({story_title} - {ep['episode_title']}): {unique_id}")
                    continue

                new_items.append({
                    "story_title": story_title,
                    "episode_title": ep["episode_title"],
                    "episode_url": ep["episode_url"],
                    "audio_url": ep["audio_url"],
                    "unique_id": unique_id
                })

        logging.info(f"[Crawler] Hoàn tất quét. Tổng số tập mới cần tải: {len(new_items)}")
        return new_items


if __name__ == "__main__":
    # Test Module 2
    crawler = Crawler()
    tracker = StateTracker("test_crawler_history.txt")
    items = crawler.fetch_new_episodes(state_tracker=tracker)
    print(f"\n--- Tìm thấy {len(items)} tập mới ---")
    for item in items[:5]:
        print(f"Bộ truyện: {item['story_title']}")
        print(f"Tập: {item['episode_title']}")
        print(f"Audio URL: {item['audio_url']}")
        print("---")
