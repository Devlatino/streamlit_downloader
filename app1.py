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

# Thread-safe counter
download_counter = 0
counter_lock = threading.Lock()

# Global variables to replace session state in threads
user_agent_index = 0
proxy_index = 0
user_agent_lock = threading.Lock()
proxy_lock = threading.Lock()

# Thread-safe function to get next user agent
def get_thread_safe_user_agent():
    global user_agent_index
    with user_agent_lock:
        user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
        user_agent_index += 1
        return user_agent

# Thread-safe function to get next proxy
def get_thread_safe_proxy():
    global proxy_index
    if not PROXY_LIST:
        return None
    with proxy_lock:
        proxy = PROXY_LIST[proxy_index % len(PROXY_LIST)]
        proxy_index += 1
        return proxy

# Function to increment download counter
def increment_download_count():
    global download_counter
    with counter_lock:
        download_counter += 1
        return download_counter

# 1. Sicurezza e Conformità Legale
CLIENT_ID = st.secrets.get('SPOTIFY', {}).get('CLIENT_ID')
CLIENT_SECRET = st.secrets.get('SPOTIFY', {}).get('CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    st.error("Le credenziali Spotify non sono state configurate in Streamlit Secrets.")
    st.stop()

# Inizializza lo stato della sessione
if 'downloaded_files' not in st.session_state:
    st.session_state['downloaded_files'] = []
if 'pending_tracks' not in st.session_state:
    st.session_state['pending_tracks'] = []
if 'log_messages' not in st.session_state:
    st.session_state['log_messages'] = []
if 'spotify_tracks_cache' not in st.session_state:
    st.session_state['spotify_tracks_cache'] = {}
if 'last_cache_update' not in st.session_state:
    st.session_state['last_cache_update'] = {}
if 'browser_pool' not in st.session_state:
    st.session_state['browser_pool'] = []
if 'user_agent_index' not in st.session_state:
    st.session_state['user_agent_index'] = 0
if 'proxy_index' not in st.session_state:
    st.session_state['proxy_index'] = 0
if 'download_progress' not in st.session_state:
    st.session_state['download_progress'] = {}
if 'download_errors' not in st.session_state:
    st.session_state['download_errors'] = {}
if 'servizi_disponibili' not in st.session_state:
    st.session_state['servizi_disponibili'] = []
# Nuova struttura per tracciare i servizi provati per ogni traccia
if 'tried_services' not in st.session_state:
    st.session_state['tried_services'] = {}

# Make sure all required session state keys are initialized
required_keys = [
    'downloaded_files', 'pending_tracks', 'log_messages', 'spotify_tracks_cache',
    'last_cache_update', 'browser_pool', 'user_agent_index', 'proxy_index',
    'download_progress', 'download_errors', 'servizi_disponibili', 'tried_services'
]

for key in required_keys:
    if key not in st.session_state:
        if key in ['downloaded_files', 'pending_tracks', 'log_messages', 'browser_pool', 'servizi_disponibili']:
            st.session_state[key] = []
        elif key in ['spotify_tracks_cache', 'last_cache_update', 'download_progress', 'download_errors', 'tried_services']:
            st.session_state[key] = {}
        elif key in ['user_agent_index', 'proxy_index']:
            st.session_state[key] = 0

# 6. Configurazione Selenium Avanzata
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
PROXY_LIST = []  # Popola questa lista se vuoi usare i proxy

# 7. Pulizia Risorse
TEMP_FILE_RETENTION = timedelta(hours=1)
CACHE_MAX_SIZE = 100 * 1024 * 1024  # 100MB

# Funzione per ottenere il prossimo user agent
def get_next_user_agent():
    if 'user_agent_index' not in st.session_state:
        st.session_state['user_agent_index'] = 0
    user_agent = USER_AGENTS[st.session_state['user_agent_index'] % len(USER_AGENTS)]
    st.session_state['user_agent_index'] += 1
    return user_agent

# Funzione per ottenere il prossimo proxy
def get_next_proxy():
    if 'proxy_index' not in st.session_state:
        st.session_state['proxy_index'] = 0
    if PROXY_LIST:
        proxy = PROXY_LIST[st.session_state['proxy_index'] % len(PROXY_LIST)]
        st.session_state['proxy_index'] += 1
        return proxy
    return None

# Configura le opzioni di Chrome
def get_thread_safe_chrome_options(download_dir, use_proxy=False):
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

# Funzione per creare una nuova istanza del browser
def create_thread_safe_browser_instance(download_dir, use_proxy=False):
    try:
        return webdriver.Chrome(options=get_thread_safe_chrome_options(download_dir, use_proxy))
    except Exception as e:
        print(f"Errore nella creazione del browser: {str(e)}")
        try:
            return webdriver.Chrome(service=Service("/usr/bin/chromedriver"),
                                    options=get_thread_safe_chrome_options(download_dir, use_proxy))
        except Exception as e2:
            print(f"Secondo tentativo fallito: {str(e2)}")
            raise

# 3. Ottimizzazione Performance - Browser Pool
def get_browser_from_pool(download_dir, use_proxy=False):
    if st.session_state['browser_pool']:
        return st.session_state['browser_pool'].pop()
    return create_thread_safe_browser_instance(download_dir, use_proxy)

def return_browser_to_pool(browser):
    if browser:
        try:
            st.session_state['browser_pool'].append(browser)
        except Exception as e:
            safe_browser_quit(browser)
            st.error(f"Errore nel ritorno del browser al pool: {str(e)}")

# Formati disponibili
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

# Mappa delle qualità disponibili per ogni formato
QUALITY_MAP = {
    "original": [{"value": "", "text": "Non applicabile"}],
    "flac": [{"value": "16", "text": "16-bit 44.1kHz"}],
    "mp3": [
        {"value": "320", "text": "320 kbps"},
        {"value": "256", "text": "256 kbps"},
        {"value": "192", "text": "192 kbps"},
        {"value": "128", "text": "128 kbps"}
    ],
    "ogg-vorbis": [
        {"value": "320", "text": "320 kbps"},
        {"value": "256", "text": "256 kbps"},
        {"value": "192", "text": "192 kbps"},
        {"value": "128", "text": "128 kbps"}
    ],
    "opus": [
        {"value": "320", "text": "320 kbps"},
        {"value": "256", "text": "256 kbps"},
        {"value": "192", "text": "192 kbps"},
        {"value": "128", "text": "128 kbps"},
        {"value": "96", "text": "96 kbps"},
        {"value": "64", "text": "64 kbps"}
    ],
    "m4a-aac": [
        {"value": "320", "text": "320 kbps"},
        {"value": "256", "text": "256 kbps"},
        {"value": "192", "text": "192 kbps"},
        {"value": "128", "text": "128 kbps"}
    ],
    "wav": [{"value": "", "text": "Non applicabile"}],
    "bitcrush": [{"value": "", "text": "Non applicabile"}]
}

# Funzione per separare artista e traccia
def split_title(full_title):
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, full_title.strip()

# Funzione per normalizzare gli artisti
def normalize_artist(artist_string):
    if not artist_string:
        return ""
    normalized = artist_string.split(',')[0].strip().lower()
    normalized = normalized.replace(" & ", " and ").replace(" ", " ")
    return normalized

# 2. Gestione degli Errori Migliorata - Controllo File Corrotti
def is_file_complete(filepath, expected_extension):
    if not os.path.exists(filepath):
        return False
    if filepath.endswith(".crdownload"):
        return False
    if not filepath.lower().endswith(expected_extension.lower()):
        return False
    return os.path.getsize(filepath) > 0

# Funzione per aspettare il download
def wait_for_download(download_dir, existing_files, formato, track_key, timeout=360):
    start_time = time.time()
    artist_part, title_part = split_title(track_key)
    title_part = title_part.lower().replace(" ", "_").replace("'", "").replace("(", "").replace(")", "")
    artist_part = artist_part.lower().replace(" ", "_").replace("'", "").replace("(", "").replace(")", "") if artist_part else ""

    is_unknown_extension = formato in ["original", "bitcrush"]
    expected_extension = formato.split('-')[0] if '-' in formato else formato
    if is_unknown_extension:
        expected_extension = None

    while time.time() - start_time < timeout:
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.*"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))

        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            if os.path.isfile(file) and not file.endswith(".crdownload") and os.path.getsize(file) > 0:
                if is_unknown_extension:
                    return True, f"Download completato: {file}", file
                elif file.lower().endswith(f".{expected_extension.lower()}"):
                    return True, f"Download completato: {file}", file

        if crdownload_files:
            time.sleep(5)
            continue

        if time.time() - start_time < 30:
            time.sleep(5)
            continue

        all_new_files = [f for f in os.listdir(download_dir) if os.path.join(download_dir, f) not in existing_files]
        for f in all_new_files:
            full_path = os.path.join(download_dir, f)
            if os.path.isfile(full_path) and not full_path.endswith(".crdownload") and os.path.getsize(full_path) > 0:
                if is_unknown_extension:
                    return True, f"Download completato: {f}", full_path
                elif full_path.lower().endswith(f".{expected_extension.lower()}"):
                    return True, f"Download completato: {f}", full_path

        time.sleep(5)

    found_files = glob.glob(os.path.join(download_dir, "*.*"))
    return False, f"Timeout raggiunto ({timeout}s), nessun file valido trovato per '{track_key}'. File nella directory: {found_files}", None

# Funzione per creare l'archivio ZIP
def create_zip_archive(downloaded_files, zip_name="tracce_scaricate.zip"):
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, zip_name)
    included_files = []
    file_count = {}

    st.session_state['log_messages'].append(f"📦 Creazione ZIP: {zip_path}")
    st.session_state['log_messages'].append(f"📋 File da includere (originali): {len(downloaded_files)}")

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in downloaded_files:
                if not file_path or not isinstance(file_path, str):
                    st.session_state['log_messages'].append(f"⚠️ Percorso non valido: {file_path}")
                    continue
                abs_file_path = os.path.abspath(file_path)
                if os.path.exists(abs_file_path) and os.path.getsize(abs_file_path) > 0:
                    basename = os.path.basename(abs_file_path)
                    if basename in file_count:
                        file_count[basename] += 1
                        name, ext = os.path.splitext(basename)
                        new_name = f"{name}_{file_count[basename]}{ext}"
                    else:
                        file_count[basename] = 0
                        new_name = basename
                    zipf.write(abs_file_path, new_name)
                    included_files.append(abs_file_path)
                    st.session_state['log_messages'].append(f"✅ Aggiunto allo ZIP come '{new_name}': {abs_file_path}")
                else:
                    st.session_state['log_messages'].append(f"❌ File non trovato o vuoto: {abs_file_path}")

        if not included_files:
            st.session_state['log_messages'].append("❌ Nessun file valido incluso nello ZIP")
            return None

        st.session_state['log_messages'].append(f"✅ ZIP creato con {len(included_files)} file: {zip_path}")
        return zip_path if os.path.exists(zip_path) else None

    except Exception as e:
        st.session_state['log_messages'].append(f"❌ Errore nella creazione dello ZIP: {str(e)}")
        return None

