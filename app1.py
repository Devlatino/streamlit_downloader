import streamlit as st
import os
import tempfile
import base64
import zipfile
from selenium import webdriver
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
import glob

# Inizializza lo stato della sessione per memorizzare i file scaricati
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = []

# Configura la directory di download
download_dir = tempfile.mkdtemp()
st.write(f"Directory di download: {download_dir} (Permessi: {os.access(download_dir, os.W_OK)})")

# Configura le opzioni di Chrome
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

# Inizializza il driver
try:
    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
except Exception as e:
    st.error(f"Errore nell'inizializzazione del driver: {str(e)}")
    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e2:
        st.error(f"Secondo tentativo fallito: {str(e2)}")
        try:
            driver = webdriver.Chrome(options=options)
        except Exception as e3:
            st.error(f"Impossibile inizializzare Chrome: {str(e3)}")
            st.stop()

# Funzione per separare artista e traccia
def split_title(full_title):
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, full_title.strip()

# Funzione per aspettare il download
def wait_for_download(download_dir, existing_files, timeout=180):
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.m4a"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        
        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            file_size = os.path.getsize(file)
            if file_size > 0:
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
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0 and f.endswith('.m4a'):
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
        st.error(f"Errore nella creazione dello ZIP: {str(e)}")
        return None

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali")
st.write("Carica un file `tracce.txt` con l'elenco delle tracce (formato: Artista - Traccia).")

# Upload del file tracce.txt
uploaded_file = st.file_uploader("Carica il file tracce.txt", type=["txt"])

