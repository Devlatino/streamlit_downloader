import streamlit as st
import os
import tempfile
import zipfile
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import re
from selenium import webdriver
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
import glob
import threading
from queue import Queue
import concurrent.futures

# Credenziali Spotify
CLIENT_ID = 'f147b13a0d2d40d7b5d0c3ac36b60769'
CLIENT_SECRET = '566b72290ee94a60ada9164fabb6515b'

# Inizializza lo stato della sessione
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = []
if 'pending_tracks' not in st.session_state:
    st.session_state.pending_tracks = []
if 'log_messages' not in st.session_state:
    st.session_state.log_messages = []

# Configura la directory di download
download_dir = tempfile.mkdtemp()
st.write(f"Directory di download: {download_dir} (Permessi: {os.access(download_dir, os.W_OK)})")

# Configura le opzioni di Chrome
def get_chrome_options():
    options = webdriver.ChromeOptions()
    options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    })
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--window-size=1920,1080")
    return options

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

# Funzione per aspettare il download
def wait_for_download(download_dir, existing_files, formato, timeout=180):
    start_time = time.time()
    estensione = formato.split('-')[0] if '-' in formato else formato
    
    while time.time() - start_time < timeout:
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, f"*.{estensione}"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        
        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            if os.path.getsize(file) > 0:
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
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0 and f.endswith(f'.{estensione}'):
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
    match = re.search(r'playlist/(\w+)', playlist_link)
    if match:
        return match.group(1)
    else:
        raise ValueError("Link della playlist non valido.")

# Funzione per ottenere le tracce da Spotify
def get_spotify_tracks(playlist_link):
    try:
        auth_manager = SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        playlist_id = get_playlist_id(playlist_link)
        
        tracks = []
        results = sp.playlist_tracks(playlist_id)
        tracks.extend(results['items'])
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])

        return [f"{', '.join([artist['name'] for artist in item['track']['artists']])} - {item['track']['name']}" 
                for item in tracks]
    except Exception as e:
        st.session_state.log_messages.append(f"Errore nel recupero delle tracce da Spotify: {str(e)}")
        return None

# Funzione per ottenere i servizi disponibili
def get_available_services(driver):
    try:
        driver.get("https://lucida.su")
        time.sleep(5)
        select_service = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "service"))
        )
        options = select_service.find_elements(By.TAG_NAME, "option")
        return [{"index": i, "value": opt.get_attribute("value"), "text": opt.text} 
                for i, opt in enumerate(options) if i > 0]
    except Exception as e:
        st.session_state.log_messages.append(f"Errore nel recupero dei servizi: {str(e)}")
        return []