# Funzione per estrarre l'ID della playlist
def get_playlist_id(playlist_link):
    parsed_url = urlparse(playlist_link)
    if parsed_url.netloc not in ['open.spotify.com']:
        raise ValueError("Link Spotify non valido.")
    match = re.search(r'playlist/(\w+)', parsed_url.path)
    if match:
        return match.group(1)
    else:
        raise ValueError("Link della playlist non valido.")

# 3. Ottimizzazione Performance - Cache delle Richieste Spotify
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def _get_spotify_tracks(sp, playlist_id):
    tracks_data = []
    results = sp.playlist_tracks(playlist_id)
    tracks_data.extend(results['items'])
    while results['next']:
        results = sp.next(results)
        tracks_data.extend(results['items'])
    return tracks_data

def get_spotify_tracks(playlist_link):
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

# Funzione per ottenere i servizi disponibili
def get_available_services(browser):
    try:
        browser.get("https://lucida.to")
        time.sleep(5)
        select_service = WebDriverWait(browser, 20).until(
            EC.presence_of_element_located((By.ID, "service"))
        )
        options = select_service.find_elements(By.TAG_NAME, "option")
        return [{"index": i, "value": opt.get_attribute("value"), "text": opt.text}
                for i, opt in enumerate(options) if i > 0]
    except Exception as e:
        st.session_state['log_messages'].append(f"Errore nel recupero dei servizi: {str(e)}")
        return []

