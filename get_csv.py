# Standard library imports
import logging
import io
import os

# Third-party imports
import pandas as pd
import requests
from dotenv import load_dotenv

# Local application imports
from config import TIMESTAMP_COLUMN

class SheetProcessor:
    """
    Handles downloading, validating, and saving data from a public Google Sheet.
    """
    def __init__(self, sheet_id: str, sheet_gid: int, output_filename: str, log_file: str = "logs/get_csv.log"):
        """
        Initializes the SheetProcessor.

        Args:
            sheet_id: The ID of the Google Sheet.
            sheet_gid: The GID of the specific sheet/tab.
            output_filename: The path to save the final CSV file.
            log_file: The path to the log file.
        """
        self.sheet_id = sheet_id
        self.sheet_gid = sheet_gid
        self.output_filename = output_filename
        self.log_file = log_file
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        self._setup_logging()

    def _setup_logging(self):
        """Sets up logging to file and console, consistent with other project scripts."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.log_file, mode="a", encoding="utf-8"),
                logging.StreamHandler()
            ]
        )

    def _download_sheet(self) -> pd.DataFrame | None:
        """Downloads the configured Google Sheet as a pandas DataFrame."""
        url = f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=csv&gid={self.sheet_gid}"
        logging.info(f"Attempting to download from: {url}")

        try:
            response = requests.get(url)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error downloading the sheet: {e}")
            logging.error("A 4xx error often means the sheet is not publicly shared ('Anyone with the link') or the ID/GID is incorrect.")
            return None

        csv_data = io.StringIO(response.text)
        df = pd.read_csv(csv_data)
        return df

    def _validate_and_save(self, df: pd.DataFrame):
        """Validates and saves the dataframe to the configured output file."""
        if df.empty:
            logging.warning("⚠️  The downloaded sheet is empty (it may only contain headers).")
            df.to_csv(self.output_filename, index=False, encoding='utf-8-sig')
            logging.info(f"✅ Empty data file with headers saved to '{self.output_filename}'.")
            return

        if df.columns.empty:
            logging.error("❌ DataFrame has no columns, cannot perform validation.")
            return

        logging.info(f"Verifying chronological order of column: '{TIMESTAMP_COLUMN}'...")

        timestamps = pd.to_datetime(df[TIMESTAMP_COLUMN], format='%Y-%m-%d %H.%M.%S', errors='coerce')

        if timestamps.isnull().any():
            logging.error("❌ CRITICAL: Chronological check failed. Found rows with invalid date format.")
            bad_rows = df[timestamps.isnull()][[TIMESTAMP_COLUMN]]
            logging.error(f"The following rows in column '{TIMESTAMP_COLUMN}' could not be parsed:\n{bad_rows}")
            logging.error("Aborting save. The CSV file will not be updated.")
            return

        if not timestamps.is_monotonic_increasing:
            logging.error("❌ CRITICAL: Chronological check failed. The timestamp column is not in order.")
            diffs = timestamps.diff()
            first_bad_index = diffs[diffs.dt.total_seconds() < 0].index.min()
            if first_bad_index is not None and first_bad_index > 0:
                logging.error(f"First out-of-order entry found at row index {first_bad_index}:")
                logging.error(f"  Previous row: {df.iloc[first_bad_index-1][TIMESTAMP_COLUMN]}")
                logging.error(f"  Current row:  {df.iloc[first_bad_index][TIMESTAMP_COLUMN]}")
            logging.error("Aborting save. The CSV file will not be updated.")
            return

        logging.info("✅ Timestamps are in chronological order.")

        logging.info(f"Downloaded {len(df)} rows of data.")
        df.to_csv(self.output_filename, index=False, encoding='utf-8-sig')
        logging.info(f"✅ Data successfully saved to '{self.output_filename}'.")

    def run(self):
        """Executes the full download and validation process."""
        logging.info("🚀 === Starting Google Sheet download ===")
        dataframe = self._download_sheet()

        if dataframe is not None:
            logging.info("✅ Successfully downloaded data from Google Sheets.")
            self._validate_and_save(dataframe)
        else:
            logging.error("❌ Download failed. Please check the sheet ID, GID, and that sharing is set to 'Anyone with the link'.")

        logging.info("🎉 === Download script finished ===")

if __name__ == "__main__":
    # Load environment variables from a .env file in the project root
    load_dotenv()

    # --- Configuration ---
    # The ID of your Google Sheet. Read from the .env file.
    MY_SHEET_ID = os.getenv("SHEET_ID")

    # The GID of the specific sheet/tab. Read from the .env file.
    MY_SHEET_GID = os.getenv("SHEET_GID")

    # The file that the other scripts in your project expect to read from.
    OUTPUT_FILENAME = "responses.csv"

    if not MY_SHEET_ID or not MY_SHEET_GID:
        raise ValueError("Error: SHEET_ID and SHEET_GID must be set in your .env file.")

    # Instantiate the processor and run the process
    processor = SheetProcessor(
        sheet_id=MY_SHEET_ID,
        sheet_gid=int(MY_SHEET_GID),
        output_filename=OUTPUT_FILENAME
    )
    processor.run()
