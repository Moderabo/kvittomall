import os
import re
import csv
import logging
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from PyPDF2 import PdfReader, PdfWriter
import os

INPUT_DIR = "processed"
CSV_FILE = "responses.csv"
LOG_FILE = "generatePDF.log"
OUTPUT_DIR = "final"
LOGO_PATH = "logo.png"  # Path to logo image

# --- Setup logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

os.makedirs(OUTPUT_DIR, exist_ok=True)  

def wrap_text(c, text, max_width):
    """
    Wrap text to fit in a given width using the canvas font.
    Returns a list of lines.
    """
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        if c.stringWidth(test_line, c._fontname, c._fontsize) <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines

def generate_pdf(row):
    timestamp = row.get("Tidstämpel", "").replace(" ", "_").replace(":", "-")
    name = row.get("Namn", "").rstrip(" ").replace(" ", "-")
    if not timestamp or not name:
        logging.warning("Missing timestamp or name, skipping row")
        return

    pdf_path = os.path.join(OUTPUT_DIR, f"{timestamp}_{name}.pdf")
    temp_text_pdf = os.path.join(OUTPUT_DIR, f"{timestamp}_{name}_text.pdf")

    # --- Step 1: Generate text page ---
    c = canvas.Canvas(temp_text_pdf, pagesize=A4)
    width, height = A4

    # Draw logo in top-right corner
    if os.path.exists(LOGO_PATH):
        logo = ImageReader(LOGO_PATH)
        logo_width = 3*cm
        logo_height = 3*cm
        c.drawImage(
            logo,
            width - logo_width - 1.5*cm,  # right margin
            height - logo_height - 1.5*cm,  # top margin
            width=logo_width,
            height=logo_height,
            preserveAspectRatio=True,
            mask="auto"
        )

    # Header
    c.setFont("Helvetica-Bold", 28)
    c.drawString(2*cm, height-4*cm, f"Kvittomall - {row.get('Transaktionstyp', '')}")

    # Body
    y = height - 5*cm
    label_width = 4*cm
    max_width = width - 4*cm
    line_height = 0.7*cm
    padding = 0.2*cm

    # Example section grouping
    sections = [
        [("Datum:", "Datum på kvittot"),
         ("Namn:", "Namn"),
         ("Summa:", "Summa"),
         ("Kontonummer:", "Kontonummer")],
        [("Utskott:", "Utskott"),
         ("Arrangemang:", "Arrangemang"),
         ("Specificering:", "Specificering")],
        [("Övrigt:", "Övrigt"),
         ("Extra info:", "Extra info")]
    ]

    for section in sections:
        non_empty_fields = []
        for label, key_csv in section:
            value = str(row.get(key_csv, "")).strip()
            if value:
                if key_csv == "Summa":
                    value = f"{value} kr"
                non_empty_fields.append((label, value))
        if not non_empty_fields:
            continue

        c.line(2*cm, y, 2*cm + max_width, y)
        for label, value in non_empty_fields:
            value_lines = wrap_text(c, value, max_width - label_width - padding)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(2*cm, y - line_height, label)
            c.setFont("Helvetica", 14)
            for i, line in enumerate(value_lines):
                c.drawString(2*cm + label_width, y - line_height*(i+1), line)
            y -= line_height * max(len(value_lines), 1)
        y -= padding
        c.line(2*cm, y, 2*cm + max_width, y)
        y -= 0.5*cm
        if y < 4*cm:
            c.showPage()
            y = height - 2*cm

    c.showPage()
    c.save()

    # --- Step 2: Merge attachments ---
    writer = PdfWriter()
    # Add text page first
    text_reader = PdfReader(temp_text_pdf)
    for page in text_reader.pages:
        writer.add_page(page)

    # Add attachments
    for f in row.get("Ladda upp kvittot", []):
        full_path = os.path.join(INPUT_DIR, f)
        ext = os.path.splitext(f)[1].lower()

        if ext in [".jpg", ".jpeg", ".png", ".heic", ".heif"]:
            temp_img_pdf = os.path.join(OUTPUT_DIR, f"temp_{f}.pdf")
            c = canvas.Canvas(temp_img_pdf, pagesize=A4)

            # Draw logo in top-right corner
            if os.path.exists(LOGO_PATH):
                logo = ImageReader(LOGO_PATH)
                logo_width = 3*cm
                logo_height = 3*cm
                c.drawImage(
                    logo,
                    width - logo_width - 1.5*cm,  # right margin
                    height - logo_height - 1.5*cm,  # top margin
                    width=logo_width,
                    height=logo_height,
                    preserveAspectRatio=True,
                    mask="auto"
                )

            # Header
            c.setFont("Helvetica-Bold", 28)
            c.drawString(2*cm, height-4*cm, f"Kvittomall - Bild på kvittot")
            
            # Draw image scaled to fit
            img = ImageReader(full_path)
            iw, ih = img.getSize()

            # Define margins and header
            top_margin = 2*cm   # space for top of page
            bottom_margin = 2*cm
            header_height = 3*cm

            # Maximum space for image
            max_width = width - 4*cm  # 2cm left + 2cm right margin
            max_height = height - top_margin - bottom_margin - header_height

            # Scale to fit
            scale = min(max_width/iw, max_height/ih, 1.0)  # don't scale up
            img_width_scaled = iw * scale
            img_height_scaled = ih * scale

            # Position image at top of page, below header
            x = 2*cm
            y = height - top_margin - header_height - img_height_scaled

            # Draw image
            c.drawImage(img, x, y, width=img_width_scaled, height=img_height_scaled)

            c.showPage()
            c.save()

            reader = PdfReader(temp_img_pdf)
            writer.add_page(reader.pages[0])
            os.remove(temp_img_pdf)

        elif ext == ".pdf":
            reader = PdfReader(full_path)
            for page in reader.pages:
                writer.add_page(page)

    # Save final PDF
    with open(pdf_path, "wb") as f_out:
        writer.write(f_out)

    os.remove(temp_text_pdf)
    logging.info(f"Generated PDF with attachments: {pdf_path}")

# Pattern to match processed files
file_pattern = re.compile(
    r"^(?P<timestamp>[\d_-]+)_(?P<name>.+?)_file\d+\.(?P<ext>jpg|jpeg|pdf)$"
)

# Build a mapping of available files
available_files = {}
for f in os.listdir(INPUT_DIR):
    m = file_pattern.match(f)
    if m:
        key = f"{m['timestamp']}_{m['name']}"
        available_files.setdefault(key, []).append(f)

# Read CSV and find corresponding files
logging.info("=== Generating PDFs ===")
with open(CSV_FILE, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        timestamp = row.get("Tidstämpel", "").replace(" ", "_").replace(":", "-")
        name = row.get("Namn", "").rstrip(" ").replace(" ", "-")
        if not timestamp or not name:
            logging.error(f"Row {i} missing Tidstämpel or Namn, skipping")
            continue

        key = f"{timestamp}_{name}"
        matched_files = available_files.get(key, [])

        if not matched_files:
            logging.warning(f"No processed files found for row {i} ({key})")
            row["Ladda upp kvittot"] = []
        else:
            logging.info(f"Row {i} ({key}) matched files: {matched_files}")
            row["Ladda upp kvittot"] = matched_files

        generate_pdf(row)

logging.info("=== Finished generating PDFs ===")
