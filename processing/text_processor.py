"""Main text processing pipeline."""
import logging
from database import get_db
from models import OCRText
from processing.normalizer import TextNormalizer
from processing.entity_detector import EntityDetector

logger = logging.getLogger(__name__)


class TextProcessor:
    """Main text processing pipeline."""
    
    def __init__(self):
        self.normalizer = TextNormalizer()
        self.entity_detector = EntityDetector()
    
    def process_ocr_text(self, ocr_text_id: str) -> bool:
        """
        Process OCR text: normalize and detect entities.
        
        Returns:
            True if successful
        """
        with get_db() as db:
            ocr_text = db.query(OCRText).filter(OCRText.id == ocr_text_id).first()
            if not ocr_text:
                logger.error(f"OCR text {ocr_text_id} not found")
                return False
            
            # Normalize text
            normalized = self.normalizer.normalize(ocr_text.raw_text)
            ocr_text.normalized_text = normalized
            db.commit()
            
            # Detect entities
            entities = self.entity_detector.extract_entities_from_word_boxes(
                ocr_text.word_boxes or [],
                normalized
            )
            
            # Save entities
            if entities:
                self.entity_detector.save_entities(ocr_text_id, entities)
            
            logger.info(f"Processed OCR text {ocr_text_id}: "
                       f"{len(normalized)} chars, {len(entities)} entities")
            
            return True



