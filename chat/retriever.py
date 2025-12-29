"""Retrieval module for chat API - retrieves relevant passages from indexed OCR text and web."""

from typing import List, Dict, Optional
from database import get_db
from models import ImagePage
from search.searcher import SearchEngine
import logging

logger = logging.getLogger(__name__)


def retrieve_passages(query: str, top_k: int = 8, search_type: str = "keyword", include_web: bool = True) -> List[Dict]:
    """
    Retrieve top passages from indexed OCR text and web news for a query.
    
    Args:
        query: User's search query
        top_k: Maximum number of passages to retrieve
        search_type: Type of search ("keyword", "phrase", "fuzzy", "semantic")
        include_web: Whether to include web news search results
    
    Returns:
        List of passage dicts with:
        - document_id: str
        - page_id: Optional[str] (ImagePage.id if available)
        - page_number: int
        - snippet: str (highlighted text snippet)
        - full_text: str (full passage text, truncated)
        - score: float (relevance score)
        - file_url: str (URL to document file or news article)
        - thumbnail_url: str (URL to page thumbnail or document thumbnail)
        - title: Optional[str] (for web articles)
        - source: Optional[str] (for web articles)
        - url: Optional[str] (for web articles)
    """
    all_passages = []
    
    # 1. Search local documents (existing functionality)
    search_engine = SearchEngine()
    
    if search_type == "phrase":
        local_results = search_engine.phrase_search(query, limit=top_k)
    elif search_type == "fuzzy":
        local_results = search_engine.fuzzy_search(query, limit=top_k)
    elif search_type == "semantic":
        local_results = search_engine.semantic_search(query, limit=top_k)
    else:  # default to keyword
        local_results = search_engine.keyword_search(query, limit=top_k)
    
    # Map local results to citations
    if local_results:
        with get_db() as db:
            for result in local_results:
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
                
                # Calculate score
                score = result.get("similarity") or result.get("confidence") or 0.5
                
                all_passages.append({
                    "document_id": document_id,
                    "page_id": page_id,
                    "page_number": page_number,
                    "snippet": result.get("snippet", "")[:500],
                    "full_text": result.get("full_text", result.get("snippet", ""))[:1000],
                    "score": float(score),
                    "file_url": file_url,
                    "thumbnail_url": thumbnail_url,
                })
    
    # 2. Search web news (new functionality)
    if include_web:
        try:
            from chat.web_search import retrieve_web_news_passages
            web_passages = retrieve_web_news_passages(query, top_k=min(5, top_k // 2))
            all_passages.extend(web_passages)
        except Exception as e:
            logger.warning(f"Web search failed: {e}", exc_info=True)
            # Continue without web results if search fails
    
    # Sort by score descending
    all_passages.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    return all_passages[:top_k]

