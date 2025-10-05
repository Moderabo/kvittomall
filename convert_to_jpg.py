import os
import shutil
from PIL import Image, ImageOps
import magic
import pillow_heif
import logging

INPUT_DIR = "downloads"
OUTPUT_DIR = "processed"
LOG_FILE = "process.log"
MAX_DIMENSION = 2000   # maximum width or height in pixels
JPEG_QUALITY = 30      # JPEG compression quality

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Setup logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def detect_filetype(file_path):
    """Detect MIME type of a file."""
    return magic.from_file(file_path, mime=True)

def downscale_image(img, max_dim=MAX_DIMENSION):
    """Downscale image to fit within max_dim while keeping aspect ratio."""
    width, height = img.size
    if max(width, height) > max_dim:
        scaling_factor = max_dim / max(width, height)
        new_size = (int(width * scaling_factor), int(height * scaling_factor))
        img = img.resize(new_size, Image.LANCZOS)
    return img

def process_files(input_dir, output_dir):
    for filename in os.listdir(input_dir):
        src_path = os.path.join(input_dir, filename)
        if not os.path.isfile(src_path):
            continue

        try:
            mime = detect_filetype(src_path)

            if mime == "application/pdf":
                # Just move PDF
                dst_path = os.path.join(output_dir, os.path.splitext(filename)[0] + ".pdf")
                shutil.copy2(src_path, dst_path)
                logging.info(f"📄 Moved PDF {src_path} → {dst_path}")

            elif mime.startswith("image/") or mime in ["image/heic", "image/heif"]:
                # Convert image to JPEG
                dst_path = os.path.join(output_dir, os.path.splitext(filename)[0] + ".jpg")

                # Open image
                if mime in ["image/heic", "image/heif"]:
                    heif_file = pillow_heif.read_heif(src_path)
                    img = Image.frombytes(
                        heif_file.mode,
                        heif_file.size,
                        heif_file.data,
                        "raw"
                    )
                else:
                    img = Image.open(src_path)
                
                # Fix orientation based on EXIF
                img = ImageOps.exif_transpose(img)

                # Downscale if needed
                img = downscale_image(img)

                # Convert to RGB and save as JPEG
                rgb_img = img.convert("RGB")
                rgb_img.save(dst_path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
                logging.info(f"🖼️ Converted & downscaled {src_path} → {dst_path}")

            else:
                logging.warning(f"⚠️ Skipped unsupported file type ({mime}): {src_path}")

        except Exception as e:
            logging.error(f"❌ Failed processing {src_path}: {e}", exc_info=True)

if __name__ == "__main__":
    logging.info("=== Processing started ===")
    process_files(INPUT_DIR, OUTPUT_DIR)
    logging.info("=== Processing finished ===")
