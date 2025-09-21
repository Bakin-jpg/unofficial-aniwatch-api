import requests
from bs4 import BeautifulSoup
import json
import os
import time
import random
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

def fetch_with_retry(url, retries=3, backoff_factor=0.5):
    """Mencoba mengambil URL beberapa kali jika terjadi error server."""
    for i in range(retries):
        try:
            response = SESSION.get(url, timeout=20)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [503, 500, 502, 504]:
                print(f"Server error {e.response.status_code} for {url}. Retrying in {backoff_factor * (2 ** i)}s...")
                time.sleep(backoff_factor * (2 ** i))
            else:
                raise # Lemparkan error lain yang bukan karena server
        except requests.exceptions.RequestException as e:
            print(f"Request error for {url}: {e}. Retrying...")
            time.sleep(backoff_factor * (2 ** i))
    # Jika semua percobaan gagal
    print(f"Failed to fetch {url} after {retries} retries.")
    return None

def scrape_episode_details(detail_url):
    """
    Mengambil detail lengkap dari halaman tonton anime, termasuk URL streaming,
    server, dan daftar episode.
    """
    try:
        watch_url = urljoin(BASE_URL, f"/watch{detail_url.split('?')[0].replace('.to//', '.to/')}")
        
        response = fetch_with_retry(watch_url)
        if not response:
            return None

        soup = BeautifulSoup(response.content, 'html.parser')

        title_element = soup.select_one('.anisc-detail .film-name a')
        title = title_element.get_text(strip=True) if title_element else "N/A"
        
        if title == "N/A": # Jika halaman tidak valid atau tidak ada judul
            return None

        description_element = soup.select_one('.film-description .text')
        description = description_element.get_text(strip=True).replace("... + More", "").strip() if description_element else "No description available."
        
        poster_element = soup.select_one('.anisc-poster .film-poster-img')
        image_url = poster_element['src'] if poster_element else None

        iframe_element = soup.select_one('iframe#iframe-embed')
        streaming_url = iframe_element['src'] if iframe_element else None

        servers = {'sub': [], 'dub': []}
        server_blocks = soup.select('.ps_-block .ps__-list')
        for block in server_blocks:
            server_type = 'dub' if 'servers-dub' in block.find_previous('div').get('class', []) else 'sub'
            server_items = block.select('.server-item')
            for item in server_items:
                server_name = item.get_text(strip=True)
                data_id = item.get('data-id')
                servers[server_type].append({'name': server_name, 'data_id': data_id})

        episodes = []
        episode_list_items = soup.select('.ss-list a.ssl-item.ep-item')
        for item in episode_list_items:
            ep_number = item.get('data-number')
            ep_title = item.select_one('.ep-name').get_text(strip=True)
            ep_url = urljoin(BASE_URL, item['href'])
            episodes.append({
                'episode_number': ep_number,
                'title': ep_title,
                'url': ep_url
            })
            
        return {
            'title': title,
            'detail_page_url': urljoin(BASE_URL, detail_url),
            'image_url': image_url,
            'description': description,
            'streaming_iframe_url': streaming_url,
            'servers': servers,
            'episodes': episodes
        }
    except Exception as e:
        print(f"An error occurred while parsing details for {detail_url}: {e}")
        return None

def scrape_homepage():
    """
    Mengambil semua kategori dan daftar anime dari halaman utama.
    """
    print("Starting homepage scrape...")
    home_url = urljoin(BASE_URL, "/home")
    
    response = fetch_with_retry(home_url)
    if not response:
        print("Failed to fetch homepage. Exiting.")
        return {}, []

    soup = BeautifulSoup(response.content, 'html.parser')
    
    unique_detail_urls = set()
    sections = {}

    all_sections = soup.select('section.block_area, #anime-featured, .deslide-wrap')
    
    for section in all_sections:
        header_element = section.select_one('.bah-heading h2.cat-heading, .anif-block-header')
        
        if section.select_one('#slider'):
            header_text = 'Spotlight'
        elif not header_element:
            continue
        else:
            header_text = header_element.get_text(strip=True)
            
        key = header_text.lower().replace(' ', '_').replace('on_aniwatch', '')
        
        items = section.select('.flw-item, .deslide-item, .item-qtip, .anif-block li')
        
        if not items: continue

        section_urls = []
        for item in items:
            link_element = item.select_one('a.film-poster-ahref, .desi-buttons a.btn-secondary, a.film-poster')
            if link_element and link_element.has_attr('href'):
                href = link_element['href']
                if '/watch/' in href:
                    href = href.replace('/watch/', '/')
                
                # Menghindari duplikasi dalam satu sesi
                if href not in unique_detail_urls:
                    unique_detail_urls.add(href)
                    section_urls.append(href)

        if section_urls:
            # Hanya tambahkan section jika ada URL di dalamnya
            sections[key] = section_urls
            
    print(f"Found {len(unique_detail_urls)} unique anime entries to process.")
    return sections, list(unique_detail_urls)

def main():
    """
    Fungsi utama untuk menjalankan scraper, memproses data, dan menyimpan ke JSON.
    """
    sections, detail_urls = scrape_homepage()
    if not detail_urls:
        print("No anime URLs found on the homepage. Exiting.")
        return

    all_anime_details = {}
    
    # Kurangi worker menjadi 5 dan tambahkan jeda untuk mengurangi beban server
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {executor.submit(scrape_episode_details, url): url for url in detail_urls}
        
        for i, future in enumerate(as_completed(future_to_url)):
            url = future_to_url[future]
            try:
                data = future.result()
                # Hanya proses data yang valid (bukan None dan punya judul)
                if data and data.get('title') != "N/A":
                    all_anime_details[url] = data
                    print(f"({i+1}/{len(detail_urls)}) Successfully scraped: {data['title']}")
                else:
                    print(f"({i+1}/{len(detail_urls)}) Failed or got invalid data for URL: {url}")
            except Exception as exc:
                print(f"URL {url} generated an exception: {exc}")
            
            # Tambahkan jeda acak antara 0.2 hingga 0.5 detik
            time.sleep(random.uniform(0.2, 0.5))

    final_data = {}
    for section_name, urls in sections.items():
        # Hanya masukkan anime yang berhasil di-scrape
        final_data[section_name] = [all_anime_details[url] for url in urls if url in all_anime_details]

    file_path = os.path.join(os.path.dirname(__file__), 'anime_data.json')
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)

    print(f"\nScraping complete. All data has been saved to {file_path}")

if __name__ == '__main__':
    main()