# Funzione per scaricare una singola traccia
def download_track(traccia, servizio_idx, formato_valore, qualita_valore):
    driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=get_chrome_options())
    try:
        log = []
        artista_input, traccia_input = split_title(traccia)
        log.append(f"🎤 Artista: {artista_input} | 🎵 Traccia: {traccia_input}")

        driver.get("https://lucida.su")
        log.append(f"🌐 Accesso a lucida.su (servizio {servizio_idx})")

        # Compila il campo di ricerca
        input_field = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "download")))
        input_field.clear()
        input_field.send_keys(traccia)
        time.sleep(2)
        log.append("✍️ Campo input compilato")

        # Seleziona il servizio
        select_service = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "service")))
        opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
        if servizio_idx >= len(opzioni_service):
            log.append(f"⚠️ Indice {servizio_idx} non valido per 'service'")
            driver.quit()
            return False, None, log
        
        servizio_valore = opzioni_service[servizio_idx].get_attribute("value")
        driver.execute_script("""
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
        time.sleep(5)

        # Seleziona il paese
        WebDriverWait(driver, 60).until(lambda d: len(d.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0)
        select_country = Select(driver.find_element(By.ID, "country"))
        if not select_country.options:
            log.append(f"⚠️ Nessuna opzione in 'country' per servizio {servizio_idx}")
            driver.quit()
            return False, None, log
        select_country.select_by_index(0)
        log.append(f"🌍 Paese selezionato: {select_country.first_selected_option.text}")
        time.sleep(1)

        # Clicca "go"
        go_button = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "go")))
        go_button.click()
        log.append("▶️ Pulsante 'go' cliccato")
        time.sleep(5)

        # Cerca i risultati
        WebDriverWait(driver, 60).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or "No results found" in d.page_source
        )
        titoli = driver.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
        artisti = driver.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")
        log.append(f"📋 Risultati trovati: {len(titoli)} titoli")

        for i, titolo in enumerate(titoli):
            titolo_testo = titolo.text.strip().lower()
            traccia_testo = traccia_input.lower()
            parole_traccia = set(traccia_testo.split())
            parole_titolo = set(titolo_testo.split())
            match = len(parole_traccia.intersection(parole_titolo)) / len(parole_traccia) if parole_traccia else 0
            
            log.append(f"🔍 Confronto: '{traccia_testo}' con '{titolo_testo}' (Match: {match:.2%})")
            if match >= 0.7 or traccia_testo in titolo_testo:
                if artista_input and i < len(artisti):
                    artista_normalizzato = normalize_artist(artista_input)
                    artista_risultato = artisti[i].text.strip().lower()
                    if artista_normalizzato and artista_normalizzato not in artista_risultato and match < 0.9:
                        log.append(f"⚠️ Artista non corrispondente: '{artista_normalizzato}' vs '{artista_risultato}'")
                        continue
                
                driver.execute_script("arguments[0].scrollIntoView(true);", titolo)
                time.sleep(1)
                titolo.click()
                log.append(f"✅ Traccia trovata e cliccata: '{titolo_testo}'")
                break
        else:
            log.append(f"❌ Traccia non trovata in servizio {servizio_idx}")
            driver.quit()
            return False, None, log

        time.sleep(5)

        # Seleziona formato
        select_convert = Select(WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.ID, "convert"))))
        select_convert.select_by_value(formato_valore)
        log.append(f"🎧 Formato selezionato")
        time.sleep(1)

        # Seleziona qualità
        select_downsetting = Select(WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.ID, "downsetting"))))
        select_downsetting.select_by_value(qualita_valore)
        log.append(f"🔊 Qualità selezionata")
        time.sleep(1)

        # Avvia il download
        existing_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.*"))]
        download_button = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.CLASS_NAME, "download-button")))
        driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
        time.sleep(1)
        download_button.click()
        log.append("⬇️ Pulsante di download cliccato")

        success, message, downloaded_file = wait_for_download(download_dir, existing_files, formato_valore)
        log.append(message)
        driver.quit()
        return success, downloaded_file, log

    except Exception as e:
        log.append(f"❌ Errore durante il download: {str(e)}")
        driver.quit()
        return False, None, log

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali (PIZZUNA)")
st.write("Carica un file `tracce.txt` o inserisci un link a una playlist Spotify per scaricare le tue tracce preferite.")

# Carica i servizi disponibili
if not st.session_state.servizi_disponibili:
    with st.spinner("Caricamento servizi disponibili..."):
        driver_temp = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=get_chrome_options())
        st.session_state.servizi_disponibili = get_available_services(driver_temp)
        driver_temp.quit()
    if st.session_state.servizi_disponibili:
        st.success(f"Caricati {len(st.session_state.servizi_disponibili)} servizi disponibili.")
    else:
        st.warning("Impossibile caricare i servizi disponibili.")

# Preferenze di download
st.subheader("Preferenze di download")
servizio_opzioni = {f"{s['text']} (Servizio {s['index']})": s['index'] for s in st.session_state.servizi_disponibili}
servizio_selezionato = st.selectbox("Servizio preferito", options=list(servizio_opzioni.keys()), index=0)
servizio_indice = servizio_opzioni[servizio_selezionato]

col1, col2 = st.columns(2)
with col1:
    formato_selezionato = st.selectbox("Formato audio", options=list(FORMATI_DISPONIBILI.values()), index=0)
    formato_valore = list(FORMATI_DISPONIBILI.keys())[list(FORMATI_DISPONIBILI.values()).index(formato_selezionato)]
with col2:
    qualita_selezionata = st.selectbox("Qualità audio", options=list(QUALITA_DISPONIBILI.values()), index=0)
    qualita_valore = list(QUALITA_DISPONIBILI.keys())[list(QUALITA_DISPONIBILI.values()).index(qualita_selezionata)]

# Numero di thread
num_threads = st.slider("Numero di download paralleli", min_value=1, max_value=5, value=2, help="Più thread possono velocizzare il processo, ma potrebbero sovraccaricare il sito o il sistema.")

# Playlist Spotify
st.subheader("Genera tracce.txt da Spotify")
playlist_link = st.text_input("Link della playlist Spotify")
if playlist_link and st.button("Genera tracce.txt da Spotify"):
    tracks = get_spotify_tracks(playlist_link)
    if tracks:
        temp_file_path = os.path.join(download_dir, "tracce.txt")
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            for track in tracks:
                f.write(track + '\n')
        st.success(f"File `tracce.txt` generato con successo! Contiene {len(tracks)} tracce.")
        st.session_state['spotify_file'] = temp_file_path

# Upload file
uploaded_file = st.file_uploader("Oppure carica il file tracce.txt", type=["txt"])

# Processa le tracce
if 'spotify_file' in st.session_state and os.path.exists(st.session_state['spotify_file']):
    tracce_source = st.session_state['spotify_file']
elif uploaded_file is not None:
    tracce_source = uploaded_file
else:
    tracce_source = None

if tracce_source:
    if isinstance(tracce_source, str):
        with open(tracce_source, 'r', encoding='utf-8') as f:
            tracce = [line.strip() for line in f.readlines() if line.strip()]
    else:
        tracce = tracce_source.read().decode("utf-8").splitlines()
        tracce = [traccia.strip() for traccia in tracce if traccia.strip()]
    
    tracce_totali = len(tracce)
    st.write(f"**Numero totale di tracce da scaricare:** {tracce_totali}")

    if st.button("Avvia Download"):
        st.session_state.downloaded_files = []
        st.session_state.log_messages = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()

        def update_progress(tracce_scaricate):
            progress_bar.progress(tracce_scaricate / tracce_totali)
            status_text.text(f"✅ {tracce_scaricate}/{tracce_totali} tracce scaricate")

        # Esegui i download in parallelo
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            future_to_track = {executor.submit(download_track, traccia, servizio_indice, formato_valore, qualita_valore): traccia 
                               for traccia in tracce}
            tracce_scaricate = 0

            for future in concurrent.futures.as_completed(future_to_track):
                traccia = future_to_track[future]
                success, downloaded_file, log = future.result()
                st.session_state.log_messages.extend([f"### {traccia}"] + log)
                
                if success and downloaded_file:
                    tracce_scaricate += 1
                    st.session_state.downloaded_files.append(downloaded_file)
                else:
                    st.session_state.pending_tracks.append(traccia)
                
                update_progress(tracce_scaricate)
                log_container.write("\n".join(st.session_state.log_messages[-10:]))  # Mostra gli ultimi 10 log

        # Riepilogo
        status_text.text(f"🏁 Completato! {tracce_scaricate}/{tracce_totali} tracce scaricate")
        st.write("### Riepilogo")
        st.write(f"**Numero totale di tracce:** {tracce_totali}")
        st.write(f"**Numero di tracce scaricate con successo:** {tracce_scaricate}")
        st.write(f"**Tracce non scaricate:** {len(st.session_state.pending_tracks)}")

        if st.session_state.downloaded_files:
            zip_path = create_zip_archive(download_dir, st.session_state.downloaded_files)
            if zip_path and os.path.exists(zip_path):
                with open(zip_path, "rb") as zip_file:
                    st.download_button(
                        label="📥 Scarica tutte le tracce (ZIP)",
                        data=zip_file,
                        file_name="tracce_scaricate.zip",
                        mime="application/zip",
                        key="download_zip"
                    )
                st.write(f"File inclusi nell'archivio: {[os.path.basename(f) for f in st.session_state.downloaded_files]}")
            else:
                st.error("Errore: l'archivio ZIP non è stato creato.")
        else:
            st.warning("Nessun file scaricato con successo.")
