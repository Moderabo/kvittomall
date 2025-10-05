import os
import re
import csv
import requests
import magic
import logging
from datetime import datetime

CSV_FILE = "responses.csv"
DOWNLOAD_DIR = "downloads"
LOG_FILE = "download.log"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Setup logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def sanitize_filename(s):
    """Sanitize string to be safe for filenames."""
    s = s.strip()
    s = re.sub(r"[^\w\-_.]", "_", s)  # replace anything not alphanumeric or _-. with _
    return s

def get_drive_file_id(url):
    """Extract Google Drive file ID from a URL."""
    match = re.search(r"id=([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/d/([a-zA-Z0-9_-]+)/", url)
    if match:
        return match.group(1)
    return None

def download_from_google_drive(file_id, destination):
    """Download a file from Google Drive, handling confirmation tokens."""
    URL = "https://docs.google.com/uc?export=download"

    session = requests.Session()
    response = session.get(URL, params={"id": file_id}, stream=True)
    token = get_confirm_token(response)

    if token:
        response = session.get(URL, params={"id": file_id, "confirm": token}, stream=True)

    save_response_content(response, destination)

def get_confirm_token(response):
    """Extract confirmation token from Google Drive response cookies or HTML."""
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            return value
    return None

def save_response_content(response, destination):
    """Write response content to a file."""
    with open(destination, "wb") as f:
        for chunk in response.iter_content(32768):
            if chunk:
                f.write(chunk)

def detect_extension(file_path):
    """Detect file type and return appropriate extension."""
    mime = magic.from_file(file_path, mime=True)
    if mime == "application/pdf":
        return ".pdf"
    elif mime.startswith("image/"):
        ext = mime.split("/")[-1].lower()
        if ext == "jpeg":
            return ".jpg"
        return f".{ext}"
    else:
        raise ValueError(f"Invalid file type: {mime}")

def process_csv(csv_file):
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            links = row.get("Ladda upp kvittot", "")
            if not links:
                continue

            timestamp = row.get("Tidstämpel", "row{}".format(i))
            name = row.get("Namn", "unknown")
            # Sanitize for filenames
            timestamp_s = sanitize_filename(timestamp.replace(" ", "_").replace(":", "-"))
            name_s = sanitize_filename(name.rstrip(" ").replace(" ", "-"))

            for j, link in enumerate(links.split(",")):
                link = link.strip()
                if not link:
                    continue
                file_id = get_drive_file_id(link)
                if not file_id:
                    logging.warning(f"Could not parse link: {link}")
                    continue

                temp_path = os.path.join(DOWNLOAD_DIR, f"{timestamp_s}_{name_s}_file{j}.tmp")
                final_base = os.path.join(DOWNLOAD_DIR, f"{timestamp_s}_{name_s}_file{j}")

                try:
                    download_from_google_drive(file_id, temp_path)
                    ext = detect_extension(temp_path)
                    final_path = final_base + ext
                    os.rename(temp_path, final_path)
                    logging.info(f"✅ Downloaded {final_path}")
                except Exception as e:
                    logging.error(f"❌ Failed row {i} file {j}: {e}")
                    logging.error(f"Row data: {row}")

if __name__ == "__main__":
    logging.info("=== Download started ===")
    process_csv(CSV_FILE)
    logging.info("=== Download finished ===")