if uploaded_file is not None:
    tracce = uploaded_file.read().decode("utf-8").splitlines()
    tracce = [traccia for traccia in tracce if traccia.strip()]
    tracce_totali = len(tracce)
    st.write(f"**Numero totale di tracce da scaricare:** {tracce_totali}")

    if st.button("Avvia Download"):
        tracce_scaricate = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()  # Contenitore per log dinamici
        
        st.session_state.downloaded_files = []

        # Processa ogni traccia
        for idx, traccia in enumerate(tracce):
            traccia = traccia.strip()
            if not traccia:
                continue

            # Aggiorna dinamicamente lo stato
            status_text.text(f"🔄 Ricerca in corso per: {traccia} ({idx+1}/{tracce_totali})")
            log_container.write(f"### {traccia}")

            artista_input, traccia_input = split_title(traccia)
            log_container.write(f"🎤 Artista: {artista_input} | 🎵 Traccia: {traccia_input}")

            trovato = False
            servizi_totali = 6

            for servizio_idx in range(1, servizi_totali + 1):
                driver.get("https://lucida.su")
                log_container.write(f"🌐 Accesso a lucida.su (servizio {servizio_idx})")

                try:
                    input_field = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.ID, "download"))
                    )
                    input_field.clear()
                    input_field.send_keys(traccia)
                    time.sleep(2)
                    log_container.write("✍️ Campo input compilato")
                except Exception as e:
                    log_container.write(f"❌ Errore campo input: {str(e)}")
                    continue

                try:
                    select_service = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.ID, "service"))
                    )
                    opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
                    if servizio_idx >= len(opzioni_service):
                        log_container.write(f"⚠️ Indice {servizio_idx} non valido per 'service'")
                        continue

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
                    log_container.write(f"🔧 Servizio {servizio_idx} selezionato: {opzioni_service[servizio_idx].text}")
                    time.sleep(10)
                except Exception as e:
                    log_container.write(f"❌ Errore selezione servizio: {str(e)}")
                    continue

                try:
                    WebDriverWait(driver, 90).until(
                        lambda driver: len(driver.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0
                    )
                    select_country = Select(driver.find_element(By.ID, "country"))
                    if not select_country.options:
                        log_container.write(f"⚠️ Nessuna opzione in 'country' per servizio {servizio_idx}")
                        continue
                    select_country.select_by_index(0)
                    log_container.write(f"🌍 Paese selezionato: {select_country.first_selected_option.text}")
                    time.sleep(2)
                except Exception as e:
                    log_container.write(f"❌ Errore selezione paese: {str(e)}")
                    continue

                try:
                    go_button = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.ID, "go"))
                    )
                    go_button.click()
                    log_container.write("▶️ Pulsante 'go' cliccato")
                    time.sleep(5)
                except Exception as e:
                    log_container.write(f"❌ Errore clic 'go': {str(e)}")
                    continue

                try:
                    WebDriverWait(driver, 60).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or 
                                 "No results found" in d.page_source
                    )
                    titoli = driver.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
                    artisti = driver.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")

                    log_container.write(f"📋 Risultati trovati: {len(titoli)} titoli")
                    
                    for i, titolo in enumerate(titoli):
                        titolo_testo = titolo.text.strip().lower()
                        traccia_testo = traccia_input.lower()

                        log_container.write(f"🔍 Confronto: '{traccia_testo}' con '{titolo_testo}'")
                        
                        if traccia_testo in titolo_testo:
                            if artista_input and i < len(artisti):
                                artista_testo = artisti[i].text.strip().lower()
                                if artista_input.lower() not in artista_testo:
                                    log_container.write(f"⚠️ Artista non corrispondente: '{artista_input.lower()}' vs '{artista_testo}'")
                                    continue

                            driver.execute_script("arguments[0].scrollIntoView(true);", titolo)
                            time.sleep(1)
                            titolo.click()
                            trovato = True
                            log_container.write(f"✅ Traccia trovata e cliccata: '{titolo_testo}'")
                            break
                    
                    if not trovato:
                        log_container.write(f"❌ Traccia non trovata in servizio {servizio_idx}")
                except Exception as e:
                    log_container.write(f"❌ Errore ricerca risultati: {str(e)}")
                    continue

                if trovato:
                    break

            if not trovato:
                log_container.write(f"❌ Traccia '{traccia}' non trovata in nessun servizio.")
                log_container.empty()  # Pulizia log
                continue

            time.sleep(8)

            try:
                select_convert = Select(WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "convert"))
                ))
                select_convert.select_by_value("m4a-aac")
                log_container.write(f"🎧 Formato 'm4a-aac' selezionato")
                time.sleep(2)
            except Exception as e:
                log_container.write(f"❌ Errore selezione formato: {str(e)}")
                continue

            try:
                select_downsetting = Select(WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "downsetting"))
                ))
                select_downsetting.select_by_value("320")
                log_container.write(f"🔊 Qualità '320kbps' selezionata")
                time.sleep(2)
            except Exception as e:
                log_container.write(f"❌ Errore selezione qualità: {str(e)}")
                continue

            existing_files = []
            for ext in ["*.m4a", "*.mp3", "*.crdownload"]:
                existing_files.extend([os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, ext))])
            
            log_container.write(f"📂 File esistenti prima del download: {existing_files}")

            try:
                download_button = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "download-button"))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
                time.sleep(1)
                download_button.click()
                log_container.write("⬇️ Pulsante di download cliccato")
            except Exception as e:
                log_container.write(f"❌ Errore clic download: {str(e)}")
                continue

            success, message, downloaded_file = wait_for_download(download_dir, existing_files, timeout=180)
            
            if success and downloaded_file:
                if os.path.exists(downloaded_file) and os.path.getsize(downloaded_file) > 0:
                    tracce_scaricate += 1
                    log_container.write(f"✅ Download completato per: {traccia}")
                    log_container.write(message)
                    st.session_state.downloaded_files.append(downloaded_file)
                else:
                    log_container.write(f"❌ File non trovato o vuoto: {downloaded_file}")
            else:
                log_container.write(f"❌ Download fallito: {message}")

            # Pulizia log dopo ogni traccia
            log_container.empty()
            
            # Aggiorna barra di progresso dinamicamente
            progress = (idx + 1) / tracce_totali
            progress_bar.progress(min(progress, 1.0))
            status_text.text(f"✅ {tracce_scaricate}/{tracce_totali} tracce scaricate")

        # Riepilogo finale
        status_text.text(f"🏁 Completato! {tracce_scaricate}/{tracce_totali} tracce scaricate")
        st.write("### Riepilogo")
        st.write(f"**Numero totale di tracce:** {tracce_totali}")
        st.write(f"**Numero di tracce scaricate con successo:** {tracce_scaricate}")

        # Creazione e download dell'archivio ZIP
        if st.session_state.downloaded_files:
            st.subheader("Download Archivio")
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
                st.error("Errore: l'archivio ZIP non è stato creato correttamente.")
        else:
            st.warning("Nessun file scaricato con successo.")

# Chiudi il browser
driver.quit()
