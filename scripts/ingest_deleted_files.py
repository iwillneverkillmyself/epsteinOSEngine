#!/usr/bin/env python3
"""
Script to ingest deleted/removed files from local directory.

This script:
1. Scans a local directory for PDFs and images
2. Stores originals + page images (and uploads to S3 if configured)
3. Marks them with collection="deleted" in the database

Note: Deleted files are intended to be storage-only by default (no OCR/summaries/tags).
You can opt into OCR with --with-ocr if you want text search later.

Usage:
    python scripts/ingest_deleted_files.py [--dir PATH] [--skip-existing] [--with-ocr]
    
    --dir: Directory containing files to ingest (default: ~/Downloads/epstiendeleted:removed files)
    --skip-existing: Skip files that are already in the database (default: True)
    --with-ocr: Run OCR + indexing after storing (default: False)
"""

import sys
import logging
from pathlib import Path
from typing import List, Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from database import init_db, get_db
from models import Document, ImagePage, OCRText
from ingestion.storage import DocumentStorage
from ingestion.pdf_converter import pdf_to_images, is_pdf
from ocr.processor import OCRProcessor
from processing.text_processor import TextProcessor
from search.indexer import SearchIndexer
from PIL import Image
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def discover_local_files(directory: Path) -> List[Dict]:
    """
    Discover PDF and image files in a local directory.
    
    Args:
        directory: Path to directory containing files
        
    Returns:
        List of file info dicts
    """
    if not directory.exists():
        logger.error(f"Directory does not exist: {directory}")
        return []
    
    files = []
    supported_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif'}
    
    for file_path in directory.iterdir():
        if not file_path.is_file():
            continue
        
        ext = file_path.suffix.lower()
        if ext not in supported_extensions:
            logger.debug(f"Skipping unsupported file: {file_path.name}")
            continue
        
        # Determine file type
        if ext == '.pdf':
            file_type = 'pdf'
        elif ext in {'.jpg', '.jpeg'}:
            file_type = 'jpg'
        elif ext == '.png':
            file_type = 'png'
        else:
            file_type = ext.lstrip('.')
        
        file_info = {
            'url': f"file://{file_path}",
            'filename': file_path.name,
            'file_type': file_type,
            'local_path': str(file_path),
            'file_size': file_path.stat().st_size,
            'source': 'local_deleted_files'
        }
        
        files.append(file_info)
    
    logger.info(f"Discovered {len(files)} files in {directory}")
    return files


def ingest_deleted_files(directory: Path, skip_existing: bool = True, with_ocr: bool = False):
    """
    Main ingestion function for deleted files.
    
    Args:
        directory: Directory containing files to ingest
        skip_existing: Skip files that are already in the database
    """
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    
    # Initialize processors
    storage = DocumentStorage()
    ocr_processor = OCRProcessor() if with_ocr else None
    text_processor = TextProcessor() if with_ocr else None
    indexer = SearchIndexer() if with_ocr else None
    
    temp_dir = Config.STORAGE_PATH / "deleted_files_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Discover local files
    logger.info("=" * 80)
    logger.info("STEP 1: Discovering files in local directory")
    logger.info("=" * 80)
    
    files = discover_local_files(directory)
    
    if not files:
        logger.warning(f"No files found in {directory}")
        return
    
    logger.info(f"\nFound {len(files)} files to process")
    
    # Step 2-7: Process each file
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2-7: Processing files (convert, OCR, index)")
    logger.info("=" * 80 + "\n")
    
    files_processed = 0
    files_skipped = 0
    files_failed = 0
    
    for file_info in tqdm(files, desc="Processing files"):
        try:
            # Store document with collection="deleted"
            doc_id, is_new = storage.store_document(file_info, collection="deleted")
            
            if not is_new and skip_existing:
                logger.debug(f"Document {doc_id} already exists, skipping")
                files_skipped += 1
                continue
            
            logger.info(f"\nProcessing: {file_info['filename']}")
            
            # Convert PDFs to images if needed
            file_path = Path(file_info['local_path'])
            image_paths = []
            
            if is_pdf(file_path):
                logger.info(f"  Converting PDF to images...")
                images_dir = temp_dir / f"{doc_id}_images"
                image_paths = pdf_to_images(file_path, images_dir)
                logger.info(f"  Generated {len(image_paths)} page images")
            else:
                # Single image file
                image_paths = [file_path]
            
            # Store image pages
            logger.info(f"  Storing {len(image_paths)} image pages...")
            for page_num, image_path in enumerate(image_paths, start=1):
                img = Image.open(image_path)
                width, height = img.size
                
                page_id = storage.store_image_page(
                    doc_id, page_num, image_path, width, height
                )
            
            if with_ocr:
                # Process OCR using configured OCR engine
                logger.info(f"  Running OCR...")
                pages_processed = ocr_processor.process_document(doc_id)  # type: ignore[union-attr]
                logger.info(f"  Processed {pages_processed} pages with OCR")
                
                # Process text (normalize, detect entities)
                logger.info(f"  Extracting entities and normalizing text...")
                with get_db() as db:
                    ocr_texts = db.query(OCRText).filter(
                        OCRText.document_id == doc_id
                    ).all()
                
                for ocr_text in ocr_texts:
                    text_processor.process_ocr_text(ocr_text.id)  # type: ignore[union-attr]
                
                # Index for search
                logger.info(f"  Indexing for search...")
                indexed = indexer.index_document(doc_id)  # type: ignore[union-attr]
                logger.info(f"  Indexed {indexed} OCR texts")
            
            files_processed += 1
            logger.info(f"  ✓ Successfully processed {file_info['filename']}")
            
        except Exception as e:
            logger.error(f"  ✗ Error processing {file_info.get('filename', 'unknown')}: {e}")
            logger.exception(e)
            files_failed += 1
            continue
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("INGESTION COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Files discovered:    {len(files)}")
    logger.info(f"Files processed:     {files_processed}")
    logger.info(f"Files skipped:       {files_skipped}")
    logger.info(f"Files failed:        {files_failed}")
    logger.info("=" * 80 + "\n")
    
    # Print info about accessing the files
    logger.info("Files are now available through the API:")
    logger.info("  • List deleted files: GET /files/deleted")
    logger.info("  • Search text:        POST /search")
    logger.info("  • Search files:       GET /search/files?collection=deleted")
    logger.info("  • List images:        GET /images")
    logger.info("  • Get image:          GET /images/{page_id}")
    logger.info("  • Get file:           GET /files/{document_id}")
    logger.info("  • Stats:              GET /stats")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Ingest deleted/removed files from local directory"
    )
    parser.add_argument(
        '--dir',
        type=str,
        default=str(Path.home() / "Downloads" / "epstiendeleted:removed files"),
        help='Directory containing files to ingest'
    )
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        default=True,
        help='Skip files already in database (default: True)'
    )
    parser.add_argument(
        '--no-skip-existing',
        action='store_false',
        dest='skip_existing',
        help='Process all files even if already in database'
    )
    parser.add_argument(
        '--with-ocr',
        action='store_true',
        default=False,
        help='Run OCR + indexing after storing (default: False)'
    )
    
    args = parser.parse_args()
    
    directory = Path(args.dir)
    ingest_deleted_files(directory, skip_existing=args.skip_existing, with_ocr=args.with_ocr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nIngestion cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)

