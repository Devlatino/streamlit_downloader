import streamlit as st
import os
import tempfile
import zipfile
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
import glob
import time
import concurrent.futures

# Spotify Credentials
CLIENT_ID = 'f147b13a0d2d40d7b5d0c3ac36b60769'
CLIENT_SECRET = '566b72290ee94a60ada9164fabb6515b'

# Session State Initialization
if 'downloaded_files' not in st.session_state:
    st.session_state.downloaded_files = []
if 'pending_tracks' not in st.session_state:
    st.session_state.pending_tracks = []
if 'log_messages' not in st.session_state:
    st.session_state.log_messages = []

# Download Directory
download_dir = tempfile.mkdtemp()
st.write(f"Download directory: {download_dir} (Writable: {os.access(download_dir, os.W_OK)})")

# Chrome Options
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
    return options

# Available Formats and Qualities
FORMATS = {
    "m4a-aac": "AAC (.m4a)", "mp3": "MP3 (.mp3)", "flac": "FLAC (.flac)",
    "wav": "WAV (.wav)", "ogg": "OGG (.ogg)"
}
QUALITIES = {
    "320": "320 kbps (High)", "256": "256 kbps (Medium-High)",
    "192": "192 kbps (Medium)", "128": "128 kbps (Low)"
}

# Utility Functions
def split_title(full_title):
    parts = full_title.split(" - ", 1)
    return parts[0].strip(), parts[1].strip() if len(parts) == 2 else (None, full_title.strip())

def normalize_artist(artist_string):
    return artist_string.split(',')[0].strip().lower() if artist_string else ""

def wait_for_download(download_dir, existing_files, formato, timeout=60):
    start_time = time.time()
    ext = formato.split('-')[0] if '-' in formato else formato
    while time.time() - start_time < timeout:
        current_files = glob.glob(os.path.join(download_dir, f"*.{ext}"))
        new_files = [f for f in current_files if f not in existing_files and os.path.getsize(f) > 0]
        if new_files:
            return True, f"Download completed: {new_files[0]}", new_files[0]
        if glob.glob(os.path.join(download_dir, "*.crdownload")):
            time.sleep(1)
        else:
            time.sleep(2)
    return False, f"Timeout ({timeout}s) reached.", None

def create_zip_archive(download_dir, downloaded_files, zip_name="tracce_scaricate.zip"):
    zip_path = os.path.join(download_dir, zip_name)
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in downloaded_files:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    zipf.write(file_path, os.path.basename(file_path))
        return zip_path if os.path.exists(zip_path) else None
    except Exception as e:
        st.session_state.log_messages.append(f"ZIP creation error: {str(e)}")
        return None

def get_spotify_tracks(playlist_link):
    try:
        auth_manager = SpotifyClientCredentials(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        playlist_id = re.search(r'playlist/(\w+)', playlist_link).group(1)
        tracks = []
        results = sp.playlist_tracks(playlist_id)
        tracks.extend(results['items'])
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
        return [f"{', '.join([artist['name'] for artist in item['track']['artists']])} - {item['track']['name']}" 
                for item in tracks]
    except Exception as e:
        st.session_state.log_messages.append(f"Spotify error: {str(e)}")
        return None

# Download Function (Single Browser Instance)
def download_tracks(tracks, servizio_idx, formato_valore, qualita_valore, max_retries=2):
    driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=get_chrome_options())
    downloaded_files = []
    pending_tracks = []
    log = []

    try:
        driver.get("https://lucida.su")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "service")))
        select_service = Select(driver.find_element(By.ID, "service"))
        servizio_valore = select_service.options[servizio_idx].get_attribute("value")
        select_service.select_by_value(servizio_valore)
        time.sleep(1)  # Reduced delay

        for traccia in tracks:
            retries = 0
            success = False
            while retries <= max_retries and not success:
                try:
                    log.append(f"Processing: {traccia}")
                    artist, title = split_title(traccia)
                    input_field = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "download")))
                    input_field.clear()
                    input_field.send_keys(traccia)
                    time.sleep(1)  # Reduced delay

                    select_country = Select(driver.find_element(By.ID, "country"))
                    select_country.select_by_index(0)
                    driver.find_element(By.ID, "go").click()
                    time.sleep(2)  # Reduced delay

                    WebDriverWait(driver, 30).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")) > 0 or "No results found" in d.page_source
                    )
                    titles = driver.find_elements(By.CSS_SELECTOR, "h1.svelte-1n1f2yj")
                    artists = driver.find_elements(By.CSS_SELECTOR, "h2.svelte-1n1f2yj")

                    for i, titolo in enumerate(titles):
                        title_text = titolo.text.strip().lower()
                        title_words = set(title_text.split())
                        track_words = set(title.lower().split())
                        match = len(track_words.intersection(title_words)) / len(track_words) if track_words else 0
                        if match >= 0.7 or title.lower() in title_text:
                            if artist and i < len(artists) and normalize_artist(artist) not in artists[i].text.lower():
                                continue
                            titolo.click()
                            break
                    else:
                        log.append(f"Track not found: {traccia}")
                        pending_tracks.append(traccia)
                        continue

                    select_convert = Select(WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "convert"))))
                    select_convert.select_by_value(formato_valore)
                    select_downsetting = Select(WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, "downsetting"))))
                    select_downsetting.select_by_value(qualita_valore)

                    existing_files = glob.glob(os.path.join(download_dir, "*.*"))
                    driver.find_element(By.CLASS_NAME, "download-button").click()
                    success, msg, file = wait_for_download(download_dir, existing_files, formato_valore)
                    log.append(msg)
                    if success:
                        downloaded_files.append(file)
                    else:
                        pending_tracks.append(traccia)
                    time.sleep(2)  # Small delay to avoid rate limiting
                except Exception as e:
                    retries += 1
                    log.append(f"Retry {retries}/{max_retries} for {traccia}: {str(e)}")
                    time.sleep(5 * retries)  # Exponential backoff
                    if retries > max_retries:
                        pending_tracks.append(traccia)
                        log.append(f"Failed after retries: {traccia}")
            driver.get("https://lucida.su")  # Reset page for next track
            time.sleep(1)
    finally:
        driver.quit()
    return downloaded_files, pending_tracks, log