# 2. Gestione degli Errori Migliorata - Logging Strutturato
def log_error(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] ERROR: {message}\n"
    with open("error.log", "a") as f:
        f.write(log_message)
    st.session_state['log_messages'].append(f"🔴 {message}")

# Funzione principale per scaricare una traccia
def download_track_thread_safe(track_info, servizio_idx, formato_valore, qualita_valore, use_proxy=False):
    if isinstance(track_info, str):
        traccia = track_info
    else:
        traccia = f"{track_info.get('artist', '')} - {track_info.get('title', '')}"
    track_key = traccia
    browser = None
    log_messages = []
    thread_download_dir = tempfile.mkdtemp()

    try:
        options = webdriver.ChromeOptions()
        prefs = {
            "download.default_directory": thread_download_dir,
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

        browser.get("https://lucida.to")
        log_messages.append(f"🌐 Accesso a lucida.to (servizio {servizio_idx})")

        if "captcha" in browser.page_source.lower() or "cloudflare" in browser.page_source.lower():
            log_messages.append("⚠️ Rilevato CAPTCHA o protezione Cloudflare")
            return {
                "track_key": track_key,
                "success": False,
                "downloaded_file": None,
                "log": log_messages,
                "status": "❌ Bloccato da protezione"
            }

        input_field = WebDriverWait(browser, 20).until(EC.element_to_be_clickable((By.ID, "download")))
        input_field.clear()
        input_field.send_keys(traccia)
        log_messages.append(f"✍️ Campo input compilato: '{traccia}'")
        time.sleep(2)

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
        browser.execute_script("""
            var select = arguments[0];
            var valore = arguments[1];
            select.value = valore;
            var events = ['mousedown', 'click', 'change', 'input', 'blur'];
            events.forEach(function(eventType) {
                var event = new Event(eventType, { bubbles: true });
                select.dispatchEvent(event);
            });
            var svelteEvent = new CustomEvent('svelte-change', { bubbles: true });
            select.dispatchEvent(svelteEvent);
        """, select_service, servizio_valore)
        log_messages.append(f"🔧 Servizio {servizio_idx} selezionato: {opzioni_service[servizio_idx].text}")
        time.sleep(5)

        WebDriverWait(browser, 60).until(
            lambda d: len(d.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0,
            message="Timeout attesa opzioni 'country'"
        )
        log_messages.append("✅ Opzioni 'country' caricate")
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
        time.sleep(1)

        go_button = WebDriverWait(browser, 20).until(EC.element_to_be_clickable((By.ID, "go")))
        go_button.click()
        log_messages.append("▶️ Pulsante 'go' cliccato")

        try:
            WebDriverWait(browser, 240).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or "No results found" in d.page_source
            )
            log_messages.append("🔍 Risultati caricati con successo")
        except Exception as e:
            log_messages.append(f"⚠️ Timeout attesa risultati: {str(e)}")
            log_messages.append(f"📄 Stato pagina: {browser.page_source[:500]}...")
            return {
                "track_key": track_key,
                "success": False,
                "downloaded_file": None,
                "log": log_messages,
                "status": f"❌ Errore: Timeout caricamento risultati - {str(e)}"
            }
        time.sleep(15)

        titoli = browser.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
        artisti = browser.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")
        log_messages.append(f"📋 Risultati trovati: {len(titoli)} titoli")

        best_match_found = False
        for i, titolo in enumerate(titoli):
            titolo_testo = titolo.text.strip().lower()
            traccia_testo = traccia_input.lower().strip()
            titolo_normalizzato = re.sub(r'[^\w\s]', '', titolo_testo)
            traccia_normalizzata = re.sub(r'[^\w\s]', '', traccia_testo)
            parole_traccia = set(traccia_normalizzata.split())
            parole_titolo = set(titolo_normalizzato.split())
            match = len(parole_traccia.intersection(parole_titolo)) / len(parole_traccia) if parole_traccia else 0

            log_messages.append(f"🔍 Confronto: '{traccia_normalizzata}' con '{titolo_normalizzato}' (Match: {match:.2%})")

            if (match >= 0.5 and len(titoli) > 1) or (match >= 0.2 and len(titoli) == 1) or traccia_normalizzata in titolo_normalizzato:
                if artista_input and i < len(artisti):
                    artista_risultato = artisti[i].text.strip().lower()
                    artista_normalizzato = normalize_artist(artista_input)
                    artista_normalizzato_and = artista_normalizzato.replace("&", "and")
                    artista_normalizzato_e = artista_normalizzato.replace("&", "e")
                    artist_match = (
                        artista_normalizzato in artista_risultato or
                        artista_normalizzato_and in artista_risultato or
                        artista_normalizzato_e in artista_risultato or
                        artista_risultato in artista_normalizzato
                    )
                    if not artist_match and match < 0.8:
                        log_messages.append(f"⚠️ Artista non corrispondente: '{artista_normalizzato}' vs '{artista_risultato}'")
                        continue

                browser.execute_script("arguments[0].scrollIntoView(true);", titolo)
                time.sleep(1)
                titolo.click()
                log_messages.append(f"✅ Traccia trovata e cliccata: '{titolo_normalizzato}'")
                best_match_found = True
                break

        if not best_match_found:
            log_messages.append(f"❌ Traccia non trovata in servizio {servizio_idx}")
            return {
                "track_key": track_key,
                "success": False,
                "downloaded_file": None,
                "log": log_messages,
                "status": "❌ Errore: Traccia non trovata"
            }

        time.sleep(5)

        select_convert = Select(WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "convert"))))
        select_convert.select_by_value(formato_valore)
        log_messages.append(f"🎧 Formato selezionato: {formato_valore}")
        time.sleep(1)

        if formato_valore not in ["original", "wav", "bitcrush"] and qualita_valore:
            try:
                select_downsetting = Select(WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "downsetting"))))
                select_downsetting.select_by_value(qualita_valore)
                log_messages.append(f"🔊 Qualità selezionata: {qualita_valore}")
            except Exception as e:
                log_messages.append(f"⚠️ Errore selezione qualità: {str(e)}")
                return {
                    "track_key": track_key,
                    "success": False,
                    "downloaded_file": None,
                    "log": log_messages,
                    "status": f"❌ Errore: Qualità non valida - {str(e)}"
                }
        else:
            log_messages.append("🔊 Nessuna qualità da selezionare per questo formato")
        time.sleep(1)

        existing_files = [os.path.abspath(f) for f in glob.glob(os.path.join(thread_download_dir, "*.*"))]
        download_button = WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.CLASS_NAME, "download-button")))
        browser.execute_script("arguments[0].scrollIntoView(true);", download_button)
        time.sleep(1)
        download_button.click()
        log_messages.append("⬇️ Pulsante di download cliccato")

        extension_map = {
            "flac": "flac",
            "mp3": "mp3",
            "ogg-vorbis": "ogg",
            "opus": "opus",
            "m4a-aac": "m4a",
            "wav": "wav"
        }
        success, message, downloaded_file = wait_for_download(thread_download_dir, existing_files, formato_valore, track_key)
        log_messages.append(message)
        if success and downloaded_file:
            if formato_valore in ["original", "bitcrush"]:
                expected_extension = os.path.splitext(downloaded_file)[1].lstrip(".").lower()
                if not expected_extension:
                    expected_extension = "mp3"
                    log_messages.append("⚠️ Estensione non rilevata, utilizzo fallback .mp3")
            else:
                expected_extension = extension_map.get(formato_valore, "mp3")
            
            new_filename = f"{track_key.replace('/', '_').replace(':', '_')}.{expected_extension}"
            new_filepath = os.path.join(thread_download_dir, new_filename)
            try:
                os.rename(downloaded_file, new_filepath)
                downloaded_file = new_filepath
                log_messages.append(f"✨ File rinominato: {new_filename}")
            except OSError as e:
                log_messages.append(f"⚠️ Errore nella rinominazione del file: {str(e)}")
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
        log_messages.append(f"📄 Stato pagina: {browser.page_source[:500] if browser else 'Browser non disponibile'}...")
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

