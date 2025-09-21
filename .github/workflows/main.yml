import json
import os
import time
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- Konfigurasi ---
BASE_URL = "https://aniwatchtv.to"

def sanitize_key(text):
    """Membersihkan teks untuk dijadikan kunci JSON yang valid."""
    text = text.lower()
    text = text.replace(' ', '_')
    text = re.sub(r'[^a-z0-9_]', '', text)
    return text

def scrape_anime_data(url, context):
    """
    Mengambil data dari URL anime menggunakan instance browser Playwright.
    Mencoba halaman /watch, jika gagal, beralih ke halaman detail.
    """
    page = None
    try:
        # Prioritaskan halaman /watch untuk data lengkap
        watch_url = urljoin(BASE_URL, f"/watch{url.split('?')[0]}")
        page = context.new_page()
        page.goto(watch_url, timeout=45000, wait_until='domcontentloaded')

        # Cek jika dialihkan ke halaman 404
        if "404" in page.title() or "Page not found" in page.inner_text('body'):
            raise PlaywrightTimeoutError("Navigated to 404 page")

        # Parsing Halaman Tonton (Watch Page)
        title = page.locator('.anisc-detail .film-name a').inner_text(timeout=5000)
        description = page.locator('.film-description .text').inner_text(timeout=5000).replace("... + More", "").strip()
        image_url = page.locator('.anisc-poster .film-poster-img').get_attribute('src', timeout=5000)
        streaming_url = page.locator('iframe#iframe-embed').get_attribute('src', timeout=5000)
        
        # Ekstrak server
        servers = {'sub': [], 'dub': []}
        for server_block in page.locator('.ps_-block').all():
            server_type = 'dub' if 'servers-dub' in server_block.get_attribute('class', '') else 'sub'
            for item in server_block.locator('.server-item').all():
                servers[server_type].append({'name': item.inner_text(), 'data_id': item.get_attribute('data-id')})
        
        # Ekstrak episode
        episodes = []
        for item in page.locator('.ss-list a.ssl-item.ep-item').all():
            episodes.append({
                'episode_number': item.get_attribute('data-number'),
                'title': item.locator('.ep-name').inner_text(),
                'url': urljoin(BASE_URL, item.get_attribute('href'))
            })

        return {
            'title': title, 'detail_page_url': urljoin(BASE_URL, url), 'image_url': image_url,
            'description': description, 'streaming_iframe_url': streaming_url,
            'servers': servers, 'episodes': episodes
        }

    except (PlaywrightTimeoutError, Exception) as e:
        # Jika halaman /watch gagal, coba halaman detail
        if page: page.close()
        page = context.new_page()
        try:
            detail_url = urljoin(BASE_URL, url)
            page.goto(detail_url, timeout=45000, wait_until='domcontentloaded')
            
            title = page.locator('.anisc-detail .film-name a').inner_text(timeout=5000)
            description = page.locator('.anisc-detail .film-description').inner_text(timeout=5000).replace("...Read more", "").strip()
            image_url = page.locator('.anisc-poster .film-poster-img').get_attribute('src', timeout=5000)

            return {
                'title': title, 'detail_page_url': detail_url, 'image_url': image_url,
                'description': description, 'streaming_iframe_url': None,
                'servers': {'sub': [], 'dub': []}, 'episodes': []
            }
        except Exception as detail_e:
            return None # Gagal di kedua halaman
    finally:
        if page:
            page.close()

def scrape_homepage(page):
    """Mengambil semua kategori dan URL anime dari halaman utama."""
    print("Starting homepage scrape...")
    page.goto(urljoin(BASE_URL, "/home"), timeout=60000, wait_until='domcontentloaded')

    unique_detail_urls = set()
    sections = {}

    all_sections = page.locator('section.block_area, #anime-featured, .deslide-wrap').all()
    
    for section in all_sections:
        header_element = section.locator('.bah-heading h2.cat-heading, .anif-block-header').first
        is_spotlight = section.locator('#slider').count() > 0
        
        header_text = 'Spotlight' if is_spotlight else header_element.inner_text() if header_element.count() > 0 else None
        if not header_text: continue

        key = sanitize_key(header_text)
        items = section.locator('.flw-item, .deslide-item, .item-qtip, .anif-block li').all()
        if not items: continue

        section_urls = []
        for item in items:
            link_element = item.locator('a.film-poster-ahref, .desi-buttons a.btn-secondary, a.film-poster').first
            if link_element.count() > 0:
                href = link_element.get_attribute('href').replace('/watch/', '/')
                if href not in section_urls:
                    section_urls.append(href)
        
        if section_urls:
            unique_detail_urls.update(section_urls)
            sections[key] = section_urls
            
    print(f"Found {len(unique_detail_urls)} unique anime entries to process.")
    return sections, list(unique_detail_urls)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS['User-Agent'])
        page = context.new_page()

        sections, detail_urls = scrape_homepage(page)
        page.close() # Halaman utama tidak diperlukan lagi
        
        if not detail_urls: return

        all_anime_details = {}
        
        with ThreadPoolExecutor(max_workers=3) as executor: # Kurangi worker karena browser lebih berat
            future_to_url = {executor.submit(scrape_anime_data, url, context): url for url in detail_urls}
            
            for i, future in enumerate(as_completed(future_to_url)):
                url = future_to_url[future]
                print(f"({i+1}/{len(detail_urls)}) Processing: {url}", end="")
                try:
                    data = future.result()
                    if data:
                        all_anime_details[url] = data
                        print(f" -> ✓ Success: {data['title']}")
                    else:
                        print(f" -> ✗ Failed or no data found.")
                except Exception as exc:
                    print(f" -> ✗ Exception: {exc}")

        browser.close()

        final_data = {}
        for section_name, urls in sections.items():
            section_data = [all_anime_details[url] for url in urls if url in all_anime_details]
            if section_data:
                final_data[section_name] = section_data

        file_path = os.path.join(os.path.dirname(__file__), 'anime_data.json')
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=4, ensure_ascii=False)

        print(f"\nScraping complete. Data saved to {file_path}")

if __name__ == '__main__':
    main()
