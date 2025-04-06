import streamlit as st
import os
import tempfile
import zipfile
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import re
from selenium import webdriver
import time
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
import glob
import concurrent.futures
import requests
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential
import threading
from queue import Queue
import unicodedata

# Thread-safe counter
download_counter = 0
counter_lock = threading.Lock()

# Global variables to replace session state in threads
user_agent_index = 0
proxy_index = 0
user_agent_lock = threading.Lock()
proxy_lock = threading.Lock()

# Constants
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
PROXY_LIST = []  # Popola questa lista se vuoi usare i proxy
TEMP_FILE_RETENTION = timedelta(hours=1)
CACHE_MAX_SIZE = 100 * 1024 * 1024  # 100MB

# Formati disponibili aggiornati
FORMATI_DISPONIBILI = {
    "original": "Formato originale (qualità massima)",
    "flac": "FLAC",
    "mp3": "MP3",
    "ogg-vorbis": "OGG Vorbis",
    "opus": "Opus",
    "m4a-aac": "M4A AAC",
    "wav": "WAV",
    "bitcrush": "Spaccatimpani"
}

# Qualità disponibili per ogni formato
QUALITA_DISPONIBILI = {
    "original": {"none": "Nessuna selezione"},
    "flac": {"16": "16-bit 44.1kHz"},
    "mp3": {
        "320": "320 kb/s",
        "256": "256 kb/s",
        "192": "192 kb/s",
        "128": "128 kb/s"
    },
    "ogg-vorbis": {
        "320": "320 kb/s",
        "256": "256 kb/s",
        "192": "192 kb/s",
        "128": "128 kb/s"
    },
    "opus": {
        "320": "320 kb/s",
        "256": "256 kb/s",
        "192": "192 kb/s",
        "128": "128 kb/s",
        "96": "96 kb/s",
        "64": "64 kb/s"
    },
    "m4a-aac": {
        "320": "320 kb/s",
        "256": "256 kb/s",
        "192": "192 kb/s",
        "128": "128 kb/s"
    },
    "wav": {"none": "Nessuna selezione"},
    "bitcrush": {"none": "Nessuna selezione"}
}

# Utility Functions
def get_thread_safe_user_agent():
    """Get next user agent in a thread-safe manner."""
    global user_agent_index
    with user_agent_lock:
        user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
        user_agent_index += 1
    return user_agent

def get_thread_safe_proxy():
    """Get next proxy in a thread-safe manner."""
    global proxy_index
    if not PROXY_LIST:
        return None
    with proxy_lock:
        proxy = PROXY_LIST[proxy_index % len(PROXY_LIST)]
        proxy_index += 1
    return proxy

def increment_download_count():
    """Increment download counter in a thread-safe manner."""
    global download_counter
    with counter_lock:
        download_counter += 1
    return download_counter

def remove_accents(text):
    """Remove accents from characters."""
    if not isinstance(text, str):
        return text
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))

def normalize_artist(artist_string):
    """Normalize artist name for better comparison."""
    if not artist_string:
        return ""
    normalized = remove_accents(artist_string.lower().strip())
    if ',' in normalized:
        normalized = normalized.split(',')[0].strip()
    
    articles = ['the ', 'a ', 'an ', 'il ', 'lo ', 'la ', 'i ', 'gli ', 'le ']
    for article in articles:
        if normalized.startswith(article):
            normalized = normalized[len(article):]
    
    conjunctions = [' and ', ' & ', ' e ', ' et ', ' + ', ' con ', ' feat ', ' feat. ',
                   ' featuring ', ' ft ', ' ft. ', ' vs ', ' vs. ', ' versus ', ' x ', ' with ']
    for conj in conjunctions:
        normalized = normalized.replace(conj, ' ')
    
    normalized = re.sub(r'\([^)]*\)', '', normalized)
    normalized = re.sub(r'\[[^\]]*\]', '', normalized)
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized

