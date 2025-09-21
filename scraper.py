import requests
from bs4 import BeautifulSoup
import json
import os
import time
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

# --- Konfigurasi ---
BASE_URL = "https://aniwatchtv.to"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': BASE_URL
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def sanitize_key(text):
    """Membersihkan teks untuk dijadikan kunci JSON yang valid."""
    text = text.lower()
    text = text.replace(' ', '_')
    # Hapus semua karakter yang bukan huruf, angka, atau underscore
    text = re.sub(r'[^a-z0-9_]', '', text)
    return text

def fetch_with_retry(url, retries=3, backoff_factor=0.8):
    """Mencoba mengambil URL beberapa kali jika terjadi error server."""
    for i in range(retries):
        try:
            response = SESSION.get(url, timeout=20)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            if e.response.status_code in [503, 500, 502, 504]:
                wait_time = backoff_factor * (2 ** i)
                print(f"  -> Server error {e.response.status_code} for {url}. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
            else:
                return None
        except requests.exceptions.RequestException:
            wait_time = backoff_factor * (2 ** i)
            time.sleep(wait_time)
    
    return None

def _parse_watch_page(soup, detail_url):
    """Helper untuk mem-parsing data dari halaman /watch (data lengkap)."""
    title_element = soup.select_one('.anisc-detail .film-name a')
    title = title_element.get_text(strip=True) if title_element else None
    if not title: return None

    description_element = soup.select_one('.film-description .text')
    description = description_element.get_text(strip=True).replace("... + More", "").strip() if description_element else "No description available."
    
    poster_element = soup.select_one('.anisc-poster .film-poster-img')
    image_url = poster_element['src'] if poster_element and poster_element.has_attr('src') else None

    iframe_element = soup.select_one('iframe#iframe-embed')
    streaming_url = iframe_element['src'] if iframe_element and iframe_element.has_attr('src') else None

    servers = {'sub': [], 'dub': []}
    server_blocks = soup.select('.ps_-block')
    for block in server_blocks:
        server_type = 'dub' if 'servers-dub' in block.get('class', []) else 'sub'
        server_items = block.select('.server-item')
        for item in server_items:
            servers[server_type].append({'name': item.get_text(strip=True), 'data_id': item.get('data-id')})

    episodes = []
    episode_list_items = soup.select('.ss-list a.ssl-item.ep-item')
    for item in episode_list_items:
        episodes.append({
            'episode_number': item.get('data-number'),
            'title': item.select_one('.ep-name').get_text(strip=True),
            'url': urljoin(BASE_URL, item['href'])
        })
    
    return {
        'title': title, 'detail_page_url': urljoin(BASE_URL, detail_url), 'image_url': image_url,
        'description': description, 'streaming_iframe_url': streaming_url,
        'servers': servers, 'episodes': episodes
    }

def _parse_detail_page(soup, detail_url):
    """Fallback untuk mem-parsing data dari halaman detail (data terbatas)."""
    title_element = soup.select_one('.anisc-detail .film-name a')
    title = title_element.get_text(strip=True) if title_element else None
    if not title: return None

    description_element = soup.select_one('.anisc-detail .film-description')
    description = description_element.get_text(strip=True).replace("...Read more", "").strip() if description_element else "No description available."
    
    poster_element = soup.select_one('.anisc-poster .film-poster-img')
    image_url = poster_element['src'] if poster_element and poster_element.has_attr('src') else None

    return {
        'title': title, 'detail_page_url': urljoin(BASE_URL, detail_url), 'image_url': image_url,
        'description': description, 'streaming_iframe_url': None,
        'servers': {'sub': [], 'dub': []}, 'episodes': []
    }

def scrape_anime_data(detail_url):
    """
    Logika utama: Coba ambil dari halaman /watch, jika 404, ambil dari halaman detail.
    """
    # 1. Coba halaman tonton (/watch/...) untuk data lengkap
    watch_url = urljoin(BASE_URL, f"/watch{detail_url.split('?')[0]}")
    response = fetch_with_retry(watch_url)

    if response:
        try:
            soup = BeautifulSoup(response.content, 'html.parser')
            data = _parse_watch_page(soup, detail_url)
            if data: return data
        except Exception as e:
            print(f"  -> Error parsing watch page {watch_url}: {e}")

    # 2. Jika halaman /watch gagal atau tidak ada (404), beralih ke halaman detail
    # print(f"  -> Watch page failed for {detail_url}. Falling back to detail page.")
    detail_page_url = urljoin(BASE_URL, detail_url)
    detail_response = fetch_with_retry(detail_page_url)
    if detail_response:
        try:
            detail_soup = BeautifulSoup(detail_response.content, 'html.parser')
            return _parse_detail_page(detail_soup, detail_url)
        except Exception as e:
            print(f"  -> Error parsing detail page {detail_page_url}: {e}")
    
    return None

def scrape_homepage():
    """Mengambil semua kategori dan URL anime dari halaman utama."""
    print("Starting homepage scrape...")
    home_url = urljoin(BASE_URL, "/home")
    response = fetch_with_retry(home_url)
    if not response:
        print("Fatal: Failed to fetch homepage. Exiting.")
        return {}, []

    soup = BeautifulSoup(response.content, 'html.parser')
    unique_detail_urls = set()
    sections = {}

    all_sections = soup.select('section.block_area, #anime-featured, .deslide-wrap')
    
    for section in all_sections:
        header_element = section.select_one('.bah-heading h2.cat-heading, .anif-block-header')
        header_text = 'Spotlight' if section.select_one('#slider') else header_element.get_text(strip=True) if header_element else None
        if not header_text: continue

        key = sanitize_key(header_text)
        items = section.select('.flw-item, .deslide-item, .item-qtip, .anif-block li')
        if not items: continue

        section_urls = []
        for item in items:
            link_element = item.select_one('a.film-poster-ahref, .desi-buttons a.btn-secondary, a.film-poster')
            if link_element and link_element.has_attr('href'):
                href = link_element['href'].replace('/watch/', '/')
                if href not in section_urls:
                    section_urls.append(href)
        
        if section_urls:
            unique_detail_urls.update(section_urls)
            sections[key] = section_urls
            
    print(f"Found {len(unique_detail_urls)} unique anime entries to process.")
    return sections, list(unique_detail_urls)

def main():
    """Fungsi utama untuk menjalankan seluruh proses scraper."""
    sections, detail_urls = scrape_homepage()
    if not detail_urls: return

    all_anime_details = {}
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {executor.submit(scrape_anime_data, url): url for url in detail_urls}
        
        for i, future in enumerate(as_completed(future_to_url)):
            url = future_to_url[future]
            print(f"({i+1}/{len(detail_urls)}) Processing: {url}", end="")
            try:
                data = future.result()
                if data and data.get('title') and data.get('title') != "N/A":
                    all_anime_details[url] = data
                    print(f" -> ✓ Success: {data['title']}")
                else:
                    print(f" -> ✗ Failed or no data found.")
            except Exception as exc:
                print(f" -> ✗ Exception: {exc}")
            
            time.sleep(random.uniform(0.4, 0.8)) # Jeda yang lebih 'sopan'

    final_data = {}
    for section_name, urls in sections.items():
        # Masukkan data ke section-nya masing-masing
        section_data = [all_anime_details[url] for url in urls if url in all_anime_details]
        # Hanya tambahkan section ke output jika berisi data
        if section_data:
            final_data[section_name] = section_data

    file_path = os.path.join(os.path.dirname(__file__), 'anime_data.json')
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)

    print(f"\nScraping complete. Data saved to {file_path}")

if __name__ == '__main__':
    main()
