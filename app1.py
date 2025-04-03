import streamlit as st
import os
import tempfile
import base64
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

# Configura la directory di download (usando temp directory per evitare problemi di permessi)
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

# Specifica il percorso del chromedriver in base all'ambiente
try:
    service = Service("/usr/bin/chromedriver")  # Percorso su Streamlit Community Cloud
    driver = webdriver.Chrome(service=service, options=options)
except Exception as e:
    st.error(f"Errore nell'inizializzazione del driver: {str(e)}")
    try:
        # Prova un percorso alternativo o lascia che selenium trovi il driver
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e2:
        st.error(f"Secondo tentativo fallito: {str(e2)}")
        # Fallback: prova senza specificare il service
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
def wait_for_download(download_dir, existing_files, timeout=180):  # Timeout aumentato
    start_time = time.time()
    st.write(f"In attesa del download. File esistenti: {existing_files}")
    
    while time.time() - start_time < timeout:
        # Mostra i file nella directory durante l'attesa
        current_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.m4a"))]
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        
        if time.time() - start_time > 10 and (time.time() - start_time) % 10 < 1:
            st.write(f"Attesa download: {int(time.time() - start_time)}s. File attuali: {os.listdir(download_dir)}")
        
        # Verifica nuovi file m4a
        new_files = [f for f in current_files if f not in existing_files]
        
        for file in new_files:
            file_size = os.path.getsize(file)
            st.write(f"Nuovo file trovato: {file}, dimensione: {file_size} bytes")
            if file_size > 0:
                return True, f"Download completato: {file}, dimensione: {file_size} byte", file
        
        # Se ci sono file in download, continua ad attendere
        if crdownload_files:
            time.sleep(5)
            continue
        
        # Se nessun file è in download ma è passato poco tempo, continua ad attendere
        if time.time() - start_time < 30:
            time.sleep(5)
            continue
            
        # Controlla se ci sono nuovi file di qualsiasi tipo
        all_new_files = [f for f in os.listdir(download_dir) if os.path.join(download_dir, f) not in existing_files]
        if all_new_files:
            for f in all_new_files:
                full_path = os.path.join(download_dir, f)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                    if f.endswith('.m4a'):
                        return True, f"Download completato: {f}", full_path
            
        # Se sono passati più di 60 secondi ma meno del timeout, attendere ancora
        if time.time() - start_time > 60:
            time.sleep(10)
        else:
            time.sleep(5)
    
    return False, f"Timeout raggiunto ({timeout}s), nessun download completato.", None

# Funzione per creare link di download usando base64 (backup)
def get_download_link(file_path, file_name):
    try:
        with open(file_path, "rb") as file:
            contents = file.read()
            b64 = base64.b64encode(contents).decode()
            href = f'<a href="data:audio/m4a;base64,{b64}" download="{file_name}">Scarica {file_name} (Metodo alternativo)</a>'
            return href
    except Exception as e:
        return f"Errore nella creazione del link: {str(e)}"

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali")
st.write("Carica un file `tracce.txt` con l'elenco delle tracce da scaricare (formato: Artista - Traccia).")

# Upload del file tracce.txt
uploaded_file = st.file_uploader("Carica il file tracce.txt", type=["txt"])

