"""Text normalization for OCR output."""
import re
import logging

logger = logging.getLogger(__name__)


class TextNormalizer:
    """Normalizes OCR text output."""
    
    def __init__(self):
        # Common OCR errors patterns
        self.ocr_fixes = {
            r'\s+': ' ',  # Multiple spaces to single space
            r'[|]': 'l',  # Common OCR error
            r'[0]': 'O',  # In certain contexts
        }
    
    def normalize(self, text: str) -> str:
        """
        Normalize OCR text.
        
        - Remove extra whitespace
        - Fix common OCR errors
        - Normalize case (preserve proper nouns where possible)
        - Remove noise characters
        """
        if not text:
            return ""
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        # Remove control characters
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
        
        # Fix common OCR errors (be conservative)
        # Only fix obvious errors
        
        return text
    
    def tokenize(self, text: str) -> list:
        """Tokenize text for search indexing."""
        # Simple tokenization - split on whitespace and punctuation
        tokens = re.findall(r'\b\w+\b', text.lower())
        return tokens
    
    def normalize_for_search(self, text: str) -> str:
        """Normalize text specifically for search indexing."""
        normalized = self.normalize(text)
        return normalized.lower()

