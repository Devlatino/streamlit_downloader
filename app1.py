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

# 1. Sicurezza e Conformità Legale
# Rimuovi Credenziali Hardcoded: Utilizza secrets di Streamlit
CLIENT_ID = st.secrets.get('SPOTIFY', {}).get('CLIENT_ID')
CLIENT_SECRET = st.secrets.get('SPOTIFY', {}).get('CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    st.error("Le credenziali Spotify non sono state configurate in Streamlit Secrets.")
    st.stop()

# Inizializza lo stato della sessione
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = []
if 'pending_tracks' not in st.session_state:
    st.session_state.pending_tracks = []
if 'log_messages' not in st.session_state:
    st.session_state.log_messages = []
if 'spotify_tracks_cache' not in st.session_state:
    st.session_state.spotify_tracks_cache = {}
if 'last_cache_update' not in st.session_state:
    st.session_state.last_cache_update = {}
if 'browser_pool' not in st.session_state:
    st.session_state.browser_pool = []
if 'user_agent_index' not in st.session_state:
    st.session_state.user_agent_index = 0
if 'proxy_index' not in st.session_state:
    st.session_state.proxy_index = 0
if 'download_progress' not in st.session_state:
    st.session_state.download_progress = {}
if 'download_errors' not in st.session_state:
    st.session_state.download_errors = {}

# 6. Configurazione Selenium Avanzata
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Aggiungi altri user agent
]
PROXY_LIST = [] # Popola questa lista se vuoi usare i proxy

# Configura la directory di download
download_dir = tempfile.mkdtemp()
st.write(f"Directory di download: {download_dir} (Permessi: {os.access(download_dir, os.W_OK)})")

# 7. Pulizia Risorse
TEMP_FILE_RETENTION = timedelta(hours=1)
CACHE_MAX_SIZE = 100 * 1024 * 1024  # 100MB

# Funzione per ottenere il prossimo user agent
def get_next_user_agent():
    user_agent = USER_AGENTS[st.session_state.user_agent_index % len(USER_AGENTS)]
    st.session_state.user_agent_index += 1
    return user_agent

# Funzione per ottenere il prossimo proxy
def get_next_proxy():
    if PROXY_LIST:
        proxy = PROXY_LIST[st.session_state.proxy_index % len(PROXY_LIST)]
        st.session_state.proxy_index += 1
        return proxy
    return None

# Configura le opzioni di Chrome
def get_chrome_options(use_proxy=False):
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
    options.add_argument(f"user-agent={get_next_user_agent()}")
    proxy = get_next_proxy()
    if use_proxy and proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return options

# Funzione per creare una nuova istanza del browser
def create_browser_instance(use_proxy=False):
    return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=get_chrome_options(use_proxy))

# 3. Ottimizzazione Performance - Browser Pool
def get_browser_from_pool(use_proxy=False):
    if st.session_state.browser_pool:
        return st.session_state.browser_pool.pop()
    return create_browser_instance(use_proxy)

def return_browser_to_pool(browser):
    if browser:
        st.session_state.browser_pool.append(browser)

# Servizi disponibili
if 'servizi_disponibili' not in st.session_state:
    st.session_state.servizi_disponibili = []

# Formati e qualità disponibili
FORMATI_DISPONIBILI = {
    "m4a-aac": "AAC (.m4a)",
    "mp3": "MP3 (.mp3)",
    "flac": "FLAC (.flac)",
    "wav": "WAV (.wav)",
    "ogg": "OGG (.ogg)"
}
QUALITA_DISPONIBILI = {
    "320": "320 kbps (Alta)",
    "256": "256 kbps (Media-Alta)",
    "192": "192 kbps (Media)",
    "128": "128 kbps (Bassa)"
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
    return artist_string.split(',')[0].strip().lower()

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
def wait_for_download(download_dir, existing_files, formato, timeout=180):
    start_time = time.time()
    expected_extension = formato.split('-')[0] if '-' in formato else formato

    while time.time() - start_time < timeout:
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, f"*.{expected_extension}"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))

        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            if is_file_complete(file, expected_extension):
                return True, f"Download completato: {file}", file

        if crdownload_files:
            time.sleep(5)
            continue

        if time.time() - start_time < 30:
            time.sleep(5)
            continue

        all_new_files = [f for f in os.listdir(download_dir) if os.path.join(download_dir, f) not in existing_files]
        if all_new_files:
            for f in all_new_files:
                full_path = os.path.join(download_dir, f)
                if os.path.isfile(full_path) and is_file_complete(full_path, expected_extension):
                    return True, f"Download completato: {f}", full_path

        time.sleep(5)

    return False, f"Timeout raggiunto ({timeout}s), nessun download completato.", None

