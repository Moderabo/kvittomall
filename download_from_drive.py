# Standard library imports
import os
import re
import csv
import logging
from typing import Optional

# Third-party imports
import requests
import magic

# Local application imports
from utils import get_final_pdf_filename, final_pdf_exists, get_base_filename
from config import RECEIPT_LINKS_COLUMN, TIMESTAMP_COLUMN, NAME_COLUMN

class Downloader:
    """
    Downloads files from Google Drive links listed in a CSV file.
    Each file is saved to a local directory with a sanitized filename.
    """
    CHUNK_SIZE = 32768  # Size of chunks to read when downloading files

    def __init__(self, csv_file: str = "responses.csv", download_dir: str = "downloads", final_dir: str = "final", log_file: str = "logs/download.log"):
        """
        Initialize the Downloader.
        Args:
            csv_file (str): Path to the CSV file containing download links.
            download_dir (str): Directory to save downloaded files.
            final_dir (str): Directory where final PDFs are stored, to check for existing files.
            log_file (str): Path to the log file.
        """
        self.csv_file = csv_file
        self.download_dir = download_dir
        self.final_dir = final_dir
        self.log_file = log_file
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        self._setup_logging()

    def _setup_logging(self):
        """Sets up logging for the instance."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.log_file, mode="a", encoding="utf-8"),
                logging.StreamHandler()
            ]
        )

    def download_from_google_drive(self, file_id: str, destination: str) -> None:
        """
        Download a file from Google Drive, handling confirmation tokens for large files.
        Args:
            file_id (str): Google Drive file ID.
            destination (str): Path to save the downloaded file.
        """
        URL = "https://docs.google.com/uc?export=download"
        session = requests.Session()
        response = session.get(URL, params={"id": file_id}, stream=True)
        token = self.get_confirm_token(response)
        if token:
            response = session.get(URL, params={"id": file_id, "confirm": token}, stream=True)
        self.save_response_content(response, destination)

    def process_csv(self) -> None:
        """
        Process the CSV file and download all files listed in the 'Ladda upp kvittot' column.
        Each file is saved with a sanitized filename based on timestamp and name.
        Logs success or failure for each file.
        """
        with open(self.csv_file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                final_pdf_filename = get_final_pdf_filename(row)
                logging.info(f"Processing row {i}: {final_pdf_filename or 'INVALID ROW'}")
                if final_pdf_exists(row, self.final_dir):
                    logging.info(f"✅ Final PDF already exists, skipping row {i}: {os.path.join(self.final_dir, final_pdf_filename)}")
                    continue

                links = row.get(RECEIPT_LINKS_COLUMN, "")
                if not links:
                    continue
                base_filename = get_base_filename(row)
                if not base_filename:
                    logging.warning(f"⚠️ Row {i} is missing '{TIMESTAMP_COLUMN}' or '{NAME_COLUMN}', cannot generate filename. Skipping downloads.")
                    continue

                for j, link in enumerate(links.split(",")):
                    link = link.strip()
                    if not link:
                        continue
                    file_id = self.get_drive_file_id(link)
                    if not file_id:
                        logging.warning(f"⚠️ Could not parse link: {link}")
                        continue
                    temp_path = os.path.join(self.download_dir, f"{base_filename}_file{j}.tmp")
                    final_base = os.path.join(self.download_dir, f"{base_filename}_file{j}")
                    try:
                        self.download_from_google_drive(file_id, temp_path)
                        ext = self.detect_extension(temp_path)
                        final_path = f"{final_base}{ext}"
                        os.rename(temp_path, final_path)
                        logging.info(f"✅ Downloaded {final_path}")
                    except Exception as e:
                        logging.error(f"❌ Failed row {i} file {j}: {e}")
                        logging.error(f"Row data: {row}")

    # --- Static helper methods ---
    @staticmethod
    def get_drive_file_id(url: str) -> Optional[str]:
        """
        Extract Google Drive file ID from a URL.
        Args:
            url (str): Google Drive URL.
        Returns:
            Optional[str]: File ID if found, else None.
        """
        match = re.search(r"id=([a-zA-Z0-9_-]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"/d/([a-zA-Z0-9_-]+)/", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def get_confirm_token(response) -> Optional[str]:
        """
        Extract confirmation token from Google Drive response cookies or HTML.
        Args:
            response (requests.Response): Response object from requests.
        Returns:
            Optional[str]: Confirmation token if present, else None.
        """
        for key, value in response.cookies.items():
            if key.startswith("download_warning"):
                return value

        # Fallback for when the token is in the response body
        if "text/html" in response.headers.get("Content-Type", ""):
            match = re.search(r'confirm=([a-zA-Z0-9\-_]+)"', response.text)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def save_response_content(response, destination: str) -> None:
        """
        Write response content to a file in chunks.
        Args:
            response (requests.Response): Response object from requests.
            destination (str): Path to save the file.
        """
        with open(destination, "wb") as f:
            for chunk in response.iter_content(Downloader.CHUNK_SIZE):
                if chunk:
                    f.write(chunk)

    @staticmethod
    def detect_extension(file_path: str) -> str:
        """
        Detect file type and return appropriate extension based on MIME type.
        Args:
            file_path (str): Path to the file.
        Returns:
            str: File extension (e.g., '.pdf', '.jpg').
        Raises:
            ValueError: If file type is unsupported.
        """
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

if __name__ == "__main__":
    """
    Main entry point for the script. Sets up logging, creates a Downloader, and processes the CSV.
    """
    downloader = Downloader(
        csv_file="responses.csv",
        download_dir="downloads",
        final_dir="final",
        log_file="logs/download.log"
    )
    logging.info("🚀 === Download started ===")
    downloader.process_csv()
    logging.info("🎉 === Download finished ===")
