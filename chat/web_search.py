"""Web search module for finding news articles about Jeffrey Epstein."""

import logging
import httpx
from typing import List, Dict, Optional
from urllib.parse import quote_plus
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def search_web_news(query: str, max_results: int = 5) -> List[Dict]:
    """
    Search for news articles about Jeffrey Epstein using DuckDuckGo.
    
    Args:
        query: Search query (will be combined with "Jeffrey Epstein" context)
        max_results: Maximum number of results to return
    
    Returns:
        List of dicts with:
        - title: str
        - url: str
        - snippet: str
        - source: str (domain name)
    """
    try:
        # Enhance query with Jeffrey Epstein context
        enhanced_query = f"Jeffrey Epstein {query}"
        
        # Use DuckDuckGo HTML search (no API key needed)
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(enhanced_query)}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(search_url, headers=headers)
            response.raise_for_status()
            
            # Parse HTML results
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            
            # DuckDuckGo HTML structure
            result_divs = soup.find_all("div", class_="result")
            
            for div in result_divs[:max_results]:
                try:
                    # Extract title and URL
                    title_elem = div.find("a", class_="result__a")
                    if not title_elem:
                        continue
                    
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    
                    # Extract snippet
                    snippet_elem = div.find("a", class_="result__snippet")
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    
                    # Extract source domain
                    source = ""
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(url)
                        source = parsed.netloc.replace("www.", "")
                    except:
                        pass
                    
                    # Filter for news sources (optional - can be more permissive)
                    if url and title:
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet[:300],  # Limit snippet length
                            "source": source,
                        })
                except Exception as e:
                    logger.debug(f"Error parsing search result: {e}")
                    continue
            
            return results
            
    except Exception as e:
        logger.error(f"Web search error: {e}", exc_info=True)
        return []


def fetch_article_content(url: str, max_length: int = 2000) -> Optional[str]:
    """
    Fetch and extract text content from a news article URL.
    
    Args:
        url: Article URL
        max_length: Maximum length of extracted text
    
    Returns:
        Extracted text content or None if failed
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Remove script and style elements
            for script in soup(["script", "style", "nav", "header", "footer"]):
                script.decompose()
            
            # Try to find main article content
            # Common article selectors
            article_selectors = [
                "article",
                "[role='article']",
                ".article-body",
                ".post-content",
                ".entry-content",
                ".article-content",
                "main",
            ]
            
            content = None
            for selector in article_selectors:
                article = soup.select_one(selector)
                if article:
                    content = article.get_text(separator=" ", strip=True)
                    break
            
            # Fallback: get all paragraph text
            if not content:
                paragraphs = soup.find_all("p")
                content = " ".join([p.get_text(strip=True) for p in paragraphs])
            
            # Clean up whitespace
            if content:
                content = re.sub(r'\s+', ' ', content)
                # Limit length
                if len(content) > max_length:
                    content = content[:max_length] + "..."
            
            return content
            
    except Exception as e:
        logger.debug(f"Error fetching article content from {url}: {e}")
        return None


def retrieve_web_news_passages(query: str, top_k: int = 5) -> List[Dict]:
    """
    Retrieve news articles from the web and format as passages.
    
    Args:
        query: Search query
        top_k: Number of articles to retrieve
    
    Returns:
        List of passage dicts compatible with citation format
    """
    # Search for news articles
    news_results = search_web_news(query, max_results=top_k)
    
    if not news_results:
        return []
    
    # Fetch content for each article
    passages = []
    for result in news_results:
        url = result["url"]
        title = result["title"]
        snippet = result["snippet"]
        source = result["source"]
        
        # Fetch full article content
        content = fetch_article_content(url)
        
        # Use fetched content if available, otherwise use snippet
        full_text = content if content else snippet
        
        # Format as passage dict (compatible with citation format)
        passages.append({
            "document_id": f"web_{hash(url) % 1000000}",  # Pseudo ID for web sources
            "page_id": None,
            "page_number": 0,
            "snippet": snippet[:500],
            "full_text": full_text[:1000] if full_text else snippet[:1000],
            "score": 0.8,  # Default score for web results
            "file_url": url,  # Use news URL as file_url
            "thumbnail_url": None,  # No thumbnail for web articles
            "title": title,
            "source": source,
            "url": url,  # Store original URL
        })
    
    return passages

