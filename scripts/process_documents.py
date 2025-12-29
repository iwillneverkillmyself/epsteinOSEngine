"""
Process document volumes (VOL00003-VOL00007) with Textract OCR.
These volumes contain documents that need text extraction.
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
import logging
from tqdm import tqdm
from PIL import Image

from config import Config
from database import init_db, SessionLocal
from models import Document, ImagePage, OCRText, SearchIndex
from ingestion.crawler import DocumentCrawler
from ingestion.pdf_converter import pdf_to_images, is_pdf
from ingestion.storage import DocumentStorage
from ocr.engine import get_ocr_engine
from processing.text_processor import TextProcessor
from search.indexer import SearchIndexer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def process_document_volumes(volumes: list = None, limit: int = None):
    """
    Process document volumes with Textract OCR.
    
    Args:
        volumes: List of volume prefixes (default: VOL00003 through VOL00007)
        limit: Optional limit on total files to process
    """
    if volumes is None:
        # Document volumes (not photo volumes)
        volumes = ["VOL00003", "VOL00004", "VOL00005", "VOL00006", "VOL00007"]
    
    init_db()
    db = SessionLocal()
    storage = DocumentStorage()
    ocr_engine = get_ocr_engine()
    text_processor = TextProcessor()
    indexer = SearchIndexer()
    
    temp_dir = Config.STORAGE_PATH / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Discover files
    async with DocumentCrawler() as crawler:
        all_files = await crawler.discover_files()
        logger.info(f"Discovered {len(all_files)} total files")
        
        # Filter to document volumes
        doc_files = []
        for vol in volumes:
            vol_files = [f for f in all_files if vol in f['url']]
            logger.info(f"{vol}: {len(vol_files)} files")
            doc_files.extend(vol_files)
        
        if limit:
            doc_files = doc_files[:limit]
        
        logger.info(f"Processing {len(doc_files)} document files for OCR")
        
        processed = 0
        ocr_extracted = 0
        
        for file_info in tqdm(doc_files, desc="Processing documents"):
            try:
                # Check if already in database with OCR
                existing = db.query(Document).filter(
                    Document.file_name == file_info['filename']
                ).first()
                
                if existing:
                    # Check if OCR was already done
                    existing_ocr = db.query(OCRText).filter(
                        OCRText.document_id == existing.id
                    ).first()
                    if existing_ocr:
                        logger.debug(f"Already processed: {file_info['filename']}")
                        continue
                    doc_id = existing.id
                    file_path = Path(file_info.get('local_path', temp_dir / file_info['filename']))
                    if not file_path.exists():
                        # Re-download
                        if not await crawler.fetch_file(file_info['url'], file_path):
                            continue
                        file_info['local_path'] = str(file_path)
                else:
                    # Download and store document
                    save_path = temp_dir / file_info['filename']
                    if not await crawler.fetch_file(file_info['url'], save_path):
                        continue
                    
                    file_info['local_path'] = str(save_path)
                    file_info['file_size'] = save_path.stat().st_size
                    doc_id, _ = storage.store_document(file_info)
                    file_path = save_path
                
                # Convert PDF to images if needed
                if is_pdf(file_path):
                    images_dir = temp_dir / f"{doc_id}_images"
                    image_paths = pdf_to_images(file_path, images_dir)
                else:
                    image_paths = [file_path]
                
                # Store image pages and run OCR
                for page_num, image_path in enumerate(image_paths, start=1):
                    try:
                        img = Image.open(image_path)
                        width, height = img.size
                        
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
                                ocr_processed=False
                            )
                            db.add(page)
                            db.commit()
                            db.refresh(page)
                        
                        # Run OCR if not already done
                        if not page.ocr_processed:
                            ocr_result = ocr_engine.extract_text(Path(image_path))
                            
                            if ocr_result and ocr_result.get('raw_text'):
                                raw_text = ocr_result['raw_text']
                                
                                # Normalize text
                                normalized = text_processor.normalize_text(raw_text) if hasattr(text_processor, 'normalize_text') else raw_text
                                
                                # Store OCR result
                                ocr_text = OCRText(
                                    id=f"{page.id}_ocr",
                                    document_id=doc_id,
                                    image_page_id=page.id,
                                    raw_text=raw_text,
                                    normalized_text=normalized,
                                    confidence=ocr_result.get('confidence', 0),
                                    word_boxes=ocr_result.get('word_boxes', [])
                                )
                                db.add(ocr_text)
                                
                                # Process text for entities
                                entities = text_processor.extract_entities(raw_text)
                                
                                # Index for search
                                indexer.index_document(
                                    doc_id=doc_id,
                                    page_id=page.id,
                                    text=raw_text,
                                    entities=entities
                                )
                                
                                ocr_extracted += 1
                                logger.info(f"OCR: {file_info['filename']} page {page_num}: {len(raw_text)} chars")
                            
                            # Mark page as processed
                            page.ocr_processed = True
                            db.commit()
                        
                    except Exception as e:
                        logger.error(f"Error processing page {page_num} of {file_info['filename']}: {e}")
                        db.rollback()
                
                processed += 1
                
            except Exception as e:
                logger.error(f"Error processing {file_info['filename']}: {e}")
                db.rollback()
        
        db.close()
        logger.info(f"\n{'='*50}")
        logger.info(f"Processed {processed} files")
        logger.info(f"OCR extracted from {ocr_extracted} pages")
        logger.info(f"{'='*50}")
        
        return processed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Process document volumes with Textract OCR")
    parser.add_argument("--volumes", nargs="+", default=None,
                        help="Volume names (default: VOL00003 VOL00004 VOL00005 VOL00006 VOL00007)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit total files to process")
    args = parser.parse_args()
    
    result = asyncio.run(process_document_volumes(args.volumes, args.limit))
    print(f"\nDone! Processed {result} files")

