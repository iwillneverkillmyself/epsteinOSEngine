"""Convert PDF pages to images."""
from pathlib import Path
from typing import List
import logging
from PIL import Image
import io

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

try:
    from pdf2image import convert_from_path
    _HAS_PDF2IMAGE = True
except Exception:
    _HAS_PDF2IMAGE = False


def pdf_to_images(pdf_path: Path, output_dir: Path, dpi: int = 300) -> List[Path]:
    """
    Convert PDF pages to images.
    
    Args:
        pdf_path: Path to PDF file
        output_dir: Directory to save images
        dpi: Resolution for conversion
        
    Returns:
        List of paths to created image files
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []
    
    try:
        pdf_name = pdf_path.stem

        # Prefer PyMuPDF (no poppler dependency) if available.
        if _HAS_FITZ:
            doc = fitz.open(str(pdf_path))
            try:
                for i in range(len(doc)):
                    page = doc[i]
                    # dpi -> scale factor: 72dpi is 1.0
                    scale = max(dpi / 72.0, 1.0)
                    mat = fitz.Matrix(scale, scale)
                    pix = page.get_pixmap(matrix=mat)
                    image_filename = f"{pdf_name}_page_{(i+1):04d}.png"
                    image_path = output_dir / image_filename
                    pix.save(str(image_path))
                    image_paths.append(image_path)
                    logger.debug(f"Converted page {i+1} of {pdf_path.name} -> {image_path}")
            finally:
                doc.close()
            logger.info(f"Converted {len(image_paths)} pages from {pdf_path.name} (PyMuPDF)")
        else:
            if not _HAS_PDF2IMAGE:
                raise RuntimeError(
                    "No PDF renderer available. Install PyMuPDF (recommended) or pdf2image+poppler."
                )
            images = convert_from_path(
                str(pdf_path),
                dpi=dpi,
                fmt='png'
            )
            for i, image in enumerate(images, start=1):
                image_filename = f"{pdf_name}_page_{i:04d}.png"
                image_path = output_dir / image_filename
                image.save(image_path, 'PNG')
                image_paths.append(image_path)
                logger.debug(f"Converted page {i} of {pdf_path.name} -> {image_path}")
            logger.info(f"Converted {len(images)} pages from {pdf_path.name} (pdf2image)")
        
    except Exception as e:
        logger.error(f"Error converting PDF {pdf_path}: {e}")
        raise
    
    return image_paths


def is_pdf(file_path: Path) -> bool:
    """Check if file is a PDF."""
    return file_path.suffix.lower() == '.pdf'

