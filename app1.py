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
st.write(f"Directory di download: {download_dir}")

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
driver = webdriver.Chrome(options=options)

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
        new_files = [f for f in current_files if f not in existing_files]
        for file in new_files:
            if os.path.getsize(file) > 0:
                return True, f"Download completato: {file}", file
        if glob.glob(os.path.join(download_dir, "*.crdownload")):
            time.sleep(5)
            continue
        time.sleep(5)
    return False, "Timeout raggiunto.", None

# Funzione per creare l'archivio ZIP
def create_zip_archive(download_dir, zip_name="tracce_scaricate.zip"):
    zip_path = os.path.join(download_dir, zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
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
        log_container = st.empty()  # Contenitore per i log

        st.session_state.downloaded_files = []

        for idx, traccia in enumerate(tracce):
            status_text.text(f"Processo: {traccia} ({idx+1}/{len(tracce)})")
            log_container.write(f"### {traccia}")

            artista, titolo = split_title(traccia)
            log_container.write(f"Artista: {artista}, Titolo: {titolo}")

            driver.get("https://lucida.su")
            log_container.write("Accesso al sito...")

            try:
                input_field = WebDriverWait(driver, 20).until(
                    EC.element_to_be_clickable((By.ID, "download"))
                )
                input_field.clear()
                input_field.send_keys(traccia)
                log_container.write("Ricerca inserita.")
                time.sleep(2)

                go_button = WebDriverWait(driver, 20).until(
                    EC.element_to_be_clickable((By.ID, "go"))
                )
                go_button.click()
                log_container.write("Ricerca avviata.")
                time.sleep(5)

                titles = WebDriverWait(driver, 60).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "h1.svelte-1n1f2yj"))
                )
                trovato = False
                for title in titles:
                    if titolo.lower() in title.text.lower():
                        title.click()
                        log_container.write(f"✅ Traccia trovata: {title.text}")
                        trovato = True
                        break

                if not trovato:
                    log_container.write("❌ Traccia non trovata.")
                    continue

                time.sleep(8)
                Select(WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "convert"))
                )).select_by_value("m4a-aac")
                log_container.write("Formato selezionato.")

                Select(WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "downsetting"))
                )).select_by_value("320")
                log_container.write("Qualità selezionata.")

                existing_files = [os.path.abspath(f) for f in glob.glob(os.path.join(download_dir, "*.*"))]
                download_button = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "download-button"))
                )
                download_button.click()
                log_container.write("Download avviato.")

                success, message, file = wait_for_download(download_dir, existing_files)
                if success:
                    st.session_state.downloaded_files.append(file)
                    log_container.write(f"✅ {message}")
                else:
                    log_container.write(f"❌ {message}")

            except Exception as e:
                log_container.write(f"Errore: {str(e)}")

            # Pulisci i log per questa traccia
            log_container.empty()
            progress_bar.progress((idx + 1) / len(tracce))

        # Riepilogo e archivio
        st.write(f"**Tracce scaricate:** {len(st.session_state.downloaded_files)}/{len(tracce)}")
        if st.session_state.downloaded_files:
            zip_path = create_zip_archive(download_dir)
            with open(zip_path, "rb") as zip_file:
                st.download_button(
                    label="Scarica tutte le tracce (ZIP)",
                    data=zip_file,
                    file_name="tracce_scaricate.zip",
                    mime="application/zip"
                )
        else:
            st.warning("Nessun file scaricato.")

driver.quit()
