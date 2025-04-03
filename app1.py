import streamlit as st
import os
import tempfile
import zipfile
from selenium import webdriver
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
import glob

# Inizializza lo stato per i file scaricati
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = []

# Configura la directory di download temporanea
download_dir = tempfile.mkdtemp()
st.write(f"Directory di download: {download_dir} (Scrivibile: {os.access(download_dir, os.W_OK)})")

# Configura Chrome
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
options.add_argument("--window-size=1920,1080")

# Inizializza il driver
try:
    driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
except Exception as e:
    st.error(f"Errore inizializzazione driver: {e}")
    driver = webdriver.Chrome(options=options)  # Fallback per test locali

# Funzione per separare artista e traccia
def split_title(full_title):
    parts = full_title.split(" - ", 1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (None, full_title.strip())

# Funzione per aspettare il download
def wait_for_download(download_dir, existing_files, timeout=180):
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_files = glob.glob(os.path.join(download_dir, "*.m4a"))
        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            if os.path.getsize(file) > 0:
                return True, f"Download completato: {file}", file
        if glob.glob(os.path.join(download_dir, "*.crdownload")):
            st.write("File in download (.crdownload) rilevato, attendo...")
            time.sleep(5)
            continue
        time.sleep(5)
    return False, f"Timeout ({timeout}s) raggiunto.", None

# Funzione per creare l'archivio ZIP
def create_zip_archive(download_dir, zip_name="tracce_scaricate.zip"):
    zip_path = os.path.join(download_dir, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in st.session_state.downloaded_files:
            zipf.write(file, os.path.basename(file))
    return zip_path

# Interfaccia Streamlit
st.title("Downloader di Tracce Musicali")
uploaded_file = st.file_uploader("Carica tracce.txt", type=["txt"])

if uploaded_file is not None:
    tracce = uploaded_file.read().decode("utf-8").splitlines()
    tracce = [t.strip() for t in tracce if t.strip()]
    st.write(f"**Tracce totali:** {len(tracce)}")

    if st.button("Avvia Download"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()

        st.session_state.downloaded_files = []

        for idx, traccia in enumerate(tracce):
            status_text.text(f"Processo: {traccia} ({idx+1}/{len(tracce)})")
            log_container.write(f"### {traccia}")

            artista, titolo = split_title(traccia)
            log_container.write(f"Artista: {artista}, Titolo: {titolo}")

            driver.get("https://lucida.su")
            log_container.write("Pagina caricata.")

            try:
                # Inserimento della traccia
                input_field = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "download"))
                )
                input_field.clear()
                input_field.send_keys(traccia)
                log_container.write("Ricerca inserita.")
                time.sleep(2)

                # Clic sul pulsante "go"
                go_button = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "go"))
                )
                go_button.click()
                log_container.write("Pulsante 'go' cliccato.")
                time.sleep(5)

                # Ricerca della traccia nei risultati
                titles = WebDriverWait(driver, 60).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "h1.svelte-1n1f2yj"))
                )
                log_container.write(f"Risultati trovati: {len(titles)}")
                trovato = False
                for title in titles:
                    title_text = title.text.lower()
                    if titolo.lower() in title_text:
                        driver.execute_script("arguments[0].scrollIntoView(true);", title)
                        title.click()
                        log_container.write(f"✅ Traccia trovata e cliccata: {title_text}")
                        trovato = True
                        break
                if not trovato:
                    log_container.write("❌ Traccia non trovata nei risultati.")
                    continue

                # Selezione formato e qualità
                time.sleep(8)
                Select(WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "convert"))
                )).select_by_value("m4a-aac")
                log_container.write("Formato 'm4a-aac' selezionato.")

                Select(WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "downsetting"))
                )).select_by_value("320")
                log_container.write("Qualità '320kbps' selezionata.")
                time.sleep(2)

                # Avvio del download
                existing_files = glob.glob(os.path.join(download_dir, "*.*"))
                download_button = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "download-button"))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
                download_button.click()
                log_container.write("Pulsante di download cliccato.")

                # Attesa del file
                success, message, file = wait_for_download(download_dir, existing_files)
                if success:
                    st.session_state.downloaded_files.append(file)
                    log_container.write(f"✅ {message}")
                else:
                    log_container.write(f"❌ {message}")
                    st.write(f"Contenuto directory dopo timeout: {os.listdir(download_dir)}")

            except Exception as e:
                log_container.write(f"Errore: {str(e)}")
                st.write(f"Contenuto directory dopo errore: {os.listdir(download_dir)}")

            # Pulizia log
            log_container.empty()
            progress_bar.progress((idx + 1) / len(tracce))

        # Riepilogo e archivio
        status_text.text(f"Completato! Tracce scaricate: {len(st.session_state.downloaded_files)}/{len(tracce)}")
        if st.session_state.downloaded_files:
            zip_path = create_zip_archive(download_dir)
            with open(zip_path, "rb") as zip_file:
                st.download_button(
                    label="Scarica tutte le tracce (ZIP)",
                    data=zip_file,
                    file_name="tracce_scaricate.zip",
                    mime="application/zip"
                )
            st.write(f"File scaricati: {[os.path.basename(f) for f in st.session_state.downloaded_files]}")
        else:
            st.warning("Nessun file scaricato. Controlla i log per dettagli.")

driver.quit()
