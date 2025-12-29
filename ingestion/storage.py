"""Storage management for documents and images."""
import hashlib
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple
from datetime import datetime
import shutil
import logging
from config import Config
from database import get_db
from models import Document, ImagePage

logger = logging.getLogger(__name__)

# S3 upload support
try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logger.warning("boto3 not available - S3 uploads will be skipped")


class DocumentStorage:
    """Manages storage of documents and images."""
    
    def __init__(self):
        self.images_dir = Config.IMAGES_PATH
        self.storage_dir = Config.STORAGE_PATH
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_document_id(self, source_url: str, filename: str) -> str:
        """Generate a stable document ID."""
        content = f"{source_url}:{filename}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def store_document(self, file_info: Dict) -> Tuple[str, bool]:
        """
        Store a document and create database entry.
        
        Args:
            file_info: Dict with url, filename, file_type, local_path, file_size
            
        Returns:
            Tuple of (Document ID, is_new: bool)
        """
        doc_id = self.generate_document_id(file_info['url'], file_info['filename'])
        
        # Check if document already exists
        with get_db() as db:
            existing = db.query(Document).filter(Document.id == doc_id).first()
            if existing:
                logger.debug(f"Document {doc_id} already exists, skipping storage")
                return doc_id, False
            
            # Copy file to storage
            source_path = Path(file_info['local_path'])
            stored_path = self.storage_dir / f"{doc_id}{source_path.suffix}"
            shutil.copy2(source_path, stored_path)
            
            # Upload to S3 if configured (for ECS/Fargate persistence)
            if Config.S3_BUCKET and BOTO3_AVAILABLE:
                try:
                    s3_client = boto3.client('s3', region_name=Config.S3_REGION or 'us-east-1')
                    s3_key = f"{Config.S3_FILES_PREFIX.rstrip('/')}/{doc_id}{source_path.suffix}"
                    s3_client.upload_file(str(stored_path), Config.S3_BUCKET, s3_key)
                    logger.info(f"Uploaded document {doc_id} to S3: s3://{Config.S3_BUCKET}/{s3_key}")
                except Exception as e:
                    logger.warning(f"Failed to upload document {doc_id} to S3: {e}")
            
            # Create document record
            document = Document(
                id=doc_id,
                source_url=file_info['url'],
                file_name=file_info['filename'],
                file_type=file_info.get('file_type', 'unknown'),
                file_size=file_info.get('file_size', 0),
                doc_metadata=file_info
            )
            
            db.add(document)
            db.commit()
            
            logger.info(f"Stored document {doc_id}: {file_info['filename']}")
            return doc_id, True
    
    def store_image_page(self, document_id: str, page_number: int, 
                        image_path: Path, width: int, height: int) -> str:
        """
        Store an image page and create database entry.
        
        Returns:
            Image page ID
        """
        page_id = f"{document_id}_page_{page_number:04d}"
        
        # Check if page already exists
        with get_db() as db:
            existing = db.query(ImagePage).filter(ImagePage.id == page_id).first()
            if existing:
                logger.debug(f"Image page {page_id} already exists, skipping")
                return page_id
        
        # Copy image to images directory
        stored_image_path = self.images_dir / f"{page_id}.png"
        shutil.copy2(image_path, stored_image_path)
        
        # Upload to S3 if configured (for ECS/Fargate persistence)
        if Config.S3_BUCKET and BOTO3_AVAILABLE:
            try:
                s3_client = boto3.client('s3', region_name=Config.S3_REGION or 'us-east-1')
                s3_key = f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}.png"
                s3_client.upload_file(str(stored_image_path), Config.S3_BUCKET, s3_key)
                logger.debug(f"Uploaded image page {page_id} to S3: s3://{Config.S3_BUCKET}/{s3_key}")
            except Exception as e:
                logger.warning(f"Failed to upload image page {page_id} to S3: {e}")
        
        # Create image page record
        with get_db() as db:
            image_page = ImagePage(
                id=page_id,
                document_id=document_id,
                page_number=page_number,
                image_path=str(stored_image_path),
                width=width,
                height=height
            )
            
            db.add(image_page)
            db.commit()
            
            logger.debug(f"Stored image page {page_id}")
            return page_id
    
    def get_image_path(self, page_id: str) -> Optional[Path]:
        """Get the file path for an image page."""
        with get_db() as db:
            page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
            if page:
                return Path(page.image_path)
        return None

