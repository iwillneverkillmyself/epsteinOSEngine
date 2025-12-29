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
        enhanced_query = f"Jeffrey Epstein {query} news"
        
        # Use DuckDuckGo HTML search (no API key needed)
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(enhanced_query)}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(search_url, headers=headers)
            response.raise_for_status()
            
            # Parse HTML results
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            
            # Try multiple DuckDuckGo HTML structures (they change frequently)
            # Method 1: Try class="result"
            result_divs = soup.find_all("div", class_="result")
            
            # Method 2: If no results, try alternative structure
            if not result_divs:
                result_divs = soup.find_all("div", class_="web-result")
            
            # Method 3: Try finding result links directly
            if not result_divs:
                result_links = soup.find_all("a", class_="result__a")
                for link in result_links[:max_results]:
                    try:
                        title = link.get_text(strip=True)
                        url = link.get("href", "")
                        if not url or not title:
                            continue
                        
                        # Try to find snippet nearby
                        parent = link.find_parent()
                        snippet = ""
                        if parent:
                            snippet_elem = parent.find("a", class_="result__snippet")
                            if snippet_elem:
                                snippet = snippet_elem.get_text(strip=True)
                            else:
                                # Try to find any text after the link
                                for sibling in parent.next_siblings:
                                    if hasattr(sibling, 'get_text'):
                                        snippet = sibling.get_text(strip=True)[:300]
                                        break
                        
                        source = ""
                        try:
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            source = parsed.netloc.replace("www.", "")
                        except:
                            pass
                        
                        if url and title:
                            results.append({
                                "title": title,
                                "url": url,
                                "snippet": snippet[:300],
                                "source": source,
                            })
                    except Exception as e:
                        logger.debug(f"Error parsing result link: {e}")
                        continue
            
            # Parse results from divs
            for div in result_divs[:max_results]:
                try:
                    # Extract title and URL
                    title_elem = div.find("a", class_="result__a")
                    if not title_elem:
                        # Try alternative selectors
                        title_elem = div.find("a", href=True)
                        if not title_elem:
                            continue
                    
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    
                    # Clean up URL (DuckDuckGo sometimes wraps URLs)
                    if url.startswith("/l/?kh="):
                        # Extract actual URL from DuckDuckGo redirect
                        try:
                            from urllib.parse import parse_qs, urlparse
                            parsed = urlparse(url)
                            params = parse_qs(parsed.query)
                            if "uddg" in params:
                                url = params["uddg"][0]
                        except:
                            pass
                    
                    # Extract snippet
                    snippet = ""
                    snippet_elem = div.find("a", class_="result__snippet")
                    if snippet_elem:
                        snippet = snippet_elem.get_text(strip=True)
                    else:
                        # Try alternative snippet selectors
                        snippet_elem = div.find("div", class_="result__snippet")
                        if snippet_elem:
                            snippet = snippet_elem.get_text(strip=True)
                        else:
                            # Get any text content from the div
                            snippet = div.get_text(strip=True)
                            # Remove title from snippet if present
                            if title in snippet:
                                snippet = snippet.replace(title, "", 1).strip()
                    
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
            
            # Remove duplicates based on URL
            seen_urls = set()
            unique_results = []
            for result in results:
                if result["url"] not in seen_urls:
                    seen_urls.add(result["url"])
                    unique_results.append(result)
            
            logger.info(f"Web search found {len(unique_results)} results for query: {query}")
            return unique_results[:max_results]
            
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