# Funzione per creare l'archivio ZIP
def create_zip_archive(download_dir, downloaded_files, zip_name="tracce_scaricate.zip"):
    zip_path = os.path.join(download_dir, zip_name)
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in downloaded_files:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    zipf.write(file_path, os.path.basename(file_path))
        return zip_path if os.path.exists(zip_path) else None
    except Exception as e:
        st.session_state.log_messages.append(f"Errore nella creazione dello ZIP: {str(e)}")
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

# 3. Ottimizzazione Performance - Cache delle Richieste Spotify & 2. Gestione degli Errori - Ritentativi
@st.cache_data(ttl=86400)
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
        st.session_state.log_messages.append(f"Errore nel recupero delle tracce da Spotify: {str(e)}")
        return None

# Funzione per ottenere i servizi disponibili
def get_available_services(browser):
    try:
        browser.get("https://lucida.su")
        time.sleep(5)
        select_service = WebDriverWait(browser, 20).until(
            EC.presence_of_element_located((By.ID, "service"))
        )
        options = select_service.find_elements(By.TAG_NAME, "option")
        return [{"index": i, "value": opt.get_attribute("value"), "text": opt.text}
                for i, opt in enumerate(options) if i > 0]
    except Exception as e:
        st.session_state.log_messages.append(f"Errore nel recupero dei servizi: {str(e)}")
        return []

# 2. Gestione degli Errori Migliorata - Logging Strutturato
def log_error(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] ERROR: {message}\n"
    with open("error.log", "a") as f:
        f.write(log_message)
    st.session_state.log_messages.append(f"🔴 {message}")

