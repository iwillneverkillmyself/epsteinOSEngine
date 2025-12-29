#!/usr/bin/env python3
"""
Script to ingest files from Department of Justice Epstein page.

This script:
1. Crawls justice.gov/epstein for documents
2. Excludes "Epstein Files Transparency Act" files (already in images)
3. Downloads all other files
4. Processes them through AWS Textract for OCR
5. Indexes the extracted text for search

Usage:
    python scripts/ingest_doj_files.py [--preview] [--skip-existing]
    
    --preview: Only show what files would be downloaded, don't actually download
    --skip-existing: Skip files that are already in the database (default: True)
"""

import sys
import asyncio
import logging
from pathlib import Path
from typing import List, Dict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from database import init_db, get_db
from models import Document, ImagePage, OCRText
from ingestion.doj_crawler import DOJEpsteinCrawler
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


async def preview_files():
    """Preview what files would be downloaded."""
    logger.info("Previewing DOJ Epstein files...")
    
    async with DOJEpsteinCrawler() as crawler:
        files = await crawler.discover_files()
    
    if not files:
        logger.warning("No files discovered from DOJ website")
        return
    
    # Group by section
    sections = {}
    for file_info in files:
        section = file_info.get('section', 'Unknown')
        if section not in sections:
            sections[section] = []
        sections[section].append(file_info)
    
    print(f"\n{'='*80}")
    print(f"DOJ EPSTEIN FILES PREVIEW")
    print(f"{'='*80}\n")
    print(f"Total files discovered: {len(files)}")
    print(f"Note: Files from 'Epstein Files Transparency Act' are excluded\n")
    
    for section, section_files in sections.items():
        print(f"\n{section} ({len(section_files)} files)")
        print("-" * 80)
        for file_info in section_files:
            print(f"  • {file_info['filename']}")
            if file_info.get('description'):
                desc = file_info['description'][:100]
                print(f"    {desc}{'...' if len(file_info['description']) > 100 else ''}")
    
    print(f"\n{'='*80}\n")


async def ingest_files(skip_existing: bool = True):
    """
    Main ingestion function.
    
    Args:
        skip_existing: Skip files that are already in the database
    """
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    
    # Initialize processors
    storage = DocumentStorage()
    ocr_processor = OCRProcessor()
    text_processor = TextProcessor()
    indexer = SearchIndexer()
    
    temp_dir = Config.STORAGE_PATH / "doj_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Crawl and download files
    logger.info("=" * 80)
    logger.info("STEP 1: Crawling and downloading files from justice.gov/epstein")
    logger.info("=" * 80)
    
    async with DOJEpsteinCrawler() as crawler:
        files = await crawler.crawl_and_fetch_all(temp_dir)
    
    if not files:
        logger.warning("No files downloaded from DOJ website")
        return
    
    logger.info(f"\nSuccessfully downloaded {len(files)} files")
    
    # Step 2-7: Process each file
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2-7: Processing files (convert, OCR, index)")
    logger.info("=" * 80 + "\n")
    
    files_processed = 0
    files_skipped = 0
    files_failed = 0
    
    for file_info in tqdm(files, desc="Processing files"):
        try:
            # Store document
            doc_id, is_new = storage.store_document(file_info)
            
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
            
            # Process OCR using AWS Textract
            logger.info(f"  Running AWS Textract OCR...")
            pages_processed = ocr_processor.process_document(doc_id)
            logger.info(f"  Processed {pages_processed} pages with OCR")
            
            # Process text (normalize, detect entities)
            logger.info(f"  Extracting entities and normalizing text...")
            with get_db() as db:
                ocr_texts = db.query(OCRText).filter(
                    OCRText.document_id == doc_id
                ).all()
            
            for ocr_text in ocr_texts:
                text_processor.process_ocr_text(ocr_text.id)
            
            # Index for search
            logger.info(f"  Indexing for search...")
            indexed = indexer.index_document(doc_id)
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
    logger.info(f"Files downloaded:    {len(files)}")
    logger.info(f"Files processed:     {files_processed}")
    logger.info(f"Files skipped:       {files_skipped}")
    logger.info(f"Files failed:        {files_failed}")
    logger.info("=" * 80 + "\n")
    
    # Print info about accessing the files
    logger.info("Files are now available through the API:")
    logger.info("  • Search text:    POST /search")
    logger.info("  • Search files:   GET /search/files")
    logger.info("  • List images:    GET /images")
    logger.info("  • Get image:      GET /images/{page_id}")
    logger.info("  • Get file:       GET /files/{document_id}")
    logger.info("  • Stats:          GET /stats")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Ingest files from Department of Justice Epstein page"
    )
    parser.add_argument(
        '--preview',
        action='store_true',
        help='Preview files without downloading'
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
    
    args = parser.parse_args()
    
    if args.preview:
        await preview_files()
    else:
        await ingest_files(skip_existing=args.skip_existing)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nIngestion cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)



