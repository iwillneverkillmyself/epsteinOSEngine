"""
Process photo volumes (VOL00002) with Rekognition only.
Uses concurrent processing for 3-5x faster execution.
Memory-optimized for large multi-page PDFs.
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
import logging
import io
import gc
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm
from PIL import Image

from config import Config
from database import init_db, SessionLocal
from models import Document, ImagePage, Celebrity
from ingestion.crawler import DocumentCrawler
from ingestion.pdf_converter import is_pdf
from ingestion.storage import DocumentStorage
from ocr.rekognition import RekognitionProcessor

# Memory limit for multi-page PDFs: process pages one at a time
MAX_PAGES_IN_MEMORY = 3

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Thread-safe counters
stats_lock = Lock()
processed_count = 0
celebrities_count = 0


def resize_for_rekognition(image_path: Path, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    """
    Resize image to fit within Rekognition's 5MB limit.
    Returns JPEG bytes.
    """
    img = Image.open(image_path)
    
    # Convert to RGB if necessary (for PNG with alpha)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    quality = 90
    while quality >= 30:
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality)
        if buffer.tell() <= max_bytes:
            img.close()
            return buffer.getvalue()
        quality -= 10
    
    # If still too big, reduce dimensions
    while True:
        img = img.resize((int(img.width * 0.75), int(img.height * 0.75)), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=70)
        if buffer.tell() <= max_bytes:
            img.close()
            return buffer.getvalue()


def pdf_to_single_image(pdf_path: Path, output_dir: Path, page_num: int) -> Path:
    """
    Convert a single page from a PDF to an image.
    Memory-efficient: only loads one page at a time.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        from pdf2image import convert_from_path
        # Fallback to pdf2image (less memory efficient)
        images = convert_from_path(pdf_path, first_page=page_num, last_page=page_num, dpi=150)
        if images:
            output_path = output_dir / f"{pdf_path.stem}_page_{page_num:04d}.png"
            images[0].save(output_path, 'PNG')
            images[0].close()
            del images
            return output_path
        return None
    
    # Use PyMuPDF for memory-efficient processing
    doc = fitz.open(pdf_path)
    if page_num <= len(doc):
        page = doc[page_num - 1]  # 0-indexed
        mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for quality
        pix = page.get_pixmap(matrix=mat)
        output_path = output_dir / f"{pdf_path.stem}_page_{page_num:04d}.png"
        pix.save(output_path)
        pix = None
        doc.close()
        return output_path
    doc.close()
    return None


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get number of pages in a PDF without loading all pages."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except ImportError:
        from pdf2image.pdf2image import pdfinfo_from_path
        info = pdfinfo_from_path(pdf_path)
        return info.get('Pages', 1)


def process_single_file(file_info: dict, temp_dir: Path, storage: DocumentStorage, 
                        rekognition: RekognitionProcessor, crawler_fetch_sync) -> tuple:
    """
    Process a single file through Rekognition.
    Returns (success: bool, celebs_found: int)
    """
    global processed_count, celebrities_count
    
    db = SessionLocal()
    celebs_found = 0
    
    try:
        # Check if already in database
        existing = db.query(Document).filter(
            Document.file_name == file_info['filename']
        ).first()
        
        if existing:
            # Check if celebrity detection was already done
            existing_celebs = db.query(Celebrity).filter(
                Celebrity.document_id == existing.id
            ).first()
            if existing_celebs:
                db.close()
                return (False, 0)  # Already processed
            doc_id = existing.id
        else:
            # Download and store document
            save_path = temp_dir / file_info['filename']
            if not save_path.exists():
                # Use sync fetch
                success = crawler_fetch_sync(file_info['url'], save_path)
                if not success:
                    db.close()
                    return (False, 0)
            
            file_info['local_path'] = str(save_path)
            file_info['file_size'] = save_path.stat().st_size
            doc_id, _ = storage.store_document(file_info)
        
        # Convert PDF to images if needed
        file_path = Path(file_info.get('local_path', temp_dir / file_info['filename']))
        
        if not file_path.exists():
            db.close()
            return (False, 0)
        
        # Get page count and process one page at a time for memory efficiency
        if is_pdf(file_path):
            page_count = get_pdf_page_count(file_path)
            images_dir = temp_dir / f"{doc_id}_images"
            images_dir.mkdir(parents=True, exist_ok=True)
        else:
            page_count = 1
            images_dir = None
        
        # Store image pages and run Rekognition - ONE PAGE AT A TIME
        for page_num in range(1, page_count + 1):
            try:
                # Generate image for this page only (memory efficient)
                if is_pdf(file_path):
                    image_path = pdf_to_single_image(file_path, images_dir, page_num)
                    if not image_path:
                        continue
                else:
                    image_path = file_path
                
                img = Image.open(image_path)
                width, height = img.size
                img.close()  # Close immediately after getting dimensions
                
                # Store or get image page
                page = db.query(ImagePage).filter(
                    ImagePage.document_id == doc_id,
                    ImagePage.page_number == page_num
                ).first()
                
                if not page:
                    page_id = f"{doc_id}_page_{page_num:04d}"
                    page = ImagePage(
                        id=page_id,
                        document_id=doc_id,
                        page_number=page_num,
                        image_path=str(image_path),
                        width=width,
                        height=height,
                        ocr_processed=True
                    )
                    db.add(page)
                    db.commit()
                    db.refresh(page)
                
                # Run celebrity detection
                celebs = rekognition.process_celebrities(page.id)
                if celebs:
                    celebs_found += celebs if isinstance(celebs, int) else len(celebs)
                    logger.info(f"Found celebrities in {file_info['filename']} page {page_num}")
                
                # Clean up this page's image to free memory
                if is_pdf(file_path) and image_path and image_path.exists():
                    try:
                        image_path.unlink()
                    except:
                        pass
                
                # Force garbage collection every few pages
                if page_num % 3 == 0:
                    gc.collect()
                
            except Exception as e:
                if "5242880" in str(e):  # Size limit error
                    logger.warning(f"Image too large, skipping: {file_info['filename']}")
                else:
                    logger.error(f"Error processing page {page_num} of {file_info['filename']}: {e}")
                db.rollback()
        
        # Clean up images directory for this document
        if images_dir and images_dir.exists():
            try:
                shutil.rmtree(images_dir)
            except:
                pass
        
        # Force garbage collection after each document
        gc.collect()
        
        with stats_lock:
            processed_count += 1
            celebrities_count += celebs_found
        
        db.close()
        return (True, celebs_found)
        
    except Exception as e:
        logger.error(f"Error processing {file_info['filename']}: {e}")
        db.rollback()
        db.close()
        return (False, 0)


