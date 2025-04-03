import os  # Add this line
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

# Configura la directory di download
download_dir = "/Users/damiano/Desktop/chromedriver-mac-x64/downloads"
if not os.path.exists(download_dir):
    os.makedirs(download_dir)

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

# Specifica il percorso del chromedriver
service = Service("/usr/bin/chromedriver")  # Percorso su Streamlit Community Cloud
driver = webdriver.Chrome(service=service, options=options)

# Funzione per separare artista e traccia
def split_title(full_title):
    parts = full_title.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, full_title.strip()

# Funzione per aspettare il download
def wait_for_download(download_dir, existing_files, timeout=120):
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_files = glob.glob(os.path.join(download_dir, "*.m4a"))
        new_files = [f for f in current_files if f not in existing_files]
        
        for file in new_files:
            file_size = os.path.getsize(file)
            if file_size > 0:
                return True, f"Nuovo file .m4a trovato: {file}, dimensione: {file_size} byte", file
            else:
                return False, f"Nuovo file .m4a trovato ma vuoto: {file}, dimensione: {file_size} byte", None
        
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        if crdownload_files:
            time.sleep(5)
        else:
            time.sleep(5)
    return False, "Timeout raggiunto, nessun nuovo download completato o file vuoto.", None

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali")
st.write("Carica un file `tracce.txt` con l'elenco delle tracce da scaricare (formato: Artista - Traccia).")

# Upload del file tracce.txt
uploaded_file = st.file_uploader("Carica il file tracce.txt", type=["txt"])

# Lista per tenere traccia dei file scaricati
downloaded_files = []

if uploaded_file is not None:
    # Leggi il file caricato
    tracce = uploaded_file.read().decode("utf-8").splitlines()
    tracce_totali = len([traccia for traccia in tracce if traccia.strip()])
    st.write(f"**Numero totale di tracce da scaricare:** {tracce_totali}")

    # Pulsante per avviare il download
    if st.button("Avvia Download"):
        tracce_scaricate = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_area = st.empty()

        # Processa ogni traccia
        for idx, traccia in enumerate(tracce):
            traccia = traccia.strip()
            if not traccia:
                continue

            status_text.text(f"Ricerca in corso per: {traccia}")
            log_area.write(f"Ricerca in corso per: {traccia}")

            artista_input, traccia_input = split_title(traccia)
            log_area.write(f"Artista: {artista_input}, Traccia: {traccia_input}")

            trovato = False
            servizi_totali = 6

            for servizio_idx in range(1, servizi_totali + 1):
                driver.get("https://lucida.su")

                input_field = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "download"))
                )
                input_field.clear()
                input_field.send_keys(traccia)
                time.sleep(1)

                select_service = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "service"))
                )
                opzioni_service = select_service.find_elements(By.TAG_NAME, "option")
                if servizio_idx >= len(opzioni_service):
                    log_area.write(f"Indice {servizio_idx} non valido per il menu 'service'")
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

                log_area.write(f"Servizio {servizio_idx} selezionato: {opzioni_service[servizio_idx].text}")
                time.sleep(8)

                try:
                    WebDriverWait(driver, 60).until(
                        lambda driver: len(driver.find_element(By.ID, "country").find_elements(By.TAG_NAME, "option")) > 0
                    )
                    select_country = Select(driver.find_element(By.ID, "country"))
                    opzioni_country = select_country.options
                    if not opzioni_country:
                        log_area.write(f"Nessuna opzione disponibile nel menu 'country' per il servizio {servizio_idx}")
                        continue
                    select_country.select_by_index(0)
                    log_area.write(f"Prima opzione di 'country' selezionata: {select_country.first_selected_option.text}")
                except Exception as e:
                    log_area.write(f"Errore nella selezione di 'country' per il servizio {servizio_idx}: {e}")
                    continue

                try:
                    go_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.ID, "go"))
                    )
                    go_button.click()
                    log_area.write("Pulsante 'go' cliccato")
                except Exception as e:
                    log_area.write(f"Errore nel cliccare 'go': {e}")
                    continue

                time.sleep(3)

                try:
                    titoli = driver.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
                    artisti = driver.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")

                    for i, titolo in enumerate(titoli):
                        titolo_testo = titolo.text.strip().lower()
                        traccia_testo = traccia_input.lower()

                        if traccia_testo in titolo_testo:
                            if artista_input and i < len(artisti):
                                artista_testo = artisti[i].text.strip().lower()
                                if artista_input.lower() not in artista_testo:
                                    continue

                            titolo.click()
                            trovato = True
                            log_area.write("Traccia trovata e cliccata nei risultati")
                            break
                    if not trovato:
                        log_area.write(f"Traccia non trovata nei risultati del servizio {servizio_idx}")
                except Exception as e:
                    log_area.write(f"Errore nella ricerca dei risultati per il servizio {servizio_idx}: {e}")
                    continue

                if trovato:
                    break

            if not trovato:
                log_area.write(f"Traccia '{traccia}' non trovata in nessun servizio.")
                continue

            time.sleep(5)

            try:
                select_convert = Select(WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "convert"))
                ))
                select_convert.select_by_value("m4a-aac")
                log_area.write(f"Formato 'convert' selezionato: {select_convert.first_selected_option.text}")
            except Exception as e:
                log_area.write(f"Errore nella selezione di 'm4a-aac' per 'convert': {e}")
                continue

            try:
                select_downsetting = Select(WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "downsetting"))
                ))
                select_downsetting.select_by_value("320")
                log_area.write(f"Opzione 'downsetting' selezionata: {select_downsetting.first_selected_option.text}")
            except Exception as e:
                log_area.write(f"Errore nella selezione di '320' per 'downsetting': {e}")
                continue

            existing_files = glob.glob(os.path.join(download_dir, "*.m4a"))
            log_area.write(f"File .m4a preesistenti nella directory: {existing_files}")

            try:
                download_button = WebDriverWait(driver, 20).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "download-button"))
                )
                download_button.click()
                log_area.write("Pulsante di download cliccato")
            except Exception as e:
                log_area.write(f"Errore nel cliccare il pulsante di download: {e}")
                continue

            success, message, downloaded_file = wait_for_download(download_dir, existing_files, timeout=120)
            if success:
                tracce_scaricate += 1
                log_area.write(f"Download completato per: {traccia}")
                log_area.write(message)
                if downloaded_file:
                    downloaded_files.append(downloaded_file)
            else:
                log_area.write(f"Download non completato per: {traccia}")
                log_area.write(message)
                continue

            # Aggiorna la barra di progresso
            progress = (idx + 1) / tracce_totali
            progress_bar.progress(min(progress, 1.0))

        # Riepilogo finale
        st.write("### Riepilogo")
        st.write(f"**Numero totale di tracce:** {tracce_totali}")
        st.write(f"**Numero di tracce scaricate con successo:** {tracce_scaricate}")
        st.write("Tutti i download sono completati!")

        # Mostra i file scaricati con link per il download
        if downloaded_files:
            st.write("### File Scaricati")
            for file_path in downloaded_files:
                file_name = os.path.basename(file_path)
                with open(file_path, "rb") as file:
                    st.download_button(
                        label=f"Scarica {file_name}",
                        data=file,
                        file_name=file_name,
                        mime="audio/m4a"
                    )

# Chiudi il browser alla fine
driver.quit()
