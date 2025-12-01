import os
import shutil
from PIL import Image, ImageOps
import magic
import pillow_heif
import logging

class ImageProcessor:
    def __init__(self, input_dir="downloads", output_dir="processed", log_file="process.log", max_dimension=2000, jpeg_quality=30):
        self.INPUT_DIR = input_dir
        self.OUTPUT_DIR = output_dir
        self.LOG_FILE = log_file
        self.MAX_DIMENSION = max_dimension
        self.JPEG_QUALITY = jpeg_quality
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)
        self._setup_logging()

    def _setup_logging(self):
        """Sets up logging for the instance."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.LOG_FILE, mode="a", encoding="utf-8"),
                logging.StreamHandler()
            ]
        )

    @staticmethod
    def detect_filetype(file_path):
        """Detect MIME type of a file."""
        return magic.from_file(file_path, mime=True)

    def downscale_image(self, img, max_dim=None):
        """Downscale image to fit within max_dim while keeping aspect ratio."""
        if max_dim is None:
            max_dim = self.MAX_DIMENSION
        width, height = img.size
        if max(width, height) > max_dim:
            scaling_factor = max_dim / max(width, height)
            new_size = (int(width * scaling_factor), int(height * scaling_factor))
            img = img.resize(new_size, Image.LANCZOS)
        return img

    def process_files(self, input_dir=None, output_dir=None):
        if input_dir is None:
            input_dir = self.INPUT_DIR
        if output_dir is None:
            output_dir = self.OUTPUT_DIR
        for filename in os.listdir(input_dir):
            src_path = os.path.join(input_dir, filename)
            if not os.path.isfile(src_path):
                continue

            try:
                mime = self.detect_filetype(src_path)

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
                    img = self.downscale_image(img)

                    # Convert to RGB and save as JPEG
                    rgb_img = img.convert("RGB")
                    rgb_img.save(dst_path, "JPEG", quality=self.JPEG_QUALITY, optimize=True, progressive=True)
                    logging.info(f"🖼️ Converted & downscaled {src_path} → {dst_path}")

                else:
                    logging.warning(f"⚠️ Skipped unsupported file type ({mime}): {src_path}")

            except Exception as e:
                logging.error(f"❌ Failed processing {src_path}: {e}", exc_info=True)

    def run(self):
        logging.info("🚀 === Processing started ===")
        self.process_files()
        logging.info("🎉 === Processing finished ===")


if __name__ == "__main__":
    processor = ImageProcessor()
    processor.run()