# 7. Pulizia Risorse - Autopulizia File
def cleanup_temp_files():
    now = datetime.now()
    temp_dirs = set(os.path.dirname(f) for f in st.session_state.get('downloaded_files', []))
    for temp_dir in temp_dirs:
        if os.path.exists(temp_dir):
            for filename in os.listdir(temp_dir):
                filepath = os.path.join(temp_dir, filename)
                if os.path.isfile(filepath):
                    file_creation_time = datetime.fromtimestamp(os.path.getctime(filepath))
                    if now - file_creation_time > TEMP_FILE_RETENTION:
                        try:
                            os.remove(filepath)
                            st.session_state['log_messages'].append(f"🗑️ File temporaneo eliminato: {filename}")
                        except Exception as e:
                            st.session_state['log_messages'].append(f"⚠️ Errore nell'eliminazione di {filename}: {e}")
            try:
                os.rmdir(temp_dir)
                st.session_state['log_messages'].append(f"🗑️ Directory temporanea rimossa: {temp_dir}")
            except OSError:
                pass

# Miglioramento della gestione degli errori di Selenium
def safe_browser_quit(browser):
    if browser:
        try:
            browser.quit()
        except Exception as e:
            print(f"Errore durante la chiusura del browser: {e}")

def cleanup_browser_pool():
    if 'browser_pool' in st.session_state:
        for browser in st.session_state['browser_pool']:
            safe_browser_quit(browser)
        st.session_state['browser_pool'] = []