def normalize_track_title(title):
    """Normalize track title by removing common suffixes and variations."""
    if not title:
        return ""
    normalized = remove_accents(title.lower().strip())
    
    suffixes = [
        ' - original mix', ' (original mix)', ' - radio edit', ' (radio edit)',
        ' - edit', ' (edit)', ' - extended mix', ' (extended mix)',
        ' - club mix', ' (club mix)', ' - remix', ' (remix)',
        ' - radio version', ' (radio version)', ' - album version', ' (album version)',
        ' - instrumental', ' (instrumental)', ' - acoustic', ' (acoustic)',
        ' - live', ' (live)'
    ]
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    
    normalized = re.sub(r'(feat\.|feat|ft\.|ft|featuring).*', '', normalized)
    normalized = re.sub(r'\([^)]*\)', '', normalized)
    normalized = re.sub(r'\[[^\]]*\]', '', normalized)
    normalized = re.sub(r'[^\w\s\']', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized

def calculate_title_similarity(title1, title2):
    """Calculate similarity between two titles with a flexible approach (0.0 to 1.0)."""
    norm1 = normalize_track_title(title1)
    norm2 = normalize_track_title(title2)
    
    if norm1 in norm2 or norm2 in norm1:
        len_ratio = min(len(norm1), len(norm2)) / max(len(norm1), len(norm2))
        return max(0.8, len_ratio)
    
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    if not words1 or not words2:
        return 0.0
    
    intersection = len(words1.intersection(words2))
    if intersection > 0:
        similarity = intersection / min(len(words1), len(words2))
        if intersection > 1:
            similarity = min(1.0, similarity * 1.2)
        return similarity
    return 0.0

def artists_match(artist1, artist2):
    """Compare two artist strings and determine if they match."""
    norm1 = normalize_artist(artist1)
    norm2 = normalize_artist(artist2)
    
    if norm1 == norm2 or norm1 in norm2 or norm2 in norm1:
        return True
    
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    if words1.intersection(words2):
        if len(words1) <= 2 or len(words2) <= 2:
            return True
        intersection = len(words1.intersection(words2))
        smaller_set = min(len(words1), len(words2))
        if intersection / smaller_set >= 0.5:
            return True
    return False

def find_best_track_match(search_title, search_artist, result_titles, result_artists):
    """Find the best match in search results with a flexible approach."""
    best_match_idx = None
    best_match_score = 0
    min_title_score = 0.4
    match_details = []
    
    for i, title_elem in enumerate(result_titles):
        result_title = title_elem.text.strip()
        title_score = calculate_title_similarity(search_title, result_title)
        artist_match = True
        artist_info = ""
        
        if search_artist and i < len(result_artists):
            result_artist = result_artists[i].text.strip()
            artist_match = artists_match(search_artist, result_artist)
            artist_info = f"Artist: {search_artist} vs {result_artist} = {artist_match}"
        
        match_score = title_score * 0.8
        if search_artist:
            match_score += (1.0 if artist_match else 0.0) * 0.2
        
        match_details.append({
            "index": i,
            "title": result_title,
            "title_score": title_score,
            "artist_info": artist_info,
            "match_score": match_score
        })
        
        if match_score > best_match_score and title_score >= min_title_score:
            if not artist_match and title_score < 0.8:
                continue
            best_match_score = match_score
            best_match_idx = i
    
    return best_match_idx, match_details

# Security and Compliance
CLIENT_ID = st.secrets.get('SPOTIFY', {}).get('CLIENT_ID')
CLIENT_SECRET = st.secrets.get('SPOTIFY', {}).get('CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    st.error("Le credenziali Spotify non sono state configurate in Streamlit Secrets.")
    st.stop()

# Session State Initialization
required_keys = [
    'downloaded_files', 'pending_tracks', 'log_messages', 'spotify_tracks_cache',
    'last_cache_update', 'browser_pool', 'user_agent_index', 'proxy_index',
    'download_progress', 'download_errors', 'servizi_disponibili'
]

for key in required_keys:
    if key not in st.session_state:
        if key in ['downloaded_files', 'pending_tracks', 'log_messages', 'browser_pool', 'servizi_disponibili']:
            st.session_state[key] = []
        elif key in ['spotify_tracks_cache', 'last_cache_update', 'download_progress', 'download_errors']:
            st.session_state[key] = {}
        elif key in ['user_agent_index', 'proxy_index']:
            st.session_state[key] = 0

# Selenium Configuration
download_dir = tempfile.mkdtemp()
st.write(f"Directory di download: {download_dir} (Permessi: {os.access(download_dir, os.W_OK)})")

def get_thread_safe_chrome_options(use_proxy=False):
    """Configure Chrome options in a thread-safe manner."""
    options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"user-agent={get_thread_safe_user_agent()}")
    
    proxy = get_thread_safe_proxy() if use_proxy else None
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return options

def create_thread_safe_browser_instance(use_proxy=False):
    """Create a new browser instance with error handling."""
    try:
        return webdriver.Chrome(options=get_thread_safe_chrome_options(use_proxy))
    except Exception as e:
        print(f"Errore nella creazione del browser: {str(e)}")
        try:
            return webdriver.Chrome(
                service=Service("/usr/bin/chromedriver"),
                options=get_thread_safe_chrome_options(use_proxy)
            )
        except Exception as e2:
            print(f"Secondo tentativo fallito: {str(e2)}")
            raise

def create_browser_instance(use_proxy=False):
    """Wrapper for creating browser instances."""
    return create_thread_safe_browser_instance(use_proxy)

def get_browser_from_pool(use_proxy=False):
    """Get a browser from the pool or create a new one."""
    if st.session_state['browser_pool']:
        browser = st.session_state['browser_pool'].pop()
        try:
            browser.current_url  # Verifica se il browser è ancora attivo
            return browser
        except:
            safe_browser_quit(browser)
    return create_browser_instance(use_proxy)

def return_browser_to_pool(browser):
    """Return a browser to the pool."""
    if browser:
        try:
            st.session_state['browser_pool'].append(browser)
        except Exception as e:
            safe_browser_quit(browser)
            st.error(f"Errore nel ritorno del browser al pool: {str(e)}")

# Helper Functions
def split_title(full_title):
    """Split title into artist and track."""
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, full_title.strip()

def is_file_complete(filepath, expected_extension):
    """Check if a file is complete and matches the expected extension."""
    if not os.path.exists(filepath) or filepath.endswith(".crdownload"):
        return False
    if not filepath.lower().endswith(expected_extension.lower()):
        return False
    return os.path.getsize(filepath) > 0

def wait_for_download(download_dir, existing_files, formato, timeout=180):
    """Wait for a download to complete."""
    start_time = time.time()
    extension_map = {
        "original": ".*",  # Potrebbe essere qualsiasi formato, usiamo wildcard
        "flac": "flac",
        "mp3": "mp3",
        "ogg-vorbis": "ogg",
        "opus": "opus",
        "m4a-aac": "m4a",
        "wav": "wav",
        "bitcrush": "mp3"  # Assumiamo mp3 per bitcrush, da verificare
    }
    expected_extension = extension_map.get(formato, formato)
    
    while time.time() - start_time < timeout:
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, f"*.{expected_extension}"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        new_files = [f for f in current_files if f not in existing_files]
        
        for file in new_files:
            if is_file_complete(file, expected_extension):
                return True, f"Download completato: {file}", file
        
        if crdownload_files or time.time() - start_time < 30:
            time.sleep(5)
            continue
        
        all_new_files = [f for f in os.listdir(download_dir) if os.path.join(download_dir, f) not in existing_files]
        for f in all_new_files:
            full_path = os.path.join(download_dir, f)
            if os.path.isfile(full_path) and is_file_complete(full_path, expected_extension):
                return True, f"Download completato: {f}", full_path
        
        time.sleep(5)
    
    return False, f"Timeout raggiunto ({timeout}s), nessun download completato.", None

def create_zip_archive(download_dir, downloaded_files, zip_name="tracce_scaricate.zip"):
    """Create a ZIP archive of downloaded files."""
    zip_path = os.path.join(download_dir, zip_name)
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in downloaded_files:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    zipf.write(file_path, os.path.basename(file_path))
        return zip_path if os.path.exists(zip_path) else None
    except Exception as e:
        st.session_state['log_messages'].append(f"Errore nella creazione dello ZIP: {str(e)}")
        return None

def get_playlist_id(playlist_link):
    """Extract playlist ID from Spotify link."""
    parsed_url = urlparse(playlist_link)
    if parsed_url.netloc not in ['open.spotify.com']:
        raise ValueError("Link Spotify non valido.")
    match = re.search(r'playlist/(\w+)', parsed_url.path)
    if match:
        return match.group(1)
    raise ValueError("Link della playlist non valido.")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def _get_spotify_tracks(sp, playlist_id):
    """Fetch tracks from Spotify playlist with retry mechanism."""
    tracks_data = []
    results = sp.playlist_tracks(playlist_id)
    tracks_data.extend(results['items'])
    while results['next']:
        results = sp.next(results)
        tracks_data.extend(results['items'])
    return tracks_data

def get_spotify_tracks(playlist_link):
    """Get tracks from a Spotify playlist."""
    try:
        auth_manager = SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        playlist_id = get_playlist_id(playlist_link)
        tracks_data = _get_spotify_tracks(sp, playlist_id)
        return [{"artist": ', '.join([artist['name'] for artist in item['track']['artists']]),
                 "title": item['track']['name']} for item in tracks_data if item['track']]
    except Exception as e:
        st.session_state['log_messages'].append(f"Errore nel recupero delle tracce da Spotify: {str(e)}")
        return None

def get_available_services(browser):
    """Get available services from the website."""
    try:
        browser.get("https://lucida.su")
        WebDriverWait(browser, 20).until(
            EC.presence_of_element_located((By.ID, "service"))
        )
        select_service = browser.find_element(By.ID, "service")
        options = select_service.find_elements(By.TAG_NAME, "option")
        return [{"index": i, "value": opt.get_attribute("value"), "text": opt.text}
                for i, opt in enumerate(options) if i > 0]
    except Exception as e:
        st.session_state['log_messages'].append(f"Errore nel recupero dei servizi: {str(e)}")
        return []

def log_error(message):
    """Log error messages to file and session state."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] ERROR: {message}\n"
    with open("error.log", "a") as f:
        f.write(log_message)
    st.session_state['log_messages'].append(f"🔴 {message}")

def download_track_thread_safe(track_info, servizio_idx, formato_valore, qualita_valore, use_proxy=False):
    """Download a track in a thread-safe manner."""
    if isinstance(track_info, str):
        traccia = track_info
    else:
        traccia = f"{track_info.get('artist', '')} - {track_info.get('title', '')}"
    track_key = traccia
    browser = None
    log_messages = []
    
    try:
        options = webdriver.ChromeOptions()
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        options.add_experimental_option("prefs", prefs)
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(f"user-agent={get_thread_safe_user_agent()}")
        
        proxy = get_thread_safe_proxy() if use_proxy else None
        if proxy:
            options.add_argument(f"--proxy-server={proxy}")
        
        browser = webdriver.Chrome(options=options)
        artista_input, traccia_input = split_title(traccia)
        log_messages.append(f"🎤 Artista: {artista_input} | 🎵 Traccia: {traccia_input}")
        
        browser.get("https://lucida.su")
        if "captcha" in browser.page_source.lower() or "cloudflare" in browser.page_source.lower():
            log_messages.append("⚠️ Rilevato CAPTCHA o protezione Cloudflare")
            return {"track_key": track_key, "success": False, "downloaded_file": None, "log": log_messages, "status": "❌ Bloccato da protezione"}
        
        log_messages.append(f"🌐 Accesso a lucida.su (servizio {servizio_idx})")
        
        input_field = WebDriverWait(browser, 20).until(EC.element_to_be_clickable((By.ID, "download")))
        input_field.clear()
        input_field.send_keys(traccia)
        log_messages.append("✍️ Campo input compilato")
        
        select_service = WebDriverWait(browser, 20).until(EC.element_to_be_clickable((By.ID, "service")))
        opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
        if servizio_idx >= len(opzioni_service):
            log_messages.append(f"⚠️ Indice {servizio_idx} non valido per 'service'")
            return {
                "track_key": track_key,
                "success": False,
                "downloaded_file": None,
                "log": log_messages,
                "status": "❌ Errore: Indice servizio non valido"
            }
        
        servizio_valore = opzioni_service[servizio_idx].get_attribute("value")
        Select(select_service).select_by_value(servizio_valore)
        log_messages.append(f"🔧 Servizio {servizio_idx} selezionato: {opzioni_service[servizio_idx].text}")
        
        WebDriverWait(browser, 20).until(
            lambda d: len(d.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0
        )
        select_country = Select(browser.find_element(By.ID, "country"))
        if not select_country.options:
            log_messages.append(f"⚠️ Nessuna opzione in 'country' per servizio {servizio_idx}")
            return {
                "track_key": track_key,
                "success": False,
                "downloaded_file": None,
                "log": log_messages,
                "status": "❌ Errore: Nessuna opzione paese"
            }
        select_country.select_by_index(0)
        log_messages.append(f"🌍 Paese selezionato: {select_country.first_selected_option.text}")
        
        go_button = WebDriverWait(browser, 20).until(EC.element_to_be_clickable((By.ID, "go")))
        go_button.click()
        log_messages.append("▶️ Pulsante 'go' cliccato")
        
        WebDriverWait(browser, 60).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or "No results found" in d.page_source
        )
        log_messages.append("🔍 Risultati caricati con successo")
        
        titoli = browser.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
        artisti = browser.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")
        log_messages.append(f"📋 Risultati trovati: {len(titoli)} titoli")
        
        best_match_idx, match_details = find_best_track_match(traccia_input, artista_input, titoli, artisti)
        for match in match_details:
            log_messages.append(
                f"🔍 Match analisi: '{traccia_input}' vs '{match['title']}' - "
                f"Punteggio titolo: {match['title_score']:.2f}, "
                f"Match score: {match['match_score']:.2f} - {match['artist_info']}"
            )
        
        if best_match_idx is not None:
            browser.execute_script("arguments[0].scrollIntoView(true);", titoli[best_match_idx])
            titoli[best_match_idx].click()
            selected_title = titoli[best_match_idx].text.strip()
            selected_artist = artisti[best_match_idx].text.strip() if best_match_idx < len(artisti) else ""
            log_messages.append(f"✅ Traccia selezionata: '{selected_title}' di '{selected_artist}' con indice {best_match_idx}")
        else:
            log_messages.append(f"❌ Traccia non trovata in servizio {servizio_idx}")
            return {
                "track_key": track_key,
                "success": False,
                "downloaded_file": None,
                "log": log_messages,
                "status": "❌ Errore: Traccia non trovata"
            }

        select_convert = Select(WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "convert"))))
        select_convert.select_by_value(formato_valore)
        log_messages.append(f"🎧 Formato selezionato: {formato_valore}")
        
        if formato_valore not in ["wav", "original", "bitcrush"]:
            select_downsetting = Select(WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "downsetting"))))
            try:
                select_downsetting.select_by_value(qualita_valore)
                log_messages.append(f"🔊 Qualità selezionata: {qualita_valore}")
            except Exception as e:
                log_messages.append(f"⚠️ Errore selezione qualità: {str(e)} - Opzioni disponibili: {[opt.get_attribute('value') for opt in select_downsetting.options]}")
                return {
                    "track_key": track_key,
                    "success": False,
                    "downloaded_file": None,
                    "log": log_messages,
                    "status": f"❌ Errore: Qualità non valida per {formato_valore}"
                }
        else:
            log_messages.append("🔊 Nessuna qualità da selezionare per questo formato")
        
        existing_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.*"))]
        download_button = WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.CLASS_NAME, "download-button")))
        browser.execute_script("arguments[0].scrollIntoView(true);", download_button)
        download_button.click()
        log_messages.append("⬇️ Pulsante di download cliccato")
        
        success, message, downloaded_file = wait_for_download(download_dir, existing_files, formato_valore)
        log_messages.append(message)
        return {
            "track_key": track_key,
            "success": success,
            "downloaded_file": downloaded_file,
            "log": log_messages,
            "status": "✅ Scaricato" if success and downloaded_file else f"❌ Errore: {message}"
        }
    
    except Exception as e:
        error_message = f"❌ Errore durante il download: {str(e)}"
        log_messages.append(error_message)
        return {
            "track_key": track_key,
            "success": False,
            "downloaded_file": None,
            "log": log_messages,
            "status": f"❌ Errore: {str(e)}"
        }
    finally:
        if browser:
            safe_browser_quit(browser)
            log_messages.append("🧹 Browser chiuso")

def cleanup_temp_files():
    """Clean up temporary files older than retention period."""
    now = datetime.now()
    for filename in os.listdir(download_dir):
        filepath = os.path.join(download_dir, filename)
        if os.path.isfile(filepath):
            file_creation_time = datetime.fromtimestamp(os.path.getctime(filepath))
            if now - file_creation_time > TEMP_FILE_RETENTION:
                try:
                    os.remove(filepath)
                    st.session_state['log_messages'].append(f"🗑️ File temporaneo eliminato: {filename}")
                except Exception as e:
                    st.session_state['log_messages'].append(f"⚠️ Errore nell'eliminazione di {filename}: {e}")

def safe_browser_quit(browser):
    """Safely quit a browser instance."""
    if browser:
        try:
            browser.quit()
        except Exception as e:
            print(f"Errore durante la chiusura del browser: {e}")

def cleanup_browser_pool():
    """Clean up all browsers in the pool."""
    if 'browser_pool' in st.session_state:
        for browser in st.session_state['browser_pool']:
            safe_browser_quit(browser)
        st.session_state['browser_pool'] = []

def close_all_browsers():
    """Close all browsers in the pool."""
    if 'browser_pool' in st.session_state:
        for browser in st.session_state['browser_pool']:
            safe_browser_quit(browser)
        st.session_state['browser_pool'] = []

import atexit
atexit.register(cleanup_browser_pool)

# Streamlit Interface
st.title("Downloader di Tracce Musicali (PIZZUNA)")
st.warning("⚠️ Prima di scaricare, assicurati di rispettare le leggi sul copyright e i termini di servizio delle piattaforme musicali.")

use_proxy = st.sidebar.checkbox("Usa Proxy", False)

if 'servizi_disponibili' not in st.session_state or not st.session_state['servizi_disponibili']:
    with st.spinner("Caricamento servizi disponibili..."):
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            temp_browser = webdriver.Chrome(options=options)
            try:
                st.session_state['servizi_disponibili'] = get_available_services(temp_browser)
            finally:
                safe_browser_quit(temp_browser)
            if st.session_state['servizi_disponibili']:
                st.success(f"Caricati {len(st.session_state['servizi_disponibili'])} servizi disponibili.")
            else:
                st.warning("Impossibile caricare i servizi disponibili.")
                st.session_state['servizi_disponibili'] = [{"index": 1, "value": "1", "text": "Servizio predefinito"}]
        except Exception as e:
            st.error(f"Errore durante il caricamento dei servizi: {str(e)}")
            st.session_state['servizi_disponibili'] = [{"index": 1, "value": "1", "text": "Servizio predefinito"}]

st.subheader("Preferenze di download")
if st.session_state['servizi_disponibili']:
    servizio_opzioni = {f"{s['text']} (Servizio {s['index']})": s['index'] for s in st.session_state['servizi_disponibili']}
    servizio_selezionato = st.selectbox("Servizio preferito", options=list(servizio_opzioni.keys()), index=0)
    servizio_indice = servizio_opzioni[servizio_selezionato]
else:
    st.warning("Nessun servizio disponibile. Ricaricare la pagina.")
    servizio_indice = 0

col1, col2 = st.columns(2)
with col1:
    formato_selezionato = st.selectbox("Formato audio", options=list(FORMATI_DISPONIBILI.values()), index=0)
    formato_valore = list(FORMATI_DISPONIBILI.keys())[list(FORMATI_DISPONIBILI.values()).index(formato_selezionato)]

with col2:
    qualita_opzioni = QUALITA_DISPONIBILI[formato_valore]
    qualita_selezionata = st.selectbox("Qualità audio", options=list(qualita_opzioni.values()), index=0)
    qualita_valore = list(qualita_opzioni.keys())[list(qualita_opzioni.values()).index(qualita_selezionata)]

num_threads = st.slider("Numero di download paralleli", min_value=1, max_value=5, value=2,
                        help="Un numero inferiore riduce il rischio di blocchi.")

st.subheader("Genera tracce da Spotify")
playlist_link = st.text_input("Link della playlist Spotify")
if playlist_link and st.button("Carica Tracce Spotify"):
    with st.spinner("Caricamento tracce da Spotify..."):
        spotify_tracks = get_spotify_tracks(playlist_link)
        if spotify_tracks:
            st.session_state['spotify_tracks'] = spotify_tracks
            st.success(f"Trovate {len(spotify_tracks)} tracce dalla playlist Spotify.")
        else:
            st.error("Impossibile recuperare le tracce da Spotify. Controlla il link e le credenziali.")

uploaded_file = st.file_uploader("Oppure carica il file tracce.txt (artista - titolo)", type=["txt"])

st.subheader("Anteprima Tracce")
tracks_to_download = []
if 'spotify_tracks' in st.session_state:
    tracks_to_download.extend(st.session_state['spotify_tracks'])
if uploaded_file is not None:
    file_content = uploaded_file.read().decode("utf-8").splitlines()
    for line in file_content:
        parts = line.split('-', 1)
        if len(parts) == 2:
            tracks_to_download.append({"artist": parts[0].strip(), "title": parts[1].strip()})
        elif line.strip():
            tracks_to_download.append({"title": line.strip(), "artist": None})

if tracks_to_download:
    st.write(f"**Tracce selezionate per il download:** {len(tracks_to_download)}")
    sort_by = st.selectbox("Ordina per:", ["Nessuno", "Artista", "Titolo"])
    if sort_by == "Artista":
        tracks_to_download.sort(key=lambda x: x.get('artist', '').lower())
    elif sort_by == "Titolo":
        tracks_to_download.sort(key=lambda x: x.get('title', '').lower())
    st.dataframe(tracks_to_download)

if 'downloaded_files' in st.session_state and st.session_state['downloaded_files'] and st.session_state.get('download_started', False):
    st.balloons()
    st.success(f"🎉 Download completato! {len(st.session_state['downloaded_files'])} tracce scaricate con successo.")
    st.session_state['download_started'] = False

if st.button("Avvia Download", key="avvia_download_button") and tracks_to_download:
    st.session_state['download_started'] = True
    st.session_state['downloaded_files'] = []
    st.session_state['log_messages'] = []
    st.session_state['pending_tracks'] = []
    
    track_status = {f"{t.get('artist', '')} - {t.get('title', '')}": "In attesa..." for t in tracks_to_download}
    st.session_state['download_progress'] = track_status.copy()
    st.session_state['download_errors'] = {}
    
    progress_bar = st.progress(0)
    num_tracks = len(tracks_to_download)
    downloaded_count = 0
    
    download_results_container = st.container()
    with download_results_container:
        status_placeholder = st.empty()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(download_track_thread_safe, track, servizio_indice, formato_valore, qualita_valore, use_proxy)
                for track in tracks_to_download
            ]
            
            pending_futures = list(futures)
            downloaded_files = []
            pending_tracks = []
            download_errors = {}
            
            while pending_futures:
                status_text = "<h3>Stato Download in corso:</h3>"
                for track_key, status in track_status.items():
                    status_class = "info" if "In corso" in status else "success" if "✅ Scaricato" in status else "error" if "❌ Errore" in status else ""
                    status_text += f"<div class='{status_class}'>{track_key}: {status}</div>"
                status_placeholder.markdown(status_text, unsafe_allow_html=True)
                
                done, pending_futures = concurrent.futures.wait(
                    pending_futures, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
                )
                
                for future in done:
                    try:
                        result = future.result()
                        track_key = result["track_key"]
                        track_status[track_key] = result["status"]
                        if result["success"] and result["downloaded_file"]:
                            downloaded_files.append(result["downloaded_file"])
                            downloaded_count += 1
                        else:
                            pending_tracks.append(track_key)
                            download_errors[track_key] = result["log"]
                        progress_value = (num_tracks - len(pending_futures)) / num_tracks
                        progress_bar.progress(progress_value)
                    except Exception as e:
                        st.error(f"Errore nel processare i risultati del download: {str(e)}")
            
            st.session_state['downloaded_files'] = downloaded_files
            st.session_state['pending_tracks'] = pending_tracks
            st.session_state['download_errors'] = download_errors
            st.session_state['download_progress'] = track_status
            
            status_text = "<h3>Stato Download Finale:</h3>"
            for track_key, status in st.session_state['download_progress'].items():
                status_class = "info" if "In corso" in status else "success" if "✅ Scaricato" in status else "error" if "❌ Errore" in status else ""
                status_text += f"<div class='{status_class}'>{track_key}: {status}</div>"
            status_placeholder.markdown(status_text, unsafe_allow_html=True)

    if st.session_state.get('pending_tracks') and not st.checkbox("Skip auto-retry", value=False):
        st.info(f"🔄 Trovate {len(st.session_state['pending_tracks'])} tracce non scaricate. Tentativo automatico di recupero in corso...")
        
        retry_tracks = []
        for track_key in st.session_state['pending_tracks']:
            parts = track_key.split(" - ", 1)
            retry_tracks.append({"artist": parts[0].strip(), "title": parts[1].strip()} if len(parts) == 2 else {"artist": "", "title": track_key.strip()})
        
        alternative_service = None
        available_services = st.session_state['servizi_disponibili']
        if len(available_services) > 1:
            for service in available_services:
                if service["index"] != servizio_indice:
                    alternative_service = service["index"]
                    break
        alternative_service = alternative_service or servizio_indice
        if alternative_service != servizio_indice:
            st.info(f"🔄 Tentativo con servizio alternativo (Servizio {alternative_service})")
        
        retry_progress_bar = st.progress(0)
        retry_status_container = st.container()
        retry_status = {track["artist"] + " - " + track["title"]: "In attesa..." for track in retry_tracks}
        retry_downloaded_files = []
        retry_pending_tracks = []
        retry_errors = {}
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as retry_executor:
            retry_futures = [
                retry_executor.submit(download_track_thread_safe, track, alternative_service, formato_valore, qualita_valore, use_proxy)
                for track in retry_tracks
            ]
            
            pending_retry_futures = list(retry_futures)
            retry_downloaded_count = 0
            
            with retry_status_container:
                retry_status_placeholder = st.empty()
                while pending_retry_futures:
                    retry_status_text = "<h3>Stato Recupero in corso:</h3>"
                    for track_key, status in retry_status.items():
                        status_class = "info" if "In corso" in status or "In attesa" in status else "success" if "✅ Scaricato" in status else "error" if "❌ Errore" in status else ""
                        retry_status_text += f"<div class='{status_class}'>{track_key}: {status}</div>"
                    retry_status_placeholder.markdown(retry_status_text, unsafe_allow_html=True)
                    
                    done_retries, pending_retry_futures = concurrent.futures.wait(
                        pending_retry_futures, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    
                    for future in done_retries:
                        try:
                            result = future.result()
                            track_key = result["track_key"]
                            retry_status[track_key] = result["status"]
                            if result["success"] and result["downloaded_file"]:
                                retry_downloaded_files.append(result["downloaded_file"])
                                retry_downloaded_count += 1
                            else:
                                retry_pending_tracks.append(track_key)
                                retry_errors[track_key] = result["log"]
                            retry_progress_value = (len(retry_tracks) - len(pending_retry_futures)) / len(retry_tracks)
                            retry_progress_bar.progress(retry_progress_value)
                        except Exception as e:
                            st.error(f"Errore nel processare i risultati del recupero: {str(e)}")
        
        st.session_state['downloaded_files'].extend(retry_downloaded_files)
        st.session_state['pending_tracks'] = retry_pending_tracks
        for track_key, errors in retry_errors.items():
            st.session_state['download_errors'][track_key] = errors
        for track_key, status in retry_status.items():
            st.session_state['download_progress'][track_key] = status
        
        st.write("### Riepilogo Recupero")
        st.write(f"**Tracce recuperate:** {retry_downloaded_count} / {len(retry_tracks)}")
        
        st.write("### Riepilogo Complessivo")
        st.write(f"**Totale tracce:** {num_tracks}")
        st.write(f"**Scaricate con successo:** {downloaded_count + retry_downloaded_count}")
        st.write(f"**Tracce non recuperabili:** {len(retry_pending_tracks)}")
        
        if retry_downloaded_count > 0:
            st.success(f"🎉 Recupero completato! Recuperate {retry_downloaded_count} tracce aggiuntive.")
            if retry_downloaded_count == len(retry_tracks):
                st.balloons()
        
        if retry_pending_tracks:
            st.write("**Elenco tracce non recuperabili:**")
            for track_key in retry_pending_tracks:
                st.write(f"- {track_key}")
            with st.expander("Dettagli errori recupero"):
                for track_key, errors in retry_errors.items():
                    st.write(f"**{track_key}:**")
                    for error in errors:
                        st.write(f"- {error}")

    st.write("### Riepilogo Download")
    st.write(f"**Totale tracce:** {num_tracks}")
    st.write(f"**Scaricate con successo:** {downloaded_count}")
    st.write(f"**Tracce non scaricate:** {len(st.session_state['pending_tracks'])}")
    
    if st.session_state['pending_tracks']:
        st.write("**Elenco tracce non scaricate:**")
        for track_key in st.session_state['pending_tracks']:
            st.write(f"- {track_key}")
    
    if st.session_state['download_errors']:
        with st.expander("Dettagli errori download"):
            for track_key, errors in st.session_state['download_errors'].items():
                st.write(f"**{track_key}:**")
                for error in errors:
                    st.write(f"- {error}")

st.sidebar.subheader("Disclaimer")
st.sidebar.info("""
    Questo strumento è fornito a scopo didattico e per uso personale. L'utente è responsabile del rispetto delle leggi sul copyright e dei termini di servizio delle piattaforme musicali. Il download di materiale protetto da copyright senza autorizzazione è illegale. Gli sviluppatori non si assumono alcuna responsabilità per un uso improprio di questo strumento.
""")

if st.sidebar.button("Pulisci Sessione"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

with st.sidebar.expander("Impostazioni Avanzate"):
    if st.button("Ricarica Servizi"):
        st.session_state['servizi_disponibili'] = []
        st.rerun()
    if st.checkbox("Mostra log completo"):
        st.subheader("Log Completo")
        for log_message in st.session_state.get('log_messages', []):
            st.write(log_message)
    if st.sidebar.checkbox("Modalità Sorpresa?"):
        st.sidebar.markdown("![Pizzuna](https://i.imgur.com/your_pizzuna_image.png)")
        st.markdown("## 🍕 Un tocco di Pizzuna! 🍕")

st.markdown("---")
st.info("L'applicazione è stata potenziata con diverse ottimizzazioni e nuove funzionalità. Ulteriori miglioramenti potrebbero essere implementati in futuro.")

if st.session_state.get('downloaded_files'):
    st.subheader("Scarica le tracce")
    zip_filename = "tracce_scaricate.zip"
    zip_path = create_zip_archive(download_dir, st.session_state['downloaded_files'], zip_filename)
    if zip_path:
        with open(zip_path, "rb") as f:
            st.download_button(
                label="Scarica tutte le tracce come ZIP",
                data=f,
                file_name=zip_filename,
                mime="application/zip"
            )
        cleanup_temp_files()
    else:
        st.error("Errore nella creazione dell'archivio ZIP.")
elif st.session_state.get('download_started', False) and not st.session_state.get('downloaded_files'):
    st.info("Download in corso... Attendi il completamento per scaricare le tracce.")
elif not tracks_to_download:
    st.info("Inserisci un link Spotify o carica un file di testo per avviare il download.")

if use_proxy and not PROXY_LIST:
    st.sidebar.warning("Hai selezionato di usare un proxy, ma la lista dei proxy è vuota. Nessun proxy verrà utilizzato.")
elif use_proxy and PROXY_LIST:
    st.sidebar.info(f"Utilizzo dei proxy: {len(PROXY_LIST)} proxy configurati.")
elif not use_proxy:
    st.sidebar.info("Non stai utilizzando un proxy.")

atexit.register(close_all_browsers)
st.markdown("---")
st.info("Grazie per aver utilizzato il Downloader di Tracce Musicali (PIZZUNA)!")
st.markdown("---")
st.markdown("Sviluppato con ❤️ da un appassionato di musica.")

st.markdown("""
    <style>
    .info { padding: 5px; background-color: #e7f5fe; border-left: 5px solid #2196F3; margin: 5px 0; }
    .success { padding: 5px; background-color: #e7ffe7; border-left: 5px solid #4CAF50; margin: 5px 0; }
    .error { padding: 5px; background-color: #ffebee; border-left: 5px solid #f44336; margin: 5px 0; }
    </style>
""", unsafe_allow_html=True)