# Funzione principale per scaricare una traccia
def _download_single_track(track_info, servizio_idx, formato_valore, qualita_valore, use_proxy=False):
    browser = get_browser_from_pool(use_proxy)
    log = []
    downloaded_file = None
    success = False
    artist = track_info['artist']
    title = track_info['title']
    search_query = f"{artist} - {title}"
    expected_extension = formato_valore.split('-')[0] if '-' in formato_valore else formato_valore

    try:
        log.append(f"🎤 Artista: {artist} | 🎵 Traccia: {title}")
        if use_proxy and get_next_proxy():
            log.append(f"🌐 Utilizzo proxy: {get_next_proxy()}")
        else:
            log.append("🌐 Nessun proxy configurato.")

        browser.get("https://lucida.su")
        log.append(f"🌐 Accesso a lucida.su (servizio {servizio_idx})")
        time.sleep(random.uniform(2, 5))

        input_field = WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "download")))
        input_field.clear()
        input_field.send_keys(search_query)
        time.sleep(random.uniform(1, 3))
        log.append("✍️ Campo input compilato")

        select_service = WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "service")))
        opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
        if servizio_idx >= len(opzioni_service):
            log.append(f"⚠️ Indice {servizio_idx} non valido per 'service'")
            return False, None, log

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
        log.append(f"🔧 Servizio {servizio_idx} selezionato: {opzioni_service[servizio_idx].text}")
        time.sleep(random.uniform(3, 7))

        WebDriverWait(browser, 60).until(lambda d: len(d.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0)
        select_country = Select(browser.find_element(By.ID, "country"))
        if not select_country.options:
            log.append(f"⚠️ Nessuna opzione in 'country' perservizio {servizio_idx}")
            return False, None, log
        select_country.select_by_index(0)
        log.append(f"🌍 Paese selezionato: {select_country.first_selected_option.text}")
        time.sleep(random.uniform(1, 3))

        go_button = WebDriverWait(browser, 30).until(EC.element_to_be_clickable((By.ID, "go")))
        go_button.click()
        log.append("▶️ Pulsante 'go' cliccato")
        time.sleep(random.uniform(5, 10))

        WebDriverWait(browser, 90).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or "No results found" in d.page_source
        )
        titoli = browser.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
        artisti_risultato = browser.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")
        log.append(f"📋 Risultati trovati: {len(titoli)} titoli")

        found_track = False
        for i, titolo in enumerate(titoli):
            titolo_testo = titolo.text.strip().lower()
            traccia_testo = title.lower()
            parole_traccia = set(traccia_testo.split())
            parole_titolo = set(titolo_testo.split())
            match = len(parole_traccia.intersection(parole_titolo)) / len(parole_traccia) if parole_traccia else 0

            log.append(f"🔍 Confronto: '{traccia_testo}' con '{titolo_testo}' (Match: {match:.2%})")
            if match >= 0.7 or traccia_testo in titolo_testo:
                if artist and i < len(artisti_risultato):
                    artista_normalizzato = normalize_artist(artist)
                    artista_trovato = artisti_risultato[i].text.strip().lower()
                    if artista_normalizzato and artista_normalizzato not in artista_trovato and match < 0.9:
                        log.append(f"⚠️ Artista non corrispondente: '{artista_normalizzato}' vs '{artista_trovato}'")
                        continue

                browser.execute_script("arguments[0].scrollIntoView(true);", titolo)
                time.sleep(random.uniform(0.5, 2))
                titolo.click()
                log.append(f"✅ Traccia trovata e cliccata: '{titolo_testo}'")
                found_track = True
                break
        if not found_track:
            log.append(f"❌ Traccia non trovata in servizio {servizio_idx}")
            return False, None, log

        time.sleep(random.uniform(5, 10))

        select_convert = Select(WebDriverWait(browser, 45).until(EC.element_to_be_clickable((By.ID, "convert"))))
        select_convert.select_by_value(formato_valore)
        log.append(f"🎧 Formato selezionato")
        time.sleep(random.uniform(1, 3))

        select_downsetting = Select(WebDriverWait(browser, 45).until(EC.element_to_be_clickable((By.ID, "downsetting"))))
        select_downsetting.select_by_value(qualita_valore)
        log.append(f"🔊 Qualità selezionata")
        time.sleep(random.uniform(1, 3))

        existing_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.*"))]
        download_button = WebDriverWait(browser, 45).until(EC.element_to_be_clickable((By.CLASS_NAME, "download-button")))
        browser.execute_script("arguments[0].scrollIntoView(true);", download_button)
        time.sleep(random.uniform(0.5, 2))
        download_button.click()
        log.append("⬇️ Pulsante di download cliccato")

        success, message, downloaded_file = wait_for_download(download_dir, existing_files, formato_valore)
        log.append(message)
        if success:
            if not is_file_complete(downloaded_file, expected_extension):
                log_error(f"File scaricato incompleto: {downloaded_file}")
                success = False
                downloaded_file = None

    except Exception as e:
        error_msg = f"Errore durante il download di '{title}': {str(e)}"
        log.append(f"❌ {error_msg}")
        log_error(error_msg)
        success = False
    finally:
        return_browser_to_pool(browser)
        return success, downloaded_file, log

# Funzione wrapper per il download con gestione dello stato
def download_track_wrapper(track_info, servizio_indice, formato_valore, qualita_valore, use_proxy):
    track_key = f"{track_info['artist']} - {track_info['title']}"
    st.session_state.download_progress[track_key] = "In corso..."
    success, downloaded_file, log = _download_single_track(track_info, servizio_indice, formato_valore, qualita_valore, use_proxy)
    if success and downloaded_file:
        st.session_state.download_progress[track_key] = "✅ Scaricato"
        return downloaded_file
    else:
        st.session_state.download_progress[track_key] = f"❌ Errore: {log[-1] if log else 'Sconosciuto'}"
        st.session_state.download_errors[track_key] = log
        return None

# 7. Pulizia Risorse - Autopulizia File
def cleanup_temp_files():
    now = datetime.now()
    for filename in os.listdir(download_dir):
        filepath = os.path.join(download_dir, filename)
        if os.path.isfile(filepath):
            file_creation_time = datetime.fromtimestamp(os.path.getctime(filepath))
            if now - file_creation_time > TEMP_FILE_RETENTION:
                try:
                    os.remove(filepath)
                    st.session_state.log_messages.append(f"🗑️ File temporaneo eliminato: {filename}")
                except Exception as e:
                    st.session_state.log_messages.append(f"⚠️ Errore nell'eliminazione di {filename}: {e}")

