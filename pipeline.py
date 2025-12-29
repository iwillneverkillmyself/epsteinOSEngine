"""Main ingestion and processing pipeline."""
import asyncio
import logging
from pathlib import Path
from typing import List
from tqdm import tqdm
from PIL import Image

from config import Config
from database import init_db, get_db
from models import Document, ImagePage
from ingestion.crawler import crawl_and_fetch_all
from ingestion.pdf_converter import pdf_to_images, is_pdf
from ingestion.storage import DocumentStorage
from ocr.processor import OCRProcessor
from processing.text_processor import TextProcessor
from search.indexer import SearchIndexer
from models import OCRText

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Main pipeline for ingesting and processing documents."""
    
    def __init__(self):
        self.storage = DocumentStorage()
        self.ocr_processor = OCRProcessor()
        self.text_processor = TextProcessor()
        self.indexer = SearchIndexer()
    
    async def ingest_all(self, temp_dir: Path = None) -> int:
        """
        Main ingestion pipeline: crawl, fetch, convert, store, OCR, process, index.
        
        Returns:
            Number of documents processed
        """
        if temp_dir is None:
            temp_dir = Config.STORAGE_PATH / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info("Starting ingestion pipeline...")
        
        # Step 1: Crawl and fetch files
        logger.info("Step 1: Crawling and fetching files...")
        fetched_files = await crawl_and_fetch_all(temp_dir)
        logger.info(f"Fetched {len(fetched_files)} files")
        
        processed_docs = 0
        
        for file_info in tqdm(fetched_files, desc="Processing files"):
            try:
                # Step 2: Store document (returns tuple: doc_id, is_new)
                doc_id, is_new = self.storage.store_document(file_info)
                
                # Skip already-processed documents
                if not is_new:
                    logger.debug(f"Document {doc_id} already processed, skipping")
                    continue
                
                # Step 3: Convert PDFs to images if needed
                file_path = Path(file_info['local_path'])
                image_paths = []
                
                if is_pdf(file_path):
                    logger.info(f"Converting PDF: {file_path.name}")
                    images_dir = temp_dir / f"{doc_id}_images"
                    image_paths = pdf_to_images(file_path, images_dir)
                else:
                    # Single image file
                    image_paths = [file_path]
                
                # Step 4: Store image pages
                for page_num, image_path in enumerate(image_paths, start=1):
                    img = Image.open(image_path)
                    width, height = img.size
                    
                    page_id = self.storage.store_image_page(
                        doc_id, page_num, image_path, width, height
                    )
                
                # Step 5: Process OCR for all pages
                logger.info(f"Processing OCR for document {doc_id}")
                pages_processed = self.ocr_processor.process_document(doc_id)
                
                # Step 6: Process text (normalize, detect entities)
                with get_db() as db:
                    ocr_texts = db.query(OCRText).filter(
                        OCRText.document_id == doc_id
                    ).all()
                
                for ocr_text in ocr_texts:
                    self.text_processor.process_ocr_text(ocr_text.id)
                
                # Step 7: Index for search
                indexed = self.indexer.index_document(doc_id)
                logger.info(f"Indexed {indexed} OCR texts for document {doc_id}")
                
                processed_docs += 1
                
            except Exception as e:
                logger.error(f"Error processing file {file_info.get('filename', 'unknown')}: {e}")
                continue
        
        logger.info(f"Ingestion complete: {processed_docs} documents processed")
        return processed_docs
    
    def process_pending_pages(self):
        """Process any pages that haven't been OCR'd yet."""
        # IMPORTANT: don't return ORM objects outside the session (can cause DetachedInstanceError).
        # Fetch only the IDs we need.
        with get_db() as db:
            pending_page_ids = [
                row[0]
                for row in db.query(ImagePage.id).filter(ImagePage.ocr_processed == False).all()
            ]
        
        logger.info(f"Processing {len(pending_page_ids)} pending pages...")
        
        for page_id in tqdm(pending_page_ids, desc="OCR processing"):
            try:
                ocr_id = self.ocr_processor.process_image_page(page_id)
                if ocr_id:
                    self.text_processor.process_ocr_text(ocr_id)
                    self.indexer.index_ocr_text(ocr_id)
            except Exception as e:
                logger.error(f"Error processing page {page_id}: {e}")


async def main():
    """Main entry point."""
    # Initialize database
    init_db()
    
    # Run pipeline
    pipeline = IngestionPipeline()
    await pipeline.ingest_all()
    
    # Process any remaining pending pages
    pipeline.process_pending_pages()


if __name__ == "__main__":
    asyncio.run(main())