# Streamlit Interface
st.title("Music Track Downloader (PIZZUNA)")
st.write("Upload a `tracce.txt` file or enter a Spotify playlist link.")

# Preferences
st.subheader("Download Preferences")
formato_selezionato = st.selectbox("Audio Format", options=list(FORMATS.values()), index=0)
formato_valore = list(FORMATS.keys())[list(FORMATS.values()).index(formato_selezionato)]
qualita_selezionata = st.selectbox("Audio Quality", options=list(QUALITIES.values()), index=0)
qualita_valore = list(QUALITIES.keys())[list(QUALITIES.values()).index(qualita_selezionata)]
servizio_idx = st.slider("Service Index", 0, 10, 0)  # Simplified service selection

# Spotify Playlist
st.subheader("Generate tracce.txt from Spotify")
playlist_link = st.text_input("Spotify Playlist Link")
if playlist_link and st.button("Generate from Spotify"):
    tracks = get_spotify_tracks(playlist_link)
    if tracks:
        temp_file = os.path.join(download_dir, "tracce.txt")
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(tracks))
        st.session_state['spotify_file'] = temp_file
        st.success(f"Generated `tracce.txt` with {len(tracks)} tracks.")

# File Upload
uploaded_file = st.file_uploader("Or upload tracce.txt", type=["txt"])
tracce_source = st.session_state.get('spotify_file', uploaded_file)

if tracce_source:
    if isinstance(tracce_source, str):
        with open(tracce_source, 'r', encoding='utf-8') as f:
            tracks = [line.strip() for line in f.readlines() if line.strip()]
    else:
        tracks = tracce_source.read().decode("utf-8").splitlines()
        tracks = [t.strip() for t in tracks if t.strip()]
    
    st.write(f"**Total Tracks:** {len(tracks)}")

    if st.button("Start Download"):
        st.session_state.downloaded_files = []
        st.session_state.pending_tracks = []
        st.session_state.log_messages = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_container = st.empty()

        downloaded_files, pending_tracks, log = download_tracks(tracks, servizio_idx, formato_valore, qualita_valore)
        st.session_state.downloaded_files = downloaded_files
        st.session_state.pending_tracks = pending_tracks
        st.session_state.log_messages = log

        progress_bar.progress(1.0)
        status_text.text(f"Completed! {len(downloaded_files)}/{len(tracks)} tracks downloaded")
        log_container.write("\n".join(log[-10:]))

        if downloaded_files:
            zip_path = create_zip_archive(download_dir, downloaded_files)
            if zip_path:
                with open(zip_path, "rb") as zip_file:
                    st.download_button(
                        label="Download All Tracks (ZIP)",
                        data=zip_file,
                        file_name="tracce_scaricate.zip",
                        mime="application/zip"
                    )
            else:
                st.error("Failed to create ZIP archive.")
        else:
            st.warning("No tracks downloaded successfully.")
