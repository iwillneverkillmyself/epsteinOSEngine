"""Search functionality."""
import re
from typing import List, Dict, Optional
from difflib import SequenceMatcher
import logging
from sqlalchemy import or_, and_
from database import get_db
from models import OCRText, SearchIndex, Entity, ImagePage
from config import Config

logger = logging.getLogger(__name__)


class SearchEngine:
    """Search engine for OCR text."""
    
    def __init__(self):
        self.semantic_searcher = None
        if Config.ENABLE_SEMANTIC_SEARCH:
            self._init_semantic_search()
    
    def _init_semantic_search(self):
        """Initialize semantic search."""
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
            
            self.embedding_model = SentenceTransformer(Config.SEMANTIC_MODEL)
            chroma_path = Config.INDEXES_PATH / "chroma_db"
            self.chroma_client = chromadb.PersistentClient(path=str(chroma_path))
            self.semantic_collection = self.chroma_client.get_or_create_collection(
                name="ocr_texts"
            )
            self.semantic_searcher = True
        except Exception as e:
            logger.warning(f"Semantic search not available: {e}")
    
    def keyword_search(self, query: str, limit: int = 50) -> List[Dict]:
        """
        Perform keyword search.
        
        Returns:
            List of search results with snippets and metadata
        """
        query_lower = query.lower()
        query_tokens = query_lower.split()
        
        with get_db() as db:
            # Search in searchable_text
            conditions = []
            for token in query_tokens:
                conditions.append(SearchIndex.searchable_text.contains(token))
            
            if conditions:
                results = db.query(SearchIndex).filter(
                    or_(*conditions)
                ).limit(limit).all()
            else:
                results = []
            
            # Format results
            formatted_results = []
            for result in results:
                ocr_text = db.query(OCRText).filter(
                    OCRText.id == result.ocr_text_id
                ).first()
                
                if ocr_text:
                    # Create snippet
                    snippet = self._create_snippet(ocr_text.normalized_text, query)
                    
                    # Get image page info
                    image_page = db.query(ImagePage).filter(
                        ImagePage.id == ocr_text.image_page_id
                    ).first()
                    
                    formatted_results.append({
                        'ocr_text_id': ocr_text.id,
                        'document_id': ocr_text.document_id,
                        'page_number': ocr_text.page_number,
                        'snippet': snippet,
                        'full_text': ocr_text.normalized_text[:500],  # Limit length
                        'confidence': ocr_text.confidence,
                        'image_path': image_page.image_path if image_page else None,
                        'bbox': {
                            'x': ocr_text.bbox_x,
                            'y': ocr_text.bbox_y,
                            'width': ocr_text.bbox_width,
                            'height': ocr_text.bbox_height
                        },
                        'word_boxes': ocr_text.word_boxes
                    })
            
            return formatted_results
    
    def fuzzy_search(self, query: str, threshold: float = 0.6, limit: int = 50) -> List[Dict]:
        """
        Perform fuzzy matching search.
        
        Args:
            query: Search query
            threshold: Similarity threshold (0-1)
            limit: Max results
        """
        query_lower = query.lower().strip()
        query_terms = [t for t in re.findall(r"\b\w+\b", query_lower) if t]
        if not query_terms:
            return []
        
        with get_db() as db:
            # Get all indexed texts
            all_indexes = db.query(SearchIndex).limit(5000).all()  # still bounded for safety
            
            scored_results = []
            for index in all_indexes:
                index_tokens = [t for t in (index.tokens or []) if isinstance(t, str)]
                if not index_tokens:
                    continue

                # Token-level fuzzy match:
                # For each query term, find the best-matching doc token using edit similarity.
                # This handles OCR typos like "clincton" -> "clinton".
                per_term_best = []
                for qt in query_terms:
                    best = 0.0
                    for dt in index_tokens:
                        r = SequenceMatcher(None, qt, dt).ratio()
                        if r > best:
                            best = r
                            if best >= 0.99:
                                break
                    per_term_best.append(best)

                similarity = sum(per_term_best) / max(1, len(per_term_best))
                
                if similarity >= threshold:
                    scored_results.append((similarity, index))
            
            # Sort by similarity
            scored_results.sort(key=lambda x: x[0], reverse=True)
            scored_results = scored_results[:limit]
            
            # Format results
            formatted_results = []
            for similarity, result in scored_results:
                ocr_text = db.query(OCRText).filter(
                    OCRText.id == result.ocr_text_id
                ).first()
                
                if ocr_text:
                    snippet = self._create_snippet(ocr_text.normalized_text, query)
                    image_page = db.query(ImagePage).filter(
                        ImagePage.id == ocr_text.image_page_id
                    ).first()
                    
                    formatted_results.append({
                        'ocr_text_id': ocr_text.id,
                        'document_id': ocr_text.document_id,
                        'page_number': ocr_text.page_number,
                        'snippet': snippet,
                        'full_text': ocr_text.normalized_text[:500],
                        'confidence': ocr_text.confidence,
                        'similarity': similarity,
                        'image_path': image_page.image_path if image_page else None,
                        'bbox': {
                            'x': ocr_text.bbox_x,
                            'y': ocr_text.bbox_y,
                            'width': ocr_text.bbox_width,
                            'height': ocr_text.bbox_height
                        },
                        'word_boxes': ocr_text.word_boxes
                    })
            
            return formatted_results
    
    def semantic_search(self, query: str, limit: int = 50) -> List[Dict]:
        """Perform semantic search using embeddings."""
        if not self.semantic_searcher:
            return []
        
        try:
            query_embedding = self.embedding_model.encode(query).tolist()
            
            results = self.semantic_collection.query(
                query_embeddings=[query_embedding],
                n_results=limit
            )
            
            # Format results
            formatted_results = []
            with get_db() as db:
                for i, ocr_id in enumerate(results['ids'][0]):
                    ocr_text = db.query(OCRText).filter(OCRText.id == ocr_id).first()
                    if ocr_text:
                        snippet = self._create_snippet(ocr_text.normalized_text, query)
                        image_page = db.query(ImagePage).filter(
                            ImagePage.id == ocr_text.image_page_id
                        ).first()
                        
                        formatted_results.append({
                            'ocr_text_id': ocr_text.id,
                            'document_id': ocr_text.document_id,
                            'page_number': ocr_text.page_number,
                            'snippet': snippet,
                            'full_text': ocr_text.normalized_text[:500],
                            'confidence': ocr_text.confidence,
                            'image_path': image_page.image_path if image_page else None,
                            'bbox': {
                                'x': ocr_text.bbox_x,
                                'y': ocr_text.bbox_y,
                                'width': ocr_text.bbox_width,
                                'height': ocr_text.bbox_height
                            },
                            'word_boxes': ocr_text.word_boxes
                        })
            
            return formatted_results
        except Exception as e:
            logger.error(f"Semantic search error: {e}")
            return []
    
    def entity_search(self, entity_type: str, entity_value: str, limit: int = 50) -> List[Dict]:
        """
        Search for specific entities.
        
        Args:
            entity_type: name, email, phone, date, keyword
            entity_value: Value to search for
        """
        with get_db() as db:
            # Search entities
            entities = db.query(Entity).filter(
                and_(
                    Entity.entity_type == entity_type,
                    or_(
                        Entity.entity_value.ilike(f"%{entity_value}%"),
                        Entity.normalized_value.ilike(f"%{entity_value.lower()}%")
                    )
                )
            ).limit(limit).all()
            
            # Format results
            formatted_results = []
            for entity in entities:
                ocr_text = db.query(OCRText).filter(
                    OCRText.id == entity.ocr_text_id
                ).first()
                
                if ocr_text:
                    image_page = db.query(ImagePage).filter(
                        ImagePage.id == ocr_text.image_page_id
                    ).first()
                    
                    formatted_results.append({
                        'entity_id': entity.id,
                        'entity_type': entity.entity_type,
                        'entity_value': entity.entity_value,
                        'ocr_text_id': ocr_text.id,
                        'document_id': ocr_text.document_id,
                        'page_number': ocr_text.page_number,
                        'snippet': self._create_snippet(ocr_text.normalized_text, entity_value),
                        'full_text': ocr_text.normalized_text[:500],
                        'confidence': entity.confidence,
                        'image_path': image_page.image_path if image_page else None,
                        'bbox': {
                            'x': entity.bbox_x,
                            'y': entity.bbox_y,
                            'width': entity.bbox_width,
                            'height': entity.bbox_height
                        }
                    })
            
            return formatted_results
    
    def phrase_search(self, phrase: str, limit: int = 50) -> List[Dict]:
        """Search for exact phrase matches."""
        phrase_lower = phrase.lower()
        
        with get_db() as db:
            results = db.query(SearchIndex).filter(
                SearchIndex.searchable_text.contains(phrase_lower)
            ).limit(limit).all()
            
            formatted_results = []
            for result in results:
                ocr_text = db.query(OCRText).filter(
                    OCRText.id == result.ocr_text_id
                ).first()
                
                if ocr_text:
                    snippet = self._create_snippet(ocr_text.normalized_text, phrase)
                    image_page = db.query(ImagePage).filter(
                        ImagePage.id == ocr_text.image_page_id
                    ).first()
                    
                    formatted_results.append({
                        'ocr_text_id': ocr_text.id,
                        'document_id': ocr_text.document_id,
                        'page_number': ocr_text.page_number,
                        'snippet': snippet,
                        'full_text': ocr_text.normalized_text[:500],
                        'confidence': ocr_text.confidence,
                        'image_path': image_page.image_path if image_page else None,
                        'bbox': {
                            'x': ocr_text.bbox_x,
                            'y': ocr_text.bbox_y,
                            'width': ocr_text.bbox_width,
                            'height': ocr_text.bbox_height
                        },
                        'word_boxes': ocr_text.word_boxes
                    })
            
            return formatted_results
    
    def _create_snippet(self, text: str, query: str, context_chars: int = 100) -> str:
        """Create a text snippet highlighting the query."""
        query_lower = query.lower()
        text_lower = text.lower()
        
        # Find query position
        pos = text_lower.find(query_lower)
        if pos == -1:
            # Try to find any word from query
            query_words = query_lower.split()
            for word in query_words:
                pos = text_lower.find(word)
                if pos != -1:
                    break
        
        if pos == -1:
            return text[:200] + "..." if len(text) > 200 else text
        
        # Extract context around match
        start = max(0, pos - context_chars)
        end = min(len(text), pos + len(query) + context_chars)
        
        snippet = text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        
        return snippet

