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
import concurrent.futures
import threading
from functools import lru_cache

# Credenziali Spotify
CLIENT_ID = 'f147b13a0d2d40d7b5d0c3ac36b60769'
CLIENT_SECRET = '566b72290ee94a60ada9164fabb6515b'

# Inizializza lo stato della sessione per memorizzare i file scaricati
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = []

# Per tracciare i download in corso
if 'active_downloads' not in st.session_state:
    st.session_state.active_downloads = {}

# Per memorizzare i servizi disponibili tra le sessioni
if 'servizi_disponibili' not in st.session_state:
    st.session_state.servizi_disponibili = []

# Configura la directory di download
download_dir = tempfile.mkdtemp()
st.write(f"Directory di download: {download_dir} (Permessi: {os.access(download_dir, os.W_OK)})")

# Lock per operazioni concorrenti
driver_lock = threading.Lock()

# Configura le opzioni di Chrome
def get_chrome_options(download_dir):
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

# Inizializza il driver (riutilizzabile)
@st.cache_resource
def initialize_driver(download_dir):
    options = get_chrome_options(download_dir)
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        try:
            service = Service()
            driver = webdriver.Chrome(service=service, options=options)
        except Exception as e2:
            try:
                driver = webdriver.Chrome(options=options)
            except Exception as e3:
                st.error(f"Impossibile inizializzare Chrome: {str(e3)}")
                return None
    return driver

# Formati disponibili
FORMATI_DISPONIBILI = {
    "m4a-aac": "AAC (.m4a)",
    "mp3": "MP3 (.mp3)",  
    "flac": "FLAC (.flac)",
    "wav": "WAV (.wav)",
    "ogg": "OGG (.ogg)"
}

# Qualità disponibili
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

# Funzione per normalizzare gli artisti: prende solo il primo artista se ci sono più artisti separati da virgole
def normalize_artist(artist_string):
    if not artist_string:
        return ""
    # Prendi solo il primo artista se ci sono virgole
    return artist_string.split(',')[0].strip().lower()

# Funzione ottimizzata per aspettare il download
def wait_for_download(download_dir, existing_files, formato, timeout=90):
    start_time = time.time()
    estensione = formato.split('-')[0] if '-' in formato else formato
    
    # Check ogni 1 secondo invece di 5
    check_interval = 1
    
    while time.time() - start_time < timeout:
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, f"*.{estensione}"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        
        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            file_size = os.path.getsize(file)
            if file_size > 0:
                # Attendiamo un breve momento per assicurarci che il file sia completamente scritto
                time.sleep(0.5)
                return True, f"Download completato: {os.path.basename(file)}", file
        
        if crdownload_files:
            time.sleep(check_interval)
            continue
        
        # Dopo 15 secondi, se non ci sono download attivi, possiamo considerare fallito
        if time.time() - start_time > 15 and not crdownload_files:
            all_new_files = [f for f in os.listdir(download_dir) if os.path.join(download_dir, f) not in existing_files]
            if all_new_files:
                for f in all_new_files:
                    full_path = os.path.join(download_dir, f)
                    if os.path.isfile(full_path) and os.path.getsize(full_path) > 0 and f.endswith(f'.{estensione}'):
                        return True, f"Download completato: {f}", full_path
        
        time.sleep(check_interval)
    
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
        st.error(f"Errore nella creazione dello ZIP: {str(e)}")
        return None

# Funzione per estrarre l'ID della playlist dal link
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

        track_list = []
        for item in tracks:
            track = item['track']
            artists = ', '.join([artist['name'] for artist in track['artists']])
            title = track['name']
            track_list.append(f"{artists} - {title}")

        return track_list
    except Exception as e:
        st.error(f"Errore nel recupero delle tracce da Spotify: {str(e)}")
        return None

