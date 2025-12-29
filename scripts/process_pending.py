"""Process pending pages that haven't been OCR'd."""
import sys
from pathlib import Path
import logging

# Allow running as a script from /app/scripts without requiring PYTHONPATH=/app
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr.processor import OCRProcessor
from processing.text_processor import TextProcessor
from search.indexer import SearchIndexer
from database import get_db, init_db
from models import ImagePage, OCRText
from config import Config

logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)


def main():
    """Process all pending pages."""
    init_db()
    
    ocr_processor = OCRProcessor()
    text_processor = TextProcessor()
    indexer = SearchIndexer()
    
    with get_db() as db:
        pending_page_ids = [
            row[0]
            for row in db.query(ImagePage.id).filter(ImagePage.ocr_processed == False).all()
        ]
    
    logger.info(f"Found {len(pending_page_ids)} pending pages")
    
    for page_id in pending_page_ids:
        try:
            logger.info(f"Processing page {page_id}")
            ocr_id = ocr_processor.process_image_page(page_id)
            if ocr_id:
                text_processor.process_ocr_text(ocr_id)
                indexer.index_ocr_text(ocr_id)
                logger.info(f"Completed processing page {page_id}")
        except Exception as e:
            logger.error(f"Error processing page {page_id}: {e}")
    
    logger.info("Processing complete")


if __name__ == "__main__":
    main()