def close_all_browsers():
    if 'browser_pool' in st.session_state:
        for browser in st.session_state['browser_pool']:
            try:
                browser.quit()
            except Exception as e:
                st.session_state['log_messages'].append(f"Errore nella chiusura del browser: {str(e)}")
        st.session_state['browser_pool'] = []

import atexit
atexit.register(cleanup_browser_pool)

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali (PIZZUNA)")

st.warning("⚠️ Prima di scaricare, assicurati di rispettare le leggi sul copyright e i termini di servizio delle piattaforme musicali. Per problemi di band limit legati all´host attuale, consigliamo di dare in pasto playlist con non più di 25 tracce.")

# Configurazione Proxy
use_proxy = st.sidebar.checkbox("Usa Proxy", False)

# Carica i servizi disponibili
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
                try:
                    temp_browser.quit()
                except:
                    pass
            if st.session_state['servizi_disponibili']:
                st.success(f"Caricati {len(st.session_state['servizi_disponibili'])} servizi disponibili.")
            else:
                st.warning("Impossibile caricare i servizi disponibili.")
                st.session_state['servizi_disponibili'] = [{"index": 1, "value": "1", "text": "Servizio predefinito"}]
        except Exception as e:
            st.error(f"Errore durante il caricamento dei servizi: {str(e)}")
            st.session_state['servizi_disponibili'] = [{"index": 1, "value": "1", "text": "Servizio predefinito"}]

