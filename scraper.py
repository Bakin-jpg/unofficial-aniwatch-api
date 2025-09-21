import requests
from bs4 import BeautifulSoup
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

# --- Konfigurasi ---
BASE_URL = "https://aniwatchtv.to"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': BASE_URL
}
# Menggunakan session untuk koneksi yang lebih efisien
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def scrape_episode_details(detail_url):
    """
    Mengambil detail lengkap dari halaman tonton anime, termasuk URL streaming,
    server, dan daftar episode.
    """
    try:
        # Halaman tonton biasanya memiliki '/watch' di depannya
        watch_url = urljoin(BASE_URL, f"/watch{detail_url.split('?')[0]}")
        
        response = SESSION.get(watch_url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # --- Ekstrak detail dasar anime ---
        title_element = soup.select_one('.anisc-detail .film-name a')
        title = title_element.get_text(strip=True) if title_element else "N/A"

        description_element = soup.select_one('.film-description .text')
        description = description_element.get_text(strip=True).replace("... + More", "").strip() if description_element else "No description available."
        
        poster_element = soup.select_one('.anisc-poster .film-poster-img')
        image_url = poster_element['src'] if poster_element else None

        # --- Ekstrak URL streaming dari iframe ---
        iframe_element = soup.select_one('iframe#iframe-embed')
        streaming_url = iframe_element['src'] if iframe_element else None

        # --- Ekstrak daftar server ---
        servers = {'sub': [], 'dub': []}
        server_blocks = soup.select('.ps_-block .ps__-list')
        for block in server_blocks:
            server_type = 'dub' if 'servers-dub' in block.find_previous('div').get('class', []) else 'sub'
            server_items = block.select('.server-item')
            for item in server_items:
                server_name = item.get_text(strip=True)
                data_id = item.get('data-id')
                servers[server_type].append({'name': server_name, 'data_id': data_id})

        # --- Ekstrak daftar episode ---
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
    except requests.exceptions.RequestException as e:
        print(f"Error fetching details for {detail_url}: {e}")
        return None
    except Exception as e:
        print(f"An error occurred while parsing details for {detail_url}: {e}")
        return None


def scrape_homepage():
    """
    Mengambil semua kategori dan daftar anime dari halaman utama.
    """
    print("Starting homepage scrape...")
    home_url = urljoin(BASE_URL, "/home")
    
    try:
        response = SESSION.get(home_url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch homepage: {e}")
        return {}

    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Kumpulkan semua URL detail unik untuk menghindari scraping ganda
    unique_detail_urls = set()
    sections = {}

    # --- Analisis setiap bagian di halaman utama ---
    all_sections = soup.select('section.block_area.block_area_home, section.block_area.block_area_trending, #anime-featured, .deslide-wrap')
    
    for section in all_sections:
        header_element = section.select_one('.bah-heading h2.cat-heading, .anif-block-header')
        
        # Penanganan khusus untuk slider/spotlight
        if section.select_one('#slider'):
            header_text = 'Spotlight'
        elif not header_element:
            continue
        else:
            header_text = header_element.get_text(strip=True)
            
        key = header_text.lower().replace(' ', '_').replace('on_aniwatch', '')

        # Temukan semua item anime di dalam bagian ini
        items = section.select('.flw-item, .deslide-item, .trending-list .item, .anif-block li')
        
        section_urls = []
        for item in items:
            link_element = item.select_one('a.film-poster-ahref, .desi-buttons a.btn-secondary, a.film-poster')
            if link_element and link_element.has_attr('href'):
                href = link_element['href']
                # Pastikan ini adalah URL detail, bukan URL tonton langsung
                if '/watch/' in href:
                    href = href.replace('/watch/', '/')
                
                unique_detail_urls.add(href)
                section_urls.append(href)
        
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
    
    # Gunakan ThreadPoolExecutor untuk mempercepat proses scraping detail
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {executor.submit(scrape_episode_details, url): url for url in detail_urls}
        
        for i, future in enumerate(as_completed(future_to_url)):
            url = future_to_url[future]
            try:
                data = future.result()
                if data:
                    all_anime_details[url] = data
                    print(f"({i+1}/{len(detail_urls)}) Successfully scraped: {data['title']}")
                else:
                    print(f"({i+1}/{len(detail_urls)}) Failed to scrape details for URL: {url}")
            except Exception as exc:
                print(f"URL {url} generated an exception: {exc}")

    # Gabungkan hasil scraping detail kembali ke struktur section
    final_data = {}
    for section_name, urls in sections.items():
        final_data[section_name] = [all_anime_details[url] for url in urls if url in all_anime_details]

    # Simpan ke file JSON
    file_path = os.path.join(os.path.dirname(__file__), 'anime_data.json')
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=4, ensure_ascii=False)

    print(f"\nScraping complete. All data has been saved to {file_path}")


if __name__ == '__main__':
    main()
