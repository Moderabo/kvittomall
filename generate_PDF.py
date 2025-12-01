import os
import re
import csv
import logging
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from PyPDF2 import PdfReader, PdfWriter
from pdf2image import convert_from_path
from PIL import Image
from io import BytesIO
class PdfGenerator:
    # --- Constants for PDF layout and style ---
    FONT_BOLD = "Helvetica-Bold"
    FONT_NORMAL = "Helvetica"
    HEADER_FONT_SIZE = 28
    BODY_FONT_SIZE = 14

    # Page layout
    PAGE_WIDTH, PAGE_HEIGHT = A4
    MARGIN_TOP = 4 * cm
    MARGIN_SIDE = 2 * cm
    LOGO_MARGIN = 1.5 * cm
    LOGO_SIZE = 3 * cm

    # Page content layout
    PAGE_BREAK_THRESHOLD = 4 * cm
    IMAGE_AREA_BOTTOM_MARGIN = 2 * cm

    # Text layout
    LABEL_WIDTH = 4 * cm
    LINE_HEIGHT = 0.7 * cm
    PADDING = 0.2 * cm

    # PDF processing parameters
    PDF_ATTACHMENT_DPI = 150
    PDF_ATTACHMENT_QUALITY = 75

    # Defines the structure of the main text page.
    # Each inner list is a section, containing tuples of (PDF Label, CSV Header).
    PDF_SECTIONS = [
        [("Datum:", "Datum på kvittot"), ("Namn:", "Namn"), ("Summa:", "Summa"), ("Kontonummer:", "Kontonummer")],
        [("Utskott:", "Utskott"), ("Arrangemang:", "Arrangemang"), ("Specificering:", "Specificering")],
        [("Övrigt:", "Övrigt"), ("Extra info:", "Extra info")]
    ]

    def __init__(self, input_dir="processed", csv_file="responses.csv", output_dir="final", log_file="generatePDF.log", logo_path="logo.png"):
        self.input_dir = input_dir
        self.csv_file = csv_file
        self.output_dir = output_dir
        self.logo_path = logo_path
        self.log_file = log_file

        self.file_pattern = re.compile(
            r"^(?P<timestamp>[\d_-]+)_(?P<name>.+?)_file\d+\.(?P<ext>jpg|jpeg|pdf)$"
        )

        os.makedirs(self.output_dir, exist_ok=True)
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

    def _resize_pdf_to_a4(self, input_pdf_path):
        """Renders all pages of a PDF, resizing each to fit on an A4 page."""
        # This buffer will hold the new multi-page PDF in memory.
        output_buffer = BytesIO()
        
        # Use a temporary canvas to create the new PDF.
        c = canvas.Canvas(output_buffer, pagesize=A4)

        # Convert all pages of the source PDF to a list of images.
        images = convert_from_path(input_pdf_path, dpi=self.PDF_ATTACHMENT_DPI)

        for pil_img in images:
            pil_img = pil_img.convert("RGB")
            iw, ih = pil_img.size
            scale = min(self.PAGE_WIDTH / iw, self.PAGE_HEIGHT / ih)
            new_size = (int(iw * scale), int(ih * scale))
            pil_img.resize(new_size, Image.LANCZOS)
            c.drawImage(ImageReader(pil_img), 0, 0, width=self.PAGE_WIDTH, height=self.PAGE_HEIGHT, preserveAspectRatio=True)
            c.showPage()
        c.save()
        output_buffer.seek(0)
        return PdfReader(output_buffer)

    @staticmethod
    def _wrap_text(c, text, max_width):
        """Wraps text to fit in a given width."""
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

    def _draw_page_header(self, canvas_obj, title):
        """Draws the logo and title header on a canvas."""
        # Draw logo in top-right corner
        if os.path.exists(self.logo_path):
            logo = ImageReader(self.logo_path)
            canvas_obj.drawImage(
                logo,
                self.PAGE_WIDTH - self.LOGO_SIZE - self.LOGO_MARGIN,
                self.PAGE_HEIGHT - self.LOGO_SIZE - self.LOGO_MARGIN,
                width=self.LOGO_SIZE, height=self.LOGO_SIZE,
                preserveAspectRatio=True, mask="auto"
            )
        # Draw header text
        canvas_obj.setFont(self.FONT_BOLD, self.HEADER_FONT_SIZE)
        canvas_obj.drawString(self.MARGIN_SIDE, self.PAGE_HEIGHT - self.MARGIN_TOP, title)

    def _generate_pdf_for_row(self, row):
        """Generates a single PDF for a given CSV row."""
        timestamp = row.get("Tidstämpel", "").replace(" ", "_").replace(":", "-")
        name = row.get("Namn", "").rstrip(" ").replace(" ", "-")
        if not timestamp or not name:
            logging.warning("⚠️ Missing timestamp or name, skipping row")
            return

        pdf_path = os.path.join(self.output_dir, f"{timestamp}_{name}.pdf")
        temp_text_pdf = os.path.join(self.output_dir, f"{timestamp}_{name}_text.pdf")

        # --- Step 1: Generate text page ---
        canvas_obj = canvas.Canvas(temp_text_pdf, pagesize=A4)
        self._draw_page_header(canvas_obj, f"Kvittomall - {row.get('Transaktionstyp', '')}")

        y = self.PAGE_HEIGHT - 5 * cm
        max_text_width = self.PAGE_WIDTH - (2 * self.MARGIN_SIDE)

        for section in self.PDF_SECTIONS:
            non_empty_fields = []
            for label, key_csv in section:
                value = str(row.get(key_csv, "")).strip()
                if value:
                    if key_csv == "Summa":
                        value = f"{value} kr"
                    non_empty_fields.append((label, value))
            if not non_empty_fields:
                continue

            canvas_obj.line(self.MARGIN_SIDE, y, self.MARGIN_SIDE + max_text_width, y)
            for label, value in non_empty_fields:
                value_lines = self._wrap_text(canvas_obj, value, max_text_width - self.LABEL_WIDTH - self.PADDING)
                canvas_obj.setFont(self.FONT_BOLD, self.BODY_FONT_SIZE)
                canvas_obj.drawString(self.MARGIN_SIDE, y - self.LINE_HEIGHT, label)
                canvas_obj.setFont(self.FONT_NORMAL, self.BODY_FONT_SIZE)
                for i, line in enumerate(value_lines):
                    canvas_obj.drawString(self.MARGIN_SIDE + self.LABEL_WIDTH, y - self.LINE_HEIGHT * (i + 1), line)
                y -= self.LINE_HEIGHT * max(len(value_lines), 1)
            y -= self.PADDING
            canvas_obj.line(self.MARGIN_SIDE, y, self.MARGIN_SIDE + max_text_width, y)
            y -= 0.5 * cm
            if y < self.PAGE_BREAK_THRESHOLD:
                canvas_obj.showPage()
                y = self.PAGE_HEIGHT - 2 * cm

        canvas_obj.showPage()
        canvas_obj.save()

        # --- Step 2: Merge text page and all attachments ---
        writer = PdfWriter()
        with open(temp_text_pdf, "rb") as f:
            reader = PdfReader(f)
            writer.append_pages_from_reader(reader)

        self._add_attachments_to_writer(writer, row.get("Ladda upp kvittot", []))

        with open(pdf_path, "wb") as f_out:
            writer.write(f_out)

        os.remove(temp_text_pdf)
        logging.info(f"✅ Generated PDF with attachments: {pdf_path}")

    def _add_attachments_to_writer(self, writer, attachment_files):
        """Processes and adds attachment files to the PdfWriter."""
        for filename in attachment_files:
            full_path = os.path.join(self.input_dir, filename)
            ext = os.path.splitext(filename)[1].lower()

            if not os.path.exists(full_path):
                logging.warning(f"🤔 Attachment file not found, skipping: {full_path}")
                continue

            try:
                if ext in [".jpg", ".jpeg"]:
                    temp_img_pdf = os.path.join(self.output_dir, f"temp_{os.path.basename(filename)}.pdf")
                    canvas_obj = canvas.Canvas(temp_img_pdf, pagesize=A4)
                    self._draw_page_header(canvas_obj, "Kvittomall - Bild på kvittot")

                    img = ImageReader(full_path)
                    iw, ih = img.getSize()
                    
                    header_height = 3 * cm
                    max_img_width = self.PAGE_WIDTH - (2 * self.MARGIN_SIDE)
                    max_img_height = self.PAGE_HEIGHT - self.MARGIN_TOP - self.IMAGE_AREA_BOTTOM_MARGIN - header_height
                    
                    scale = min(max_img_width / iw, max_img_height / ih, 1.0)
                    img_width_scaled, img_height_scaled = iw * scale, ih * scale
                    
                    x_pos = self.MARGIN_SIDE
                    y_pos = self.PAGE_HEIGHT - self.MARGIN_TOP - header_height - img_height_scaled
                    canvas_obj.drawImage(img, x_pos, y_pos, width=img_width_scaled, height=img_height_scaled)

                    canvas_obj.showPage()
                    canvas_obj.save()

                    with open(temp_img_pdf, "rb") as f_img:
                        reader = PdfReader(f_img)
                        writer.append_pages_from_reader(reader)
                    os.remove(temp_img_pdf)

                elif ext == ".pdf":
                    reader = self._resize_pdf_to_a4(full_path)
                    writer.append_pages_from_reader(reader)

            except Exception as e:
                logging.error(f"❌ Failed to process attachment {filename}: {e}", exc_info=True)

    def _build_file_map(self):
        """Builds a mapping of available processed files."""
        available_files = {}
        for f in os.listdir(self.input_dir):
            m = self.file_pattern.match(f)
            if m:
                key = f"{m['timestamp']}_{m['name']}"
                available_files.setdefault(key, []).append(f)
        return available_files

    def run(self):
        """Main execution method to generate all PDFs."""
        logging.info("🚀 === Generating PDFs ===")
        available_files = self._build_file_map()

        with open(self.csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                timestamp = row.get("Tidstämpel", "").replace(" ", "_").replace(":", "-")
                name = row.get("Namn", "").rstrip(" ").replace(" ", "-")
                if not timestamp or not name:
                    logging.error(f"❌ Row {i} missing Tidstämpel or Namn, skipping")
                    continue

                key = f"{timestamp}_{name}"
                matched_files = available_files.get(key, [])

                if not matched_files:
                    logging.warning(f"🤔 No processed files found for row {i} ({key})")
                else:
                    logging.info(f"🔗 Row {i} ({key}) matched files: {matched_files}")

                row["Ladda upp kvittot"] = matched_files
                self._generate_pdf_for_row(row)

        logging.info("🎉 === Finished generating PDFs ===")

if __name__ == "__main__":
    generator = PdfGenerator()
    generator.run()
