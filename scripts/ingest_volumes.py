"""Ingest specific volumes only - useful for targeting photo-rich volumes."""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
import logging
from config import Config
from database import init_db, SessionLocal
from models import Document
from ingestion.crawler import DocumentCrawler
from pipeline import IngestionPipeline

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def ingest_volumes(volumes: list, limit_per_volume: int = None):
    """
    Ingest only files from specified volumes.
    
    Args:
        volumes: List of volume prefixes e.g. ["VOL00002", "VOL00003"]
        limit_per_volume: Optional limit per volume (for testing)
    """
    init_db()
    db = SessionLocal()
    
    # Discover all files
    async with DocumentCrawler() as crawler:
        all_files = await crawler.discover_files()
        logger.info(f"Discovered {len(all_files)} total files")
        
        # Filter to selected volumes
        filtered = []
        for vol in volumes:
            vol_files = [f for f in all_files if vol in f['url']]
            logger.info(f"{vol}: {len(vol_files)} files")
            if limit_per_volume:
                vol_files = vol_files[:limit_per_volume]
            filtered.extend(vol_files)
        
        logger.info(f"Will process {len(filtered)} files from {volumes}")
        
        # Download files
        temp_dir = Config.STORAGE_PATH / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        fetched = []
        for file_info in filtered:
            # Check if already in database
            existing = db.query(Document).filter(
                Document.file_name == file_info['filename']
            ).first()
            if existing:
                logger.debug(f"Already processed: {file_info['filename']}")
                continue
            
            save_path = temp_dir / file_info['filename']
            if await crawler.fetch_file(file_info['url'], save_path):
                file_info['local_path'] = str(save_path)
                file_info['file_size'] = save_path.stat().st_size
                fetched.append(file_info)
        
        logger.info(f"Fetched {len(fetched)} new files")
    
    db.close()
    
    # Now process the fetched files
    if fetched:
        pipeline = IngestionPipeline()
        for file_info in fetched:
            try:
                doc_id, is_new = pipeline.storage.store_document(file_info)
                if is_new:
                    logger.info(f"Processing: {file_info['filename']}")
                    # The pipeline handles the rest...
            except Exception as e:
                logger.error(f"Error processing {file_info['filename']}: {e}")
    
    return len(fetched)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ingest specific volumes")
    parser.add_argument("volumes", nargs="+", help="Volume names e.g. VOL00002 VOL00003")
    parser.add_argument("--limit", type=int, default=None, help="Limit per volume")
    args = parser.parse_args()
    
    result = asyncio.run(ingest_volumes(args.volumes, args.limit))
    logger.info(f"Done! Processed {result} files")




