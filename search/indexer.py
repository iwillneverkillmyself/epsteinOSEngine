"""Indexing system for search."""
import uuid
import logging
from typing import List, Dict
from database import get_db
from models import OCRText, SearchIndex
from processing.normalizer import TextNormalizer
from config import Config

logger = logging.getLogger(__name__)


class SearchIndexer:
    """Indexes OCR text for fast search."""
    
    def __init__(self):
        self.normalizer = TextNormalizer()
        self.semantic_index = None
        
        if Config.ENABLE_SEMANTIC_SEARCH:
            self._init_semantic_index()
    
    def _init_semantic_index(self):
        """Initialize semantic search index."""
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
            
            # Initialize embedding model
            self.embedding_model = SentenceTransformer(Config.SEMANTIC_MODEL)
            
            # Initialize ChromaDB
            chroma_path = Config.INDEXES_PATH / "chroma_db"
            self.chroma_client = chromadb.PersistentClient(path=str(chroma_path))
            self.semantic_collection = self.chroma_client.get_or_create_collection(
                name="ocr_texts",
                metadata={"hnsw:space": "cosine"}
            )
            
            logger.info("Semantic search index initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize semantic search: {e}")
            self.semantic_index = None
    
    def index_ocr_text(self, ocr_text_id: str) -> bool:
        """Index an OCR text for search."""
        with get_db() as db:
            ocr_text = db.query(OCRText).filter(OCRText.id == ocr_text_id).first()
            if not ocr_text:
                logger.error(f"OCR text {ocr_text_id} not found")
                return False
            
            # Check if already indexed
            existing = db.query(SearchIndex).filter(
                SearchIndex.ocr_text_id == ocr_text_id
            ).first()
            
            if existing:
                logger.debug(f"OCR text {ocr_text_id} already indexed")
                return True
            
            # Normalize and tokenize for search
            searchable_text = self.normalizer.normalize_for_search(ocr_text.normalized_text)
            tokens = self.normalizer.tokenize(ocr_text.normalized_text)
            
            # Create search index record
            index_id = str(uuid.uuid4())
            search_index = SearchIndex(
                id=index_id,
                ocr_text_id=ocr_text_id,
                document_id=ocr_text.document_id,
                searchable_text=searchable_text,
                tokens=tokens
            )
            
            db.add(search_index)
            db.commit()
            
            # Add to semantic index if enabled
            if self.semantic_index is not None and self.semantic_collection:
                try:
                    embedding = self.embedding_model.encode(ocr_text.normalized_text).tolist()
                    self.semantic_collection.add(
                        ids=[ocr_text_id],
                        embeddings=[embedding],
                        documents=[ocr_text.normalized_text],
                        metadatas=[{
                            'document_id': ocr_text.document_id,
                            'page_number': ocr_text.page_number
                        }]
                    )
                except Exception as e:
                    logger.warning(f"Failed to add to semantic index: {e}")
            
            logger.debug(f"Indexed OCR text {ocr_text_id}")
            return True
    
    def index_document(self, document_id: str) -> int:
        """Index all OCR texts for a document."""
        # Query IDs only to avoid DetachedInstanceError
        with get_db() as db:
            ocr_text_ids = [
                row[0]
                for row in db.query(OCRText.id).filter(
                    OCRText.document_id == document_id
                ).all()
            ]
        
        indexed = 0
        for ocr_text_id in ocr_text_ids:
            if self.index_ocr_text(ocr_text_id):
                indexed += 1
        
        return indexed


