"""Entity detection (names, emails, phones, dates, keywords)."""
import re
import uuid
from typing import List, Dict, Optional
from datetime import datetime
import logging
from dateutil import parser as date_parser
from config import Config
from database import get_db
from models import Entity, OCRText

logger = logging.getLogger(__name__)


class EntityDetector:
    """Detects entities in OCR text."""
    
    def __init__(self):
        # Email pattern
        self.email_pattern = re.compile(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        )
        
        # Phone patterns (US format)
        self.phone_patterns = [
            re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'),  # 123-456-7890
            re.compile(r'\b\(\d{3}\)\s?\d{3}[-.]?\d{4}\b'),  # (123) 456-7890
            re.compile(r'\b\d{10}\b'),  # 1234567890
        ]
        
        # Date patterns
        self.date_patterns = [
            re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b'),  # MM/DD/YYYY
            re.compile(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b', re.IGNORECASE),
            re.compile(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b', re.IGNORECASE),
        ]
        
        # Name detection (simple heuristic - capitalized words)
        # This is basic; could be enhanced with NER models
        self.name_pattern = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b')
    
    def detect_emails(self, text: str) -> List[Dict]:
        """Detect email addresses."""
        if not Config.ENABLE_EMAIL_DETECTION:
            return []
        
        emails = []
        for match in self.email_pattern.finditer(text):
            emails.append({
                'type': 'email',
                'value': match.group(),
                'normalized_value': match.group().lower(),
                'start': match.start(),
                'end': match.end()
            })
        return emails
    
    def detect_phones(self, text: str) -> List[Dict]:
        """Detect phone numbers."""
        if not Config.ENABLE_PHONE_DETECTION:
            return []
        
        phones = []
        for pattern in self.phone_patterns:
            for match in pattern.finditer(text):
                phones.append({
                    'type': 'phone',
                    'value': match.group(),
                    'normalized_value': re.sub(r'[^\d]', '', match.group()),
                    'start': match.start(),
                    'end': match.end()
                })
        return phones
    
    def detect_dates(self, text: str) -> List[Dict]:
        """Detect dates."""
        if not Config.ENABLE_DATE_DETECTION:
            return []
        
        dates = []
        for pattern in self.date_patterns:
            for match in pattern.finditer(text):
                date_str = match.group()
                try:
                    # Try to parse and normalize
                    parsed_date = date_parser.parse(date_str, fuzzy=True)
                    normalized = parsed_date.strftime('%Y-%m-%d')
                except:
                    normalized = date_str
                
                dates.append({
                    'type': 'date',
                    'value': date_str,
                    'normalized_value': normalized,
                    'start': match.start(),
                    'end': match.end()
                })
        return dates
    
    def detect_names(self, text: str) -> List[Dict]:
        """Detect potential names (capitalized words)."""
        if not Config.ENABLE_NAME_DETECTION:
            return []
        
        names = []
        # Simple heuristic: sequences of capitalized words
        for match in self.name_pattern.finditer(text):
            potential_name = match.group()
            # Filter out common false positives
            if not self._is_false_positive_name(potential_name):
                names.append({
                    'type': 'name',
                    'value': potential_name,
                    'normalized_value': potential_name.title(),
                    'start': match.start(),
                    'end': match.end()
                })
        return names
    
    def _is_false_positive_name(self, text: str) -> bool:
        """Filter out common false positives for names."""
        false_positives = {
            'The', 'This', 'That', 'These', 'Those',
            'Page', 'Date', 'Time', 'Subject', 'From', 'To',
            'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'
        }
        return text in false_positives
    
    def detect_all(self, text: str) -> List[Dict]:
        """Detect all entity types."""
        entities = []
        entities.extend(self.detect_emails(text))
        entities.extend(self.detect_phones(text))
        entities.extend(self.detect_dates(text))
        entities.extend(self.detect_names(text))
        return entities
    
    def extract_entities_from_word_boxes(self, word_boxes: List[Dict], 
                                        text: str) -> List[Dict]:
        """
        Extract entities and map them to word box positions.
        
        Returns entities with bounding box information.
        """
        entities = self.detect_all(text)
        
        # Map entities to word boxes
        enriched_entities = []
        for entity in entities:
            # Find word boxes that overlap with entity position
            start_pos = entity['start']
            end_pos = entity['end']
            
            # Find overlapping word boxes
            overlapping_boxes = []
            current_pos = 0
            
            for box in word_boxes:
                box_text = box.get('text', '')
                box_start = current_pos
                box_end = current_pos + len(box_text)
                
                # Check if this box overlaps with entity
                if not (box_end < start_pos or box_start > end_pos):
                    overlapping_boxes.append(box)
                
                current_pos = box_end + 1  # +1 for space
            
            # Calculate combined bounding box
            if overlapping_boxes:
                min_x = min(box['x'] for box in overlapping_boxes)
                min_y = min(box['y'] for box in overlapping_boxes)
                max_x = max(box['x'] + box['width'] for box in overlapping_boxes)
                max_y = max(box['y'] + box['height'] for box in overlapping_boxes)
                
                enriched_entities.append({
                    **entity,
                    'bbox_x': min_x,
                    'bbox_y': min_y,
                    'bbox_width': max_x - min_x,
                    'bbox_height': max_y - min_y
                })
        
        return enriched_entities
    
    def save_entities(self, ocr_text_id: str, entities: List[Dict]) -> int:
        """Save detected entities to database."""
        if not entities:
            return 0
        
        with get_db() as db:
            # Get OCR text info
            ocr_text = db.query(OCRText).filter(OCRText.id == ocr_text_id).first()
            if not ocr_text:
                logger.error(f"OCR text {ocr_text_id} not found")
                return 0
            
            saved = 0
            for entity_data in entities:
                entity_id = str(uuid.uuid4())
                entity = Entity(
                    id=entity_id,
                    ocr_text_id=ocr_text_id,
                    document_id=ocr_text.document_id,
                    page_number=ocr_text.page_number,
                    entity_type=entity_data['type'],
                    entity_value=entity_data['value'],
                    normalized_value=entity_data.get('normalized_value', entity_data['value']),
                    bbox_x=entity_data.get('bbox_x', 0.0),
                    bbox_y=entity_data.get('bbox_y', 0.0),
                    bbox_width=entity_data.get('bbox_width', 0.0),
                    bbox_height=entity_data.get('bbox_height', 0.0),
                    confidence=1.0  # Rule-based detection
                )
                db.add(entity)
                saved += 1
            
            db.commit()
            logger.info(f"Saved {saved} entities for OCR text {ocr_text_id}")
            return saved