# 7. Pulizia Risorse - Gestione Memoria (semplice, la cache di Streamlit è gestita da Streamlit)
def manage_cache_size():
    # La cache di st.cache_data ha una gestione implicita,
    # per cache più complesse si dovrebbe implementare una logica di rimozione (LRU, LFU, ecc.)
    pass

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali (PIZZUNA)")

# 1. Sicurezza e Conformità Legale
st.warning("⚠️ Prima di scaricare, assicurati di rispettare le leggi sul copyright e i termini di servizio delle piattaforme musicali.")

# Configurazione Proxy
use_proxy = st.sidebar.checkbox("Usa Proxy", False)

# Carica i servizi disponibili
if not st.session_state.servizi_disponibili:
    with st.spinner("Caricamento servizi disponibili..."):
        temp_browser = create_browser_instance(use_proxy)
        st.session_state.servizi_disponibili = get_available_services(temp_browser)
        return_browser_to_pool(temp_browser)
    if st.session_state.servizi_disponibili:
        st.success(f"Caricati {len(st.session_state.servizi_disponibili)} servizi disponibili.")
    else:
        st.warning("Impossibile caricare i servizi disponibili.")

# Preferenze di download
st.subheader("Preferenze di download")
if st.session_state.servizi_disponibili:
    servizio_opzioni = {f"{s['text']} (Servizio {s['index']})": s['index'] for s in st.session_state.servizi_disponibili}
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
    qualita_selezionata = st.selectbox("Qualità audio", options=list(QUALITA_DISPONIBILI.values()), index=0)
    qualita_valore = list(QUALITA_DISPONIBILI.keys())[list(QUALITA_DISPONIBILI.values()).index(qualita_selezionata)]

# Numero di thread (dinamico - implementazione semplificata)
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
            tracks_to_download.append({"title": line.strip(), "artist": None}) # Prova a scaricare solo con il titolo

if tracks_to_download:
    st.write(f"**Tracce selezionate per il download:** {len(tracks_to_download)}")
    # 4. Usabilità - Ordinamento Risultati
    sort_by = st.selectbox("Ordina per:", ["Nessuno", "Artista", "Titolo"])
    if sort_by == "Artista":
        tracks_to_download.sort(key=lambda x: x.get('artist', '').lower())
    elif sort_by == "Titolo":
        tracks_to_download.sort(key=lambda x: x.get('title', '').lower())

    st.dataframe(tracks_to_download)

    if st.button("Avvia Download"):
        st.session_state.downloaded_files = []
        st.session_state.log_messages = []
        st.session_state.pending_tracks = []
        st.session_state.download_progress = {f"{t['artist']} - {t['title']}": "In attesa..." for t in tracks_to_download}
        st.session_state.download_errors = {}
        progress_bar = st.progress(0)
        num_tracks = len(tracks_to_download)
        downloaded_count = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(download_track_wrapper, track, servizio_indice, formato_valore, qualita_valore, use_proxy)
                       for track in tracks_to_download]

            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                downloaded_file = future.result()
                track = tracks_to_download[i]
                track_key = f"{track['artist']} - {track['title']}"
                if downloaded_file:
                    st.session_state.downloaded_files.append(downloaded_file)
                    st.session_state.pending_tracks = [t for t in st.session_state.pending_tracks if t != track_key]
                    downloaded_count += 1
                else:
                    if track_key not in st.session_state.pending_tracks:
                        st.session_state.pending_tracks.append(track_key)
                progress_bar.progress((i + 1) / num_tracks)

        st.write("### Stato Download Tracce:")
        for track_key, status in st.session_state.download_progress.items():
            st.write(f"- {track_key}: {status}")

       st.write("### Riepilogo Download")
        st.write(f"**Totale tracce:** {num_tracks}")
        st.write(f"**Scaricate con successo:** {downloaded_count}")
        st.write(f"**Tracce non scaricate:** {len(st.session_state.pending_tracks)}")
        if st.session_state.pending_tracks:
            st.write("**Elenco tracce non scaricate:**")
            for track_key in st.session_state.pending_tracks:
                st.write(f"- {track_key}")
            if st.session_state.download_errors:
                with st.expander("Dettagli errori download"):
                    for track_key, errors in st.session_state.download_errors.items():
                        st.write(f"**{track_key}:**")
                        for error in errors:
                            st.write(f"- {error}")
