import json
import os
import time
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
# --- PERBAIKAN: Nama impor yang benar adalah sync_stealth ---
from playwright_stealth import sync_stealth

# --- Konfigurasi ---
BASE_URL = "https://aniwatchtv.to"
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
MAX_WORKERS = 2
MAX_RETRIES = 2

def sanitize_key(text):
    """Membersihkan teks untuk dijadikan kunci JSON yang valid."""
    text = text.lower().replace(' ', '_')
    return re.sub(r'[^a-z0-9_]', '', text)

def _parse_watch_page(page, detail_url):
    """Helper untuk mem-parsing data dari halaman /watch."""
    page.wait_for_selector('iframe#iframe-embed', timeout=25000)
    
    title = page.locator('.anisc-detail .film-name a').inner_text(timeout=5000)
    description = page.locator('.film-description .text').inner_text(timeout=5000).replace("... + More", "").strip()
    image_url = page.locator('.anisc-poster .film-poster-img').get_attribute('src', timeout=5000)
    streaming_url = page.locator('iframe#iframe-embed').get_attribute('src', timeout=10000)
    
    servers = {'sub': [], 'dub': []}
    for server_block in page.locator('.ps_-block').all():
        server_type = 'dub' if 'servers-dub' in server_block.get_attribute('class', '') else 'sub'
        for item in server_block.locator('.server-item').all():
            servers[server_type].append({'name': item.inner_text(), 'data_id': item.get_attribute('data-id')})
    
    episodes = []
    for item in page.locator('.ss-list a.ssl-item.ep-item').all():
        episodes.append({
            'episode_number': item.get_attribute('data-number'),
            'title': item.locator('.ep-name').inner_text(),
            'url': urljoin(BASE_URL, item.get_attribute('href'))
        })
    
    return {
        'title': title, 'detail_page_url': urljoin(BASE_URL, detail_url), 'image_url': image_url,
        'description': description, 'streaming_iframe_url': streaming_url,
        'servers': servers, 'episodes': episodes
    }

def _parse_detail_page(page, detail_url):
    """Fallback untuk mem-parsing data dari halaman detail."""
    page.wait_for_selector('.anisc-detail .film-name a', timeout=25000)

    title = page.locator('.anisc-detail .film-name a').inner_text(timeout=5000)
    description = page.locator('.anisc-detail .film-description').inner_text(timeout=5000).replace("...Read more", "").strip()
    image_url = page.locator('.anisc-poster .film-poster-img').get_attribute('src', timeout=5000)

    return {
        'title': title, 'detail_page_url': urljoin(BASE_URL, detail_url), 'image_url': image_url,
        'description': description, 'streaming_iframe_url': None,
        'servers': {'sub': [], 'dub': []}, 'episodes': []
    }

def scrape_anime_data(url, browser):
    """Logika utama: Coba ambil dari halaman /watch, jika gagal, beralih ke halaman detail."""
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()
    # --- PERBAIKAN: Menggunakan fungsi yang benar, yaitu sync_stealth ---
    sync_stealth(page)
    try:
        watch_url = urljoin(BASE_URL, f"/watch{url.split('?')[0]}")
        page.goto(watch_url, timeout=60000, wait_until='domcontentloaded')
        if "404" in page.title() or "Page not found" in page.inner_text('body', timeout=5000):
            raise PlaywrightTimeoutError("Navigated to 404 page, falling back.")
        return _parse_watch_page(page, url)
    except Exception:
        try:
            detail_url = urljoin(BASE_URL, url)
            page.goto(detail_url, timeout=60000, wait_until='domcontentloaded')
            return _parse_detail_page(page, url)
        except Exception:
            return None
    finally:
        page.close()
        context.close()

def scrape_homepage(browser):
    """Mengambil semua kategori dan URL anime dari halaman utama."""
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()
    # --- PERBAIKAN: Menggunakan fungsi yang benar, yaitu sync_stealth ---
    sync_stealth(page)
    print("Starting homepage scrape...")
    try:
        page.goto(urljoin(BASE_URL, "/home"), timeout=60000, wait_until='domcontentloaded')
        page.wait_for_selector('section.block_area', timeout=25000)

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
    finally:
        page.close()
        context.close()

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        sections, detail_urls = scrape_homepage(browser)
        
        if not detail_urls: 
            browser.close()
            return

        all_anime_details = {}
        failed_urls = detail_urls.copy()
        
        for i in range(MAX_RETRIES + 1):
            if not failed_urls: break
            
            current_batch = failed_urls.copy()
            failed_urls.clear()
            
            if i > 0:
                print(f"\n--- Starting Retry Attempt {i}/{MAX_RETRIES} for {len(current_batch)} failed URLs ---")
                time.sleep(5)

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_url = {executor.submit(scrape_anime_data, url, browser): url for url in current_batch}
                
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        data = future.result()
                        if data and data.get('title') and data.get('title') != "N/A":
                            all_anime_details[url] = data
                            print(f"✓ Success: {data['title']} ({url})")
                        else:
                            failed_urls.append(url)
                            print(f"✗ Failed: No valid data found for {url}")
                    except Exception as exc:
                        failed_urls.append(url)
                        print(f"✗ Exception for {url}: {exc}")

        browser.close()

        final_data = {}
        for section_name, urls in sections.items():
            section_data = [all_anime_details[url] for url in urls if url in all_anime_details]
            if section_data:
                final_data[section_name] = section_data

        if failed_urls:
            print(f"\nWarning: {len(failed_urls)} URLs failed to scrape after all retries.")
            final_data['failed_urls'] = failed_urls

        file_path = os.path.join(os.path.dirname(__file__), 'anime_data.json')
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=4, ensure_ascii=False)

        print(f"\nScraping complete. Data saved to {file_path}")

if __name__ == '__main__':
    main()