# Preferenze di download
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
    qualita_disponibili = QUALITY_MAP.get(formato_valore, [{"value": "", "text": "Non applicabile"}])
    qualita_opzioni = {q["text"]: q["value"] for q in qualita_disponibili}
    qualita_selezionata = st.selectbox("Qualità audio", options=list(qualita_opzioni.keys()), index=0)
    qualita_valore = qualita_opzioni[qualita_selezionata]

num_threads = st.slider("Numero di download paralleli", min_value=1, max_value=5, value=2, help="Un numero inferiore riduce il rischio di blocchi.")

# Playlist Spotify
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

# Upload file
uploaded_file = st.file_uploader("Oppure carica il file tracce.txt (artista - titolo)", type=["txt"])

# 4. Usabilità - Anteprima Brani
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

# 8. Notifiche e Feedback
if 'downloaded_files' in st.session_state and st.session_state['downloaded_files'] and st.session_state.get('download_started', False):
    st.balloons()
    st.success(f"🎉 Download completato! {len(st.session_state['downloaded_files'])} tracce scaricate con successo.")
    st.session_state['download_started'] = False

if st.button("Avvia Download", key="avvia_download_button") and tracks_to_download:
    st.session_state['download_started'] = True
    st.session_state['downloaded_files'] = []
    st.session_state['log_messages'] = []
    st.session_state['pending_tracks'] = []
    st.session_state['tried_services'] = {}

    track_status = {f"{t.get('artist', '')} - {t.get('title', '')}": "In attesa..." for t in tracks_to_download}
    st.session_state['download_progress'] = track_status.copy()
    st.session_state['download_errors'] = {}

    progress_bar = st.progress(0)
    num_tracks = len(tracks_to_download)
    downloaded_count = 0

    download_results_container = st.container()
    with download_results_container:
        status_placeholder = st.empty()

    # Elenco dei servizi disponibili
    available_services = [s['index'] for s in st.session_state['servizi_disponibili']]
    if not available_services:
        st.error("Nessun servizio disponibile per il download.")
        st.stop()

    # Ciclo per gestire i tentativi su tutti i servizi
    pending_tracks = tracks_to_download.copy()
    while pending_tracks:
        current_pending = []
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = {}
                for track in pending_tracks:
                    track_key = f"{track.get('artist', '')} - {track.get('title', '')}"
                    # Determina il prossimo servizio da provare
                    tried_services = st.session_state['tried_services'].get(track_key, [])
                    available_for_track = [s for s in available_services if s not in tried_services]
                    if not available_for_track:
                        current_pending.append(track)
                        st.session_state['log_messages'].append(f"❌ Tutti i servizi provati per {track_key}")
                        continue
                    current_service = available_for_track[0]  # Prendi il primo servizio non provato
                    futures[executor.submit(
                        download_track_thread_safe,
                        track, current_service, formato_valore, qualita_valore, use_proxy
                    )] = (track, current_service)

                downloaded_files = st.session_state['downloaded_files']

                for future in concurrent.futures.as_completed(futures):
                    track, current_service = futures[future]
                    track_key = f"{track.get('artist', '')} - {track.get('title', '')}"
                    try:
                        result = future.result()
                        track_status[track_key] = result["status"]
                        st.session_state['log_messages'].extend(result["log"])

                        # Aggiorna i servizi provati
                        if track_key not in st.session_state['tried_services']:
                            st.session_state['tried_services'][track_key] = []
                        st.session_state['tried_services'][track_key].append(current_service)

                        if result["success"] and result["downloaded_file"]:
                            downloaded_files.append(result["downloaded_file"])
                            downloaded_count += 1
                            st.session_state['log_messages'].append(f"✅ Traccia scaricata con successo: {track_key} -> {result['downloaded_file']}")
                        else:
                            available_for_track = [s for s in available_services if s not in st.session_state['tried_services'][track_key]]
                            if available_for_track:
                                current_pending.append(track)
                                st.session_state['download_errors'][track_key] = result["log"]
                                st.session_state['log_messages'].append(f"🔄 Traccia {track_key} fallita su servizio {current_service}, verranno provati altri servizi")
                            else:
                                st.session_state['download_errors'][track_key] = result["log"]
                                st.session_state['log_messages'].append(f"❌ Traccia {track_key} fallita su tutti i servizi")

                        progress_value = downloaded_count / num_tracks
                        progress_bar.progress(progress_value)

                    except Exception as e:
                        track_status[track_key] = f"❌ Errore: {str(e)}"
                        if track_key not in st.session_state['tried_services']:
                            st.session_state['tried_services'][track_key] = []
                        st.session_state['tried_services'][track_key].append(current_service)
                        available_for_track = [s for s in available_services if s not in st.session_state['tried_services'][track_key]]
                        if available_for_track:
                            current_pending.append(track)
                            st.session_state['download_errors'][track_key] = [f"Errore durante l'elaborazione: {str(e)}"]
                            st.session_state['log_messages'].append(f"🔄 Errore critico per {track_key} su servizio {current_service}, verranno provati altri servizi")
                        else:
                            st.session_state['download_errors'][track_key] = [f"Errore durante l'elaborazione: {str(e)}"]
                            st.session_state['log_messages'].append(f"❌ Errore critico per {track_key} su tutti i servizi")

                    status_text = "<h3>Stato Download in corso:</h3>"
                    for tk, status in track_status.items():
                        status_class = "info" if "In attesa" in status else "success" if "✅" in status else "error"
                        status_text += f"<div class='{status_class}'>{tk}: {status}</div>"
                    status_placeholder.markdown(status_text, unsafe_allow_html=True)

                st.session_state['downloaded_files'] = downloaded_files
                st.session_state['log_messages'].append(f"📥 File scaricati registrati: {len(downloaded_files)}")
                for file in downloaded_files:
                    st.session_state['log_messages'].append(f" - {file}")

        except Exception as e:
            st.session_state['log_messages'].append(f"❌ Errore durante l'esecuzione dei thread: {str(e)}")
            break

        pending_tracks = current_pending
        st.session_state['pending_tracks'] = [f"{t.get('artist', '')} - {t.get('title', '')}" for t in pending_tracks]
        if pending_tracks:
            st.session_state['log_messages'].append(f"🔄 {len(pending_tracks)} tracce in attesa di ulteriori tentativi su altri servizi")

    # Stato finale e log
    if st.session_state.get('mostra_log_completo', False):
        st.subheader("Log Completo")
        for log_message in st.session_state['log_messages']:
            st.write(log_message)

    status_text = "<h3>Stato Download Finale:</h3>"
    for track_key, status in st.session_state['download_progress'].items():
        status_class = "info" if "In attesa" in status else "success" if "✅" in status else "error"
        status_text += f"<div class='{status_class}'>{track_key}: {status}</div>"
    status_placeholder.markdown(status_text, unsafe_allow_html=True)

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