#Pulizia iniziale del pool di browser all'avvio (se presente)
if st.session_state.browser_pool:
for browser in st.session_state.browser_pool:
try:
browser.quit()
except:
pass
st.session_state.browser_pool = []# Chiusura del browser pool alla chiusura dell'app (non direttamente gestibile in Streamlit,
# ma è buona pratica se si gestisse il ciclo di vita dell'app in modo diverso)
# import atexit
# def close_browser_pool():
#     if 'browser_pool' in st.session_state:
#         for browser in st.session_state.browser_pool:
#             try:
#                 browser.quit()
#             except:
#                 pass
# atexit.register(close_browser_pool)

# 8. Notifiche e Feedback (implementazione semplice via Streamlit)
if 'downloaded_files' in st.session_state and st.session_state.downloaded_files and st.session_state.get('download_started', False):
    st.balloons()
    st.success(f"🎉 Download completato! {len(st.session_state.downloaded_files)} tracce scaricate con successo.")
    st.session_state['download_started'] = False

if st.button("Avvia Download") and tracks_to_download:
    st.session_state['download_started'] = True# Aggiunta di un disclaimer legale più visibile all'inizio
st.sidebar.subheader("Disclaimer")
st.sidebar.info("""
Questo strumento è fornito a scopo didattico e per uso personale.
L'utente è responsabile del rispetto delle leggi sul copyright
e dei termini di servizio delle piattaforme musicali.
Il download di materiale protetto da copyright senza autorizzazione
è illegale. Gli sviluppatori non si assumono alcuna responsabilità
per un uso improprio di questo strumento.
""")

# Pulizia della sessione (utile per test)
if st.sidebar.button("Pulisci Sessione"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()# Ulteriore miglioramento usabilità: espansore per le impostazioni avanzate
with st.sidebar.expander("Impostazioni Avanzate"):
    # Opzione per forzare il ricaricamento dei servizi
    if st.button("Ricarica Servizi"):
        st.session_state.servizi_disponibili = []
        st.rerun()

    # Opzione per visualizzare il log completo
    if st.checkbox("Mostra log completo"):
        st.subheader("Log Completo")
        for log_message in st.session_state.get('log_messages', []):
            st.write(log_message)

    # Ulteriori impostazioni avanzate potrebbero essere aggiunte qui
    pass# Un piccolo easter egg (opzionale)
if st.sidebar.checkbox("Modalità Sorpresa?"):
    st.sidebar.markdown("![Pizzuna](https://i.imgur.com/your_pizzuna_image.png)") # Sostituisci con un link a un'immagine
    st.markdown("## 🍕 Un tocco di Pizzuna! 🍕")# Funzionalità futura (non implementata): Coda di download prioritaria
# st.sidebar.subheader("Coda di Download Prioritaria (Futuro)")
# priority_tracks = st.sidebar.text_area("Inserisci tracce prioritarie (una per riga)")
# # Logica per gestire la coda prioritaria andrebbe implementata nel loop di download# Ulteriore feedback visivo durante il download
if 'download_progress' in st.session_state and st.session_state.download_progress:
    st.subheader("Stato Download Dettagliato")
    for track, status in st.session_state.download_progress.items():
        if "In corso" in status:
            st.info(f"⏳ {track}: {status}")
        elif "✅ Scaricato" in status:
            st.success(f"{track}: {status}")
        elif "❌ Errore" in status:
            st.error(f"{track}: {status}")
        else:
            st.write(f"{track}: {status}")# Messaggio finale per indicare che non ci sono ulteriori implementazioni immediate
st.markdown("---")
st.info("L'applicazione è stata potenziata con diverse ottimizzazioni e nuove funzionalità. Ulteriori miglioramenti potrebbero essere implementati in futuro.")