# Funzione ottimizzata per ottenere i servizi disponibili
@st.cache_data(ttl=3600)  # Aggiorna ogni ora
def get_available_services(driver):
    try:
        with driver_lock:
            driver.get("https://lucida.su")
            # Ridotto il tempo di attesa
            time.sleep(2)
            
            select_service = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "service"))
            )
            options = select_service.find_elements(By.TAG_NAME, "option")
            
            services = []
            for i, option in enumerate(options):
                if i > 0:  # Ignora la prima opzione che è "Seleziona servizio"
                    value = option.get_attribute("value")
                    text = option.text
                    services.append({"index": i, "value": value, "text": text})
            
            return services
    except Exception as e:
        st.error(f"Errore nel recupero dei servizi disponibili: {str(e)}")
        return []

# Funzione per scaricare una singola traccia
def download_track(traccia, servizi_da_provare, formato_valore, qualita_valore, download_dir, 
                  formato_selezionato, qualita_selezionata, log_placeholder):
    log_messages = []
    log_messages.append(f"### {traccia}")
    
    artista_input, traccia_input = split_title(traccia)
    log_messages.append(f"🎤 Artista: {artista_input} | 🎵 Traccia: {traccia_input}")
    
    # Otteniamo un nuovo driver per ogni thread per evitare conflitti
    local_driver = initialize_driver(download_dir)
    if not local_driver:
        log_messages.append("❌ Impossibile inizializzare il browser")
        return log_messages, None
    
    trovato = False
    downloaded_file = None
    
    try:
        for servizio_idx in servizi_da_provare:
            log_messages.append(f"🌐 Accesso a lucida.su (servizio {servizio_idx})")
            local_driver.get("https://lucida.su")
            
            try:
                # Inseriamo la traccia nel campo di ricerca
                input_field = WebDriverWait(local_driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "download"))
                )
                input_field.clear()
                input_field.send_keys(traccia)
                # Ridotto il tempo di attesa
                time.sleep(1)
                log_messages.append("✍️ Campo input compilato")
            except Exception as e:
                log_messages.append(f"❌ Errore campo input: {str(e)}")
                continue
                
            try:
                # Selezioniamo il servizio
                select_service = WebDriverWait(local_driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "service"))
                )
                opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
                if servizio_idx >= len(opzioni_service):
                    log_messages.append(f"⚠️ Indice {servizio_idx} non valido per 'service'")
                    continue

                servizio_valore = opzioni_service[servizio_idx].get_attribute("value")
                local_driver.execute_script("""
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
                # Ridotto il tempo di attesa
                time.sleep(5)
            except Exception as e:
                log_messages.append(f"❌ Errore selezione servizio: {str(e)}")
                continue

            try:
                # Attendiamo il caricamento delle opzioni del paese
                WebDriverWait(local_driver, 45).until(
                    lambda driver: len(driver.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0
                )
                select_country = Select(local_driver.find_element(By.ID, "country"))
                if not select_country.options:
                    log_messages.append(f"⚠️ Nessuna opzione in 'country' per servizio {servizio_idx}")
                    continue
                select_country.select_by_index(0)
                log_messages.append(f"🌍 Paese selezionato: {select_country.first_selected_option.text}")
                # Ridotto il tempo di attesa
                time.sleep(1)
            except Exception as e:
                log_messages.append(f"❌ Errore selezione paese: {str(e)}")
                continue

            try:
                # Clicchiamo sul pulsante Go
                go_button = WebDriverWait(local_driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "go"))
                )
                go_button.click()
                log_messages.append("▶️ Pulsante 'go' cliccato")
                # Ridotto il tempo di attesa
                time.sleep(2)
            except Exception as e:
                log_messages.append(f"❌ Errore clic 'go': {str(e)}")
                continue

            try:
                # Attendiamo i risultati di ricerca
                WebDriverWait(local_driver, 30).until(
                    lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or 
                             "No results found" in d.page_source
                )
                titoli = local_driver.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
                artisti = local_driver.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")

                log_messages.append(f"📋 Risultati trovati: {len(titoli)} titoli")
                
                for i, titolo in enumerate(titoli):
                    titolo_testo = titolo.text.strip().lower()
                    traccia_testo = traccia_input.lower()

                    # Migliorato algoritmo di corrispondenza
                    parole_traccia = set(re.findall(r'\w+', traccia_testo))
                    parole_titolo = set(re.findall(r'\w+', titolo_testo))
                    
                    if not parole_traccia:  # Se non ci sono parole significative
                        continue
                        
                    sovrapposizione = len(parole_traccia.intersection(parole_titolo))
                    percentuale_match = sovrapposizione / len(parole_traccia)
                    
                    log_messages.append(f"🔍 Confronto: '{traccia_testo}' con '{titolo_testo}' (Match: {percentuale_match:.2%})")
                    
                    # Verifica corrispondenza titolo
                    match_titolo = percentuale_match >= 0.7 or traccia_testo in titolo_testo
                    
                    # Verifica artista se disponibile
                    match_artista = True
                    if artista_input and i < len(artisti):
                        artista_normalizzato = normalize_artist(artista_input)
                        artista_risultato = artisti[i].text.strip().lower()
                        
                        if artista_normalizzato and artista_normalizzato not in artista_risultato:
                            log_messages.append(f"⚠️ Artista principale non corrispondente: '{artista_normalizzato}' vs '{artista_risultato}'")
                            # Accetta comunque se il titolo ha ottima corrispondenza
                            match_artista = (percentuale_match >= 0.9)
                    
                    if match_titolo and match_artista:
                        local_driver.execute_script("arguments[0].scrollIntoView(true);", titolo)
                        titolo.click()
                        trovato = True
                        log_messages.append(f"✅ Traccia trovata e cliccata: '{titolo_testo}'")
                        break
                
                if not trovato:
                    log_messages.append(f"❌ Traccia non trovata in servizio {servizio_idx}")
            except Exception as e:
                log_messages.append(f"❌ Errore ricerca risultati: {str(e)}")
                continue

            if trovato:
                break

        if not trovato:
            log_messages.append(f"❌ Traccia '{traccia}' non trovata in nessun servizio.")
            local_driver.quit()
            return log_messages, None

        # Ridotto il tempo di attesa dopo aver trovato la traccia
        time.sleep(3)

        try:
            # Selezioniamo il formato audio
            select_convert = Select(WebDriverWait(local_driver, 15).until(
                EC.element_to_be_clickable((By.ID, "convert"))
            ))
            select_convert.select_by_value(formato_valore)
            log_messages.append(f"🎧 Formato '{formato_selezionato}' selezionato")
            # Ridotto il tempo di attesa
            time.sleep(1)
        except Exception as e:
            log_messages.append(f"❌ Errore selezione formato: {str(e)}")
            local_driver.quit()
            return log_messages, None

        try:
            # Selezioniamo la qualità audio
            select_downsetting = Select(WebDriverWait(local_driver, 15).until(
                EC.element_to_be_clickable((By.ID, "downsetting"))
            ))
            select_downsetting.select_by_value(qualita_valore)
            log_messages.append(f"🔊 Qualità '{qualita_selezionata}' selezionata")
            # Ridotto il tempo di attesa
            time.sleep(1)
        except Exception as e:
            log_messages.append(f"❌ Errore selezione qualità: {str(e)}")
            local_driver.quit()
            return log_messages, None

        # Otteniamo l'estensione del file
        estensione = formato_valore.split('-')[0] if '-' in formato_valore else formato_valore
        
        # Elenchiamo i file esistenti prima del download
        existing_files = []
        for ext in [f"*.{estensione}", "*.crdownload"]:
            existing_files.extend([os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, ext))])
        
        log_messages.append(f"📂 File esistenti prima del download: {len(existing_files)}")

        try:
            # Clicchiamo sul pulsante Download
            download_button = WebDriverWait(local_driver, 15).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "download-button"))
            )
            local_driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
            download_button.click()
            log_messages.append("⬇️ Pulsante di download cliccato")
        except Exception as e:
            log_messages.append(f"❌ Errore clic download: {str(e)}")
            local_driver.quit()
            return log_messages, None

        # Attendiamo il completamento del download
        success, message, downloaded_file = wait_for_download(
            download_dir, existing_files, formato_valore, timeout=90
        )
        
        if success and downloaded_file:
            if os.path.exists(downloaded_file) and os.path.getsize(downloaded_file) > 0:
                log_messages.append(f"✅ Download completato per: {traccia}")
                log_messages.append(message)
            else:
                log_messages.append(f"❌ File non trovato o vuoto: {downloaded_file}")
                downloaded_file = None
        else:
            log_messages.append(f"❌ Download fallito: {message}")
            downloaded_file = None
    
    except Exception as e:
        log_messages.append(f"❌ Errore generale: {str(e)}")
    
    finally:
        # Chiudiamo sempre il driver locale
        local_driver.quit()
        
    return log_messages, downloaded_file

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali (PIZZUNA)")
st.write("Carica un file `tracce.txt` o inserisci un link a una playlist Spotify per scaricare le tue tracce preferite.")

# Inizializzazione del driver principale
main_driver = initialize_driver(download_dir)
if main_driver is None:
    st.error("Impossibile inizializzare il browser Chrome. Verificare che sia installato correttamente.")
    st.stop()

# Carica i servizi disponibili se non sono già stati caricati
if not st.session_state.servizi_disponibili:
    with st.spinner("Caricamento servizi disponibili..."):
        st.session_state.servizi_disponibili = get_available_services(main_driver)
    
    if st.session_state.servizi_disponibili:
        st.success(f"Caricati {len(st.session_state.servizi_disponibili)} servizi disponibili.")
    else:
        st.warning("Impossibile caricare i servizi disponibili. Potrebbe essere necessario ricaricare la pagina.")

# Sezione per le preferenze di download
st.subheader("Preferenze di download")

# Selezione del servizio
servizio_opzioni = {f"{s['text']} (Servizio {s['index']})": s['index'] for s in st.session_state.servizi_disponibili}
if servizio_opzioni:
    servizio_predefinito = list(servizio_opzioni.keys())[0] if servizio_opzioni else None
    servizio_selezionato = st.selectbox(
        "Servizio preferito", 
        options=list(servizio_opzioni.keys()),
        index=0,
        help="Seleziona il servizio di streaming da cui preferisci scaricare le tracce."
    )
    servizio_indice = servizio_opzioni[servizio_selezionato]
else:
    st.warning("Nessun servizio disponibile. Verrà utilizzato il metodo predefinito di ricerca tra tutti i servizi.")
    servizio_indice = None

# Creazione di due colonne per formato e qualità
col1, col2, col3 = st.columns(3)

# Selezione del formato
with col1:
    formato_selezionato = st.selectbox(
        "Formato audio",
        options=list(FORMATI_DISPONIBILI.values()),
        index=0,
        help="Seleziona il formato audio per i file scaricati."
    )
    # Converti la selezione dal testo al valore corrispondente
    formato_valore = list(FORMATI_DISPONIBILI.keys())[list(FORMATI_DISPONIBILI.values()).index(formato_selezionato)]

# Selezione della qualità
with col2:
    qualita_selezionata = st.selectbox(
        "Qualità audio",
        options=list(QUALITA_DISPONIBILI.values()),
        index=0,
        help="Seleziona la qualità audio per i file scaricati."
    )
    # Converti la selezione dal testo al valore corrispondente
    qualita_valore = list(QUALITA_DISPONIBILI.keys())[list(QUALITA_DISPONIBILI.values()).index(qualita_selezionata)]

# Numero di download paralleli
with col3:
    max_workers = st.slider(
        "Download paralleli", 
        min_value=1, 
        max_value=5, 
        value=2,
        help="Numero di tracce da scaricare contemporaneamente. Un numero maggiore può velocizzare il processo ma richiede più risorse."
    )

# Sezione per il link della playlist Spotify
st.subheader("Genera tracce.txt da Spotify")
st.write("Inserisci il link di una playlist Spotify (es. https://open.spotify.com/playlist/...) per creare automaticamente un file `tracce.txt` con le tracce della playlist.")
playlist_link = st.text_input("Link della playlist Spotify")

if playlist_link:
    if st.button("Genera tracce.txt da Spotify"):
        tracks = get_spotify_tracks(playlist_link)
        if tracks:
            # Salva le tracce in un file temporaneo
            temp_file_path = os.path.join(download_dir, "tracce.txt")
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                for track in tracks:
                    f.write(track + '\n')
            st.success(f"File `tracce.txt` generato con successo! Contiene {len(tracks)} tracce.")
            st.session_state['spotify_file'] = temp_file_path
        else:
            st.error("Impossibile generare il file delle tracce.")

# Upload del file tracce.txt
uploaded_file = st.file_uploader("Oppure carica il file tracce.txt", type=["txt"])

# Determina la fonte delle tracce
if 'spotify_file' in st.session_state and os.path.exists(st.session_state['spotify_file']):
    tracce_source = st.session_state['spotify_file']
elif uploaded_file is not None:
    tracce_source = uploaded_file
else:
    tracce_source = None

if tracce_source:
    # Leggi le tracce dalla fonte
    if isinstance(tracce_source, str):  # File generato da Spotify
        with open(tracce_source, 'r', encoding='utf-8') as f:
            tracce = [line.strip() for line in f.readlines() if line.strip()]
    else:  # File caricato dall'utente
        tracce = tracce_source.read().decode("utf-8").splitlines()
        tracce = [traccia.strip() for traccia in tracce if traccia.strip()]
    
    tracce_totali = len(tracce)
    st.write(f"**Numero totale di tracce da scaricare:** {tracce_totali}")

    if st.button("Avvia Download"):
        tracce_scaricate = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()
        
        # Visualizzazione in tempo reale
        logs_placeholder = st.container()
        
        st.session_state.downloaded_files = []
        
        # Determina quali servizi utilizzare
        if servizio_indice is not None:
            # Usa solo il servizio selezionato
            servizi_da_provare = [servizio_indice]
        else:
            # Usa tutti i servizi disponibili
            servizi_totali = len(st.session_state.servizi_disponibili) if st.session_state.servizi_disponibili else 6
            servizi_da_provare = range(1, servizi_totali + 1)

        # Dividiamo le tracce in blocchi
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Creiamo un dizionario per memorizzare i log di ogni traccia
            all_logs = {}
            futures = {}
            
            for idx, traccia in enumerate(tracce):
                status_text.text(f"⏱️ Accodamento traccia: {traccia} ({idx+1}/{tracce_totali})")
                
                # Inviamo le tracce all'executor
                future = executor.submit(
                    download_track, 
                    traccia, 
                    servizi_da_provare,
                    formato_valore,
                    qualita_valore,
                    download_dir,
                    formato_selezionato,
                    qualita_selezionata,
                    log_container
                )
                futures[future] = idx
                all_logs[idx] = []
                
                # Piccola pausa per evitare di sovraccaricare l'interfaccia
                time.sleep(0.2)
            
            # Monitoriamo il completamento dei download
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                traccia = tracce[idx]
                
                try:
                    # Otteniamo i risultati
                    log_messages, downloaded_file = future.result()
                    
                    # Aggiorniamo i log
                    all_logs[idx] = log_messages
                    
                    # Se abbiamo scaricato un file con successo
                    if downloaded_file:
                        st.session_state.downloaded_files.append(downloaded_file)
                        tracce_scaricate += 1
                    
                    # Aggiorniamo la progress bar
                    completed = len([f for f in futures if f.done()])
                    progress_bar.progress(completed / tracce_totali)
                    status