# Sidebar
st.sidebar.subheader("Disclaimer")
st.sidebar.info("""
Questo strumento è fornito a scopo didattico e per uso personale.
L'utente è responsabile del rispetto delle leggi sul copyright
e dei termini di servizio delle piattaforme musicali.
Il download di materiale protetto da copyright senza autorizzazione
è illegale. Gli sviluppatori non si assumono alcuna responsabilità
per un uso improprio di questo strumento.
""")

if st.sidebar.button("Pulisci Sessione"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

with st.sidebar.expander("Impostazioni Avanzate"):
    if st.button("Ricarica Servizi"):
        st.session_state['servizi_disponibili'] = []
        st.rerun()
    st.session_state['mostra_log_completo'] = st.checkbox("Mostra log completo")
    if st.session_state['mostra_log_completo']:
        st.subheader("Log Completo")
        for log_message in st.session_state.get('log_messages', []):
            st.write(log_message)

if st.sidebar.checkbox("Modalità Sorpresa?"):
    st.sidebar.markdown("![Pizzuna](https://i.imgur.com/your_pizzuna_image.png)")
    st.markdown("## 🍕 Un tocco di Pizzuna! 🍕")

st.markdown("---")
st.info("L'applicazione è stata potenziata con diverse ottimizzazioni e nuove funzionalità. Ulteriori miglioramenti potrebbero essere implementati in futuro.")

# Sezione Download
if st.session_state.get('downloaded_files'):
    st.subheader("Scarica le tracce")
    zip_filename = "tracce_scaricate.zip"
    zip_path = create_zip_archive(st.session_state['downloaded_files'], zip_filename)
    if zip_path:
        with open(zip_path, "rb") as f:
            st.download_button(
                label="Scarica tutte le tracce come ZIP",
                data=f,
                file_name=zip_filename,
                mime="application/zip",
                key="download_zip_button"
            )
        if st.button("Pulisci file temporanei dopo il download", key="cleanup_button"):
            cleanup_temp_files()
            st.session_state['log_messages'].append("🗑️ File temporanei puliti manualmente")
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
    .info {
        padding: 5px;
        background-color: #40484d;
        border-left: 5px solid #2196F3;
        margin: 5px 0;
    }
    .success {
        padding: 5px;
        background-color: #012600;
        border-left: 5px solid #4CAF50;
        margin: 5px 0;
    }
    .error {
        padding: 5px;
        background-color: #52020e;
        border-left: 5px solid #f44336;
        margin: 5px 0;
    }
</style>
""", unsafe_allow_html=True)
