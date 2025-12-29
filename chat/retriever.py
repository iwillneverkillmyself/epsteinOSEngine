"""Retrieval module for chat API - retrieves relevant passages from indexed OCR text."""

from typing import List, Dict, Optional
from database import get_db
from models import ImagePage
from search.searcher import SearchEngine
import logging

logger = logging.getLogger(__name__)


def retrieve_passages(query: str, top_k: int = 8, search_type: str = "keyword") -> List[Dict]:
    """
    Retrieve top passages from indexed OCR text for a query.
    
    Args:
        query: User's search query
        top_k: Maximum number of passages to retrieve
        search_type: Type of search ("keyword", "phrase", "fuzzy", "semantic")
    
    Returns:
        List of passage dicts with:
        - document_id: str
        - page_id: Optional[str] (ImagePage.id if available)
        - page_number: int
        - snippet: str (highlighted text snippet)
        - full_text: str (full passage text, truncated)
        - score: float (relevance score)
        - file_url: str (URL to document file)
        - thumbnail_url: str (URL to page thumbnail or document thumbnail)
    """
    search_engine = SearchEngine()
    
    # Perform search based on type
    if search_type == "phrase":
        results = search_engine.phrase_search(query, limit=top_k)
    elif search_type == "fuzzy":
        results = search_engine.fuzzy_search(query, limit=top_k)
    elif search_type == "semantic":
        results = search_engine.semantic_search(query, limit=top_k)
    else:  # default to keyword
        results = search_engine.keyword_search(query, limit=top_k)
    
    if not results:
        return []
    
    # Map results to citations with page_id and URLs
    with get_db() as db:
        citations = []
        for result in results:
            document_id = result.get("document_id")
            page_number = result.get("page_number", 1)
            
            if not document_id:
                continue
            
            # Look up ImagePage to get page_id
            page_id = None
            image_page = db.query(ImagePage).filter(
                ImagePage.document_id == document_id,
                ImagePage.page_number == page_number
            ).first()
            
            if image_page:
                page_id = image_page.id
            
            # Build URLs
            file_url = f"/files/{document_id}"
            if page_id:
                thumbnail_url = f"/thumbnails/{page_id}"
            else:
                thumbnail_url = f"/file-thumbnails/{document_id}"
            
            # Calculate score (use confidence or similarity if available)
            score = result.get("similarity") or result.get("confidence") or 0.5
            
            citations.append({
                "document_id": document_id,
                "page_id": page_id,
                "page_number": page_number,
                "snippet": result.get("snippet", "")[:500],  # Limit snippet length
                "full_text": result.get("full_text", result.get("snippet", ""))[:1000],  # Limit full text
                "score": float(score),
                "file_url": file_url,
                "thumbnail_url": thumbnail_url,
            })
    
    # Sort by score descending
    citations.sort(key=lambda x: x["score"], reverse=True)
    
    return citations[:top_k]