if uploaded_file is not None:
    # Leggi il file caricato
    tracce = uploaded_file.read().decode("utf-8").splitlines()
    tracce = [traccia for traccia in tracce if traccia.strip()]
    tracce_totali = len(tracce)
    st.write(f"**Numero totale di tracce da scaricare:** {tracce_totali}")

    # Pulsante per avviare il download
    if st.button("Avvia Download"):
        tracce_scaricate = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_area = st.empty()
        
        # Reset della lista file scaricati per questa sessione
        st.session_state.downloaded_files = []

        # Processa ogni traccia
        for idx, traccia in enumerate(tracce):
            traccia = traccia.strip()
            if not traccia:
                continue

            status_text.text(f"Ricerca in corso per: {traccia} ({idx+1}/{tracce_totali})")
            log_container = st.container()
            log_container.write(f"### Ricerca in corso per: {traccia}")

            artista_input, traccia_input = split_title(traccia)
            log_container.write(f"Artista: {artista_input}, Traccia: {traccia_input}")

            trovato = False
            servizi_totali = 6

            for servizio_idx in range(1, servizi_totali + 1):
                driver.get("https://lucida.su")
                log_container.write(f"Accesso a lucida.su (servizio {servizio_idx})")

                try:
                    input_field = WebDriverWait(driver, 20).until(  # Aumentato timeout
                        EC.element_to_be_clickable((By.ID, "download"))
                    )
                    input_field.clear()
                    input_field.send_keys(traccia)
                    time.sleep(2)  # Attesa aumentata
                    log_container.write("Campo input compilato")
                except Exception as e:
                    log_container.write(f"Errore nell'accesso al campo input: {str(e)}")
                    continue

                try:
                    select_service = WebDriverWait(driver, 20).until(  # Aumentato timeout
                        EC.element_to_be_clickable((By.ID, "service"))
                    )
                    opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
                    if servizio_idx >= len(opzioni_service):
                        log_container.write(f"Indice {servizio_idx} non valido per il menu 'service'")
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

                    log_container.write(f"Servizio {servizio_idx} selezionato: {opzioni_service[servizio_idx].text}")
                    time.sleep(10)  # Attesa aumentata
                except Exception as e:
                    log_container.write(f"Errore nella selezione del servizio: {str(e)}")
                    continue

                try:
                    WebDriverWait(driver, 90).until(  # Aumentato timeout
                        lambda driver: len(driver.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0
                    )
                    select_country = Select(driver.find_element(By.ID, "country"))
                    opzioni_country = select_country.options
                    if not opzioni_country:
                        log_container.write(f"Nessuna opzione disponibile nel menu 'country' per il servizio {servizio_idx}")
                        continue
                    select_country.select_by_index(0)
                    log_container.write(f"Prima opzione di 'country' selezionata: {select_country.first_selected_option.text}")
                    time.sleep(2)  # Attesa aggiunta
                except Exception as e:
                    log_container.write(f"Errore nella selezione di 'country' per il servizio {servizio_idx}: {str(e)}")
                    continue

                try:
                    go_button = WebDriverWait(driver, 20).until(  # Aumentato timeout
                        EC.element_to_be_clickable((By.ID, "go"))
                    )
                    go_button.click()
                    log_container.write("Pulsante 'go' cliccato")
                    time.sleep(5)  # Attesa aumentata
                except Exception as e:
                    log_container.write(f"Errore nel cliccare 'go': {str(e)}")
                    continue

                try:
                    WebDriverWait(driver, 60).until(  # Attendiamo che i risultati si carichino
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or 
                                 "No results found" in d.page_source
                    )
                    
                    titoli = driver.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
                    artisti = driver.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")

                    log_container.write(f"Risultati trovati: {len(titoli)} titoli")
                    
                    for i, titolo in enumerate(titoli):
                        titolo_testo = titolo.text.strip().lower()
                        traccia_testo = traccia_input.lower()

                        log_container.write(f"Confronto: '{traccia_testo}' con '{titolo_testo}'")
                        
                        if traccia_testo in titolo_testo:
                            if artista_input and i < len(artisti):
                                artista_testo = artisti[i].text.strip().lower()
                                if artista_input.lower() not in artista_testo:
                                    log_container.write(f"Artista non corrispondente: '{artista_input.lower()}' vs '{artista_testo}'")
                                    continue

                            driver.execute_script("arguments[0].scrollIntoView(true);", titolo)
                            time.sleep(1)
                            titolo.click()
                            trovato = True
                            log_container.write(f"✅ Traccia trovata e cliccata: '{titolo_testo}'")
                            break
                    
                    if not trovato:
                        log_container.write(f"Traccia non trovata nei risultati del servizio {servizio_idx}")
                except Exception as e:
                    log_container.write(f"Errore nella ricerca dei risultati per il servizio {servizio_idx}: {str(e)}")
                    continue

                if trovato:
                    break

            if not trovato:
                log_container.write(f"❌ Traccia '{traccia}' non trovata in nessun servizio.")
                continue

            time.sleep(8)  # Attesa aumentata

            try:
                select_convert = Select(WebDriverWait(driver, 30).until(  # Aumentato timeout
                    EC.element_to_be_clickable((By.ID, "convert"))
                ))
                select_convert.select_by_value("m4a-aac")
                log_container.write(f"Formato 'convert' selezionato: {select_convert.first_selected_option.text}")
                time.sleep(2)  # Attesa aggiunta
            except Exception as e:
                log_container.write(f"Errore nella selezione di 'm4a-aac' per 'convert': {str(e)}")
                continue

            try:
                select_downsetting = Select(WebDriverWait(driver, 30).until(  # Aumentato timeout
                    EC.element_to_be_clickable((By.ID, "downsetting"))
                ))
                select_downsetting.select_by_value("320")
                log_container.write(f"Opzione 'downsetting' selezionata: {select_downsetting.first_selected_option.text}")
                time.sleep(2)  # Attesa aggiunta
            except Exception as e:
                log_container.write(f"Errore nella selezione di '320' per 'downsetting': {str(e)}")
                continue

            # Elenco di tutti i file esistenti prima del download
            existing_files = []
            for ext in ["*.m4a", "*.mp3", "*.crdownload"]:  # Controlla diversi tipi di file
                existing_files.extend([os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, ext))])
            
            log_container.write(f"File esistenti prima del download: {existing_files}")

            try:
                download_button = WebDriverWait(driver, 30).until(  # Aumentato timeout
                    EC.element_to_be_clickable((By.CLASS_NAME, "download-button"))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
                time.sleep(1)
                download_button.click()
                log_container.write("Pulsante di download cliccato")
            except Exception as e:
                log_container.write(f"Errore nel cliccare il pulsante di download: {str(e)}")
                continue

            success, message, downloaded_file = wait_for_download(download_dir, existing_files, timeout=180)  # Timeout aumentato
            
            if success and downloaded_file:
                # Verifica che il file esista e non sia vuoto
                if os.path.exists(downloaded_file) and os.path.getsize(downloaded_file) > 0:
                    tracce_scaricate += 1
                    log_container.write(f"✅ Download completato per: {traccia}")
                    log_container.write(message)
                    
                    # Salva il riferimento al file scaricato in session_state
                    st.session_state.downloaded_files.append(downloaded_file)
                else:
                    log_container.write(f"❌ File non trovato o vuoto: {downloaded_file}")
            else:
                log_container.write(f"❌ Download non completato per: {traccia}")
                log_container.write(message)
                continue

            # Aggiorna la barra di progresso
            progress = (idx + 1) / tracce_totali
            progress_bar.progress(min(progress, 1.0))

        # Riepilogo finale
        st.write("### Riepilogo")
        st.write(f"**Numero totale di tracce:** {tracce_totali}")
        st.write(f"**Numero di tracce scaricate con successo:** {tracce_scaricate}")
        st.write("Tutti i download sono completati!")

        # Debug: mostra lo stato del sistema di file
        st.subheader("Stato del filesystem")
        st.write(f"Directory di download: {download_dir}")
        st.write(f"File presenti nella directory: {os.listdir(download_dir)}")
        st.write(f"File tracciati in session_state: {[os.path.basename(f) for f in st.session_state.downloaded_files]}")

        # Mostra i file scaricati con link per il download
        if st.session_state.downloaded_files:
            st.subheader("File Scaricati")
            for file_path in st.session_state.downloaded_files:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    file_name = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    
                    st.write(f"**{file_name}** ({file_size/1024:.1f} KB)")
                    
                    # Metodo primario: pulsante di download Streamlit
                    try:
                        with open(file_path, "rb") as file:
                            file_content = file.read()
                            st.download_button(
                                label=f"Scarica {file_name}",
                                data=file_content,
                                file_name=file_name,
                                mime="audio/m4a",
                                key=f"download_{file_name}"
                            )
                    except Exception as e:
                        st.error(f"Errore nel leggere il file: {str(e)}")
                    
                    # Metodo alternativo: link in HTML
                    st.markdown(get_download_link(file_path, file_name), unsafe_allow_html=True)
                else:
                    st.error(f"Il file {os.path.basename(file_path)} non esiste più o è vuoto!")
        else:
            st.warning("Nessun file scaricato con successo.")

# Chiudi il browser alla fine
driver.quit()
