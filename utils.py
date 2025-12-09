import os
import re

# Local application imports
from config import TIMESTAMP_COLUMN, NAME_COLUMN


def sanitize_filename(s: str) -> str:
    """
    Sanitize a string to be safe for use in filenames.

    - Strips leading/trailing whitespace.
    - Replaces any character that is not a word character (a-z, A-Z, 0-9, _),
      hyphen, or dot with an underscore.

    Args:
        s: The input string.

    Returns:
        The sanitized string.
    """
    s = s.strip()
    # This regex ensures that only safe characters remain in the filename.
    s = re.sub(r"[^\w\-_.]", "_", s)
    return s

def get_base_filename(row: dict) -> str | None:
    """
    Generates the standardized, sanitized base filename from a CSV row.
    This is the core part of the filename (e.g., "2023-10-27_12-30-00_John-Doe")
    used for both intermediate and final files.

    Args:
        row: A dictionary representing a row from the CSV file.

    Returns:
        The sanitized base filename string, or None if required fields are missing.
    """
    timestamp_raw = row.get(TIMESTAMP_COLUMN, "")
    name_raw = row.get(NAME_COLUMN, "")

    if not timestamp_raw or not name_raw:
        return None

    # Perform initial formatting consistent with previous logic
    timestamp = timestamp_raw.replace(" ", "_").replace(":", "-")
    name = name_raw.rstrip(" ").replace(" ", "-")

    # The full sanitization happens here, ensuring a safe filename part.
    # We sanitize the parts separately to avoid sanitizing the underscore between them.
    sanitized_timestamp = sanitize_filename(timestamp)
    sanitized_name = sanitize_filename(name)

    return f"{sanitized_timestamp}_{sanitized_name}"

def get_final_pdf_filename(row: dict) -> str | None:
    """
    Generates the standardized filename for the final PDF based on a CSV row.

    This function acts as the single source of truth for naming final PDF files,
    ensuring consistency across all scripts in the pipeline.

    Args:
        row: A dictionary representing a row from the CSV file.

    Returns:
        The generated filename string (e.g., "2023-10-27_12-30-00_John-Doe.pdf"),
        or None if the required 'Tidstämpel' or 'Namn' fields are missing.
    """
    base_filename = get_base_filename(row)
    if not base_filename:
        return None
    return f"{base_filename}.pdf"


def final_pdf_exists(row: dict, final_dir: str) -> bool:
    """
    Checks if the final PDF for a given CSV row already exists.

    Args:
        row: A dictionary representing a row from the CSV file.
        final_dir: The directory where final PDFs are stored.

    Returns:
        True if the final PDF file exists, False otherwise.
    """
    final_filename = get_final_pdf_filename(row)
    if not final_filename:
        return False

    final_pdf_path = os.path.join(final_dir, final_filename)
    return os.path.exists(final_pdf_path)