async def process_photo_volumes(volumes: list = None, limit: int = None, workers: int = 5):
    """
    Process photo volumes with Rekognition celebrity detection.
    Uses concurrent processing for speed.
    
    Args:
        volumes: List of volume prefixes (default: ["VOL00002"])
        limit: Optional limit on total files to process
        workers: Number of concurrent workers (default: 5)
    """
    global processed_count, celebrities_count
    processed_count = 0
    celebrities_count = 0
    
    if volumes is None:
        volumes = ["VOL00002"]  # Only VOL00002 has photos of people
    
    init_db()
    storage = DocumentStorage()
    rekognition = RekognitionProcessor()
    
    if not rekognition.enabled:
        logger.error("Rekognition not enabled! Check AWS credentials.")
        return 0
    
    temp_dir = Config.STORAGE_PATH / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Discover files
    async with DocumentCrawler() as crawler:
        all_files = await crawler.discover_files()
        logger.info(f"Discovered {len(all_files)} total files")
        
        # Filter to photo volumes
        photo_files = []
        for vol in volumes:
            vol_files = [f for f in all_files if vol in f['url']]
            logger.info(f"{vol}: {len(vol_files)} files")
            photo_files.extend(vol_files)
        
        if limit:
            photo_files = photo_files[:limit]
        
        logger.info(f"Processing {len(photo_files)} photo files with {workers} concurrent workers")
        
        # Create sync fetch function for threads
        def fetch_sync(url, path):
            import requests
            try:
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with open(path, 'wb') as f:
                        f.write(resp.content)
                    return True
            except Exception as e:
                logger.error(f"Download error: {e}")
            return False
        
        # Process files concurrently
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    process_single_file, 
                    file_info, 
                    temp_dir, 
                    storage, 
                    rekognition,
                    fetch_sync
                ): file_info 
                for file_info in photo_files
            }
            
            # Progress bar
            with tqdm(total=len(photo_files), desc=f"Processing ({workers} workers)") as pbar:
                for future in as_completed(futures):
                    file_info = futures[future]
                    try:
                        success, celebs = future.result()
                        if success and celebs > 0:
                            pbar.set_postfix({"celebs": celebrities_count})
                    except Exception as e:
                        logger.error(f"Future error for {file_info['filename']}: {e}")
                    pbar.update(1)
        
        logger.info(f"\n{'='*50}")
        logger.info(f"Processed {processed_count} files")
        logger.info(f"Found {celebrities_count} celebrity appearances")
        logger.info(f"{'='*50}")
        
        return processed_count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Process photo volumes with Rekognition (concurrent)")
    parser.add_argument("--volumes", nargs="+", default=["VOL00002"],
                        help="Volume names (default: VOL00002)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit total files to process")
    parser.add_argument("--workers", type=int, default=5,
                        help="Number of concurrent workers (default: 5)")
    args = parser.parse_args()
    
    result = asyncio.run(process_photo_volumes(args.volumes, args.limit, args.workers))
    print(f"\nDone! Processed {result} files")
