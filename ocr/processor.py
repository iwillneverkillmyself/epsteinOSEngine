"""OCR processing pipeline."""
import uuid
from pathlib import Path
from typing import Dict, Optional
import logging
from datetime import datetime
from database import get_db
from models import OCRText, ImagePage
from ocr.engine import get_ocr_engine, OCREngine
from config import Config

logger = logging.getLogger(__name__)


class OCRProcessor:
    """Processes images through OCR pipeline."""
    
    def __init__(self, ocr_engine: Optional[OCREngine] = None):
        # If provided, use that engine (e.g. force Textract for specific pipelines).
        # Otherwise, use the globally configured engine.
        self.ocr_engine = ocr_engine or get_ocr_engine()
    
    def process_image_page(self, page_id: str) -> Optional[str]:
        """
        Process an image page through OCR.
        
        Args:
            page_id: Image page ID
            
        Returns:
            OCR text ID if successful, None otherwise
        """
        with get_db() as db:
            # Get image page
            page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
            if not page:
                logger.error(f"Image page {page_id} not found")
                return None
            
            # Check if already processed
            if page.ocr_processed:
                logger.debug(f"Page {page_id} already processed")
                existing = db.query(OCRText).filter(
                    OCRText.image_page_id == page_id
                ).first()
                return existing.id if existing else None
            
            # Perform OCR
            image_path = Path(page.image_path)
            if not image_path.exists():
                logger.error(f"Image file not found: {image_path}")
                return None
            
            logger.info(f"Processing OCR for {page_id}")
            ocr_result = self.ocr_engine.extract_text(image_path)
            
            if not ocr_result['text']:
                logger.warning(f"No text extracted from {page_id}")
                page.ocr_processed = True
                page.ocr_processed_at = datetime.utcnow()
                db.commit()
                return None
            
            # Calculate overall bounding box
            if ocr_result['word_boxes']:
                min_x = min(box['x'] for box in ocr_result['word_boxes'])
                min_y = min(box['y'] for box in ocr_result['word_boxes'])
                max_x = max(box['x'] + box['width'] for box in ocr_result['word_boxes'])
                max_y = max(box['y'] + box['height'] for box in ocr_result['word_boxes'])
            else:
                min_x = min_y = max_x = max_y = 0.0
            
            # Create OCR text record
            ocr_id = str(uuid.uuid4())
            ocr_text = OCRText(
                id=ocr_id,
                image_page_id=page_id,
                document_id=page.document_id,
                page_number=page.page_number,
                raw_text=ocr_result['text'],
                normalized_text=ocr_result['text'],  # Will be normalized later
                word_boxes=ocr_result['word_boxes'],
                bbox_x=min_x,
                bbox_y=min_y,
                bbox_width=max_x - min_x,
                bbox_height=max_y - min_y,
                confidence=ocr_result['confidence']
            )
            
            db.add(ocr_text)
            
            # Mark page as processed
            page.ocr_processed = True
            page.ocr_processed_at = datetime.utcnow()
            
            db.commit()
            
            logger.info(f"OCR completed for {page_id}: {len(ocr_result['text'])} chars, "
                       f"confidence: {ocr_result['confidence']:.2f}")
            
            return ocr_id
    
    def process_document(self, document_id: str) -> int:
        """
        Process all pages of a document.
        
        Returns:
            Number of pages processed
        """
        # IMPORTANT: don't return ORM instances outside the session context
        # (can cause DetachedInstanceError when accessing attributes later).
        with get_db() as db:
            page_ids = [
                row[0]
                for row in db.query(ImagePage.id)
                .filter(ImagePage.document_id == document_id)
                .all()
            ]

        processed = 0
        for page_id in page_ids:
            if self.process_image_page(page_id):
                processed += 1

        return processed

