"""Crawler for fetching documents from source endpoint."""
import aiohttp
import asyncio
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import logging
from config import Config

logger = logging.getLogger(__name__)


class DocumentCrawler:
    """Crawls and discovers documents from the source endpoint."""
    
    def __init__(self, base_url: str = None):
        # Root used for downloading full files: GET {source_root}/{key}
        self.source_root = (base_url or Config.SOURCE_ENDPOINT).rstrip("/")
        self.session: Optional[aiohttp.ClientSession] = None
        self.default_headers = {
            "User-Agent": "epsteingptengine-ocr-ingestor/1.0 (+https://localhost)",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        }
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.default_headers)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def discover_files(self) -> List[Dict[str, str]]:
        """
        Discover all available files from the endpoint.
        Returns list of file metadata: {url, filename, file_type}
        """
        files = []
        
        try:
            # Many CF Workers don't serve a directory listing at the root.
            # Try a small set of common listing endpoints.
            candidate_urls = self._candidate_listing_urls(self.source_root)

            content = None
            content_type = ""
            last_status = None

            for candidate in candidate_urls:
                try:
                    async with self.session.get(candidate) as response:
                        last_status = response.status
                        if response.status != 200:
                            continue
                        content_type = (response.headers.get("content-type") or "").lower()
                        content = await response.text()
                        # If we got a 200, stop trying further candidates.
                        # NOTE: do NOT overwrite source_root; listing endpoints may be /api/*.
                        break
                except Exception:
                    continue

            if content is None:
                raise RuntimeError(f"Non-200 from all listing candidates; last_status={last_status}")

            # Prefer JSON if server provides it
            if "application/json" in content_type:
                try:
                    # We already read text into `content`; parse it.
                    import json
                    data = json.loads(content)
                    files = self._extract_files_from_json(data, download_base=self.source_root)
                except Exception:
                    files = []
            else:
                # Many worker endpoints return JSON but with text/plain; try parsing
                if content.strip().startswith("{") or content.strip().startswith("["):
                    try:
                        import json
                        data = json.loads(content)
                        files = self._extract_files_from_json(data, download_base=self.source_root)
                    except Exception:
                        files = []

                # If still nothing, parse HTML for links
                if not files:
                    soup = BeautifulSoup(content, 'html.parser')
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        full_url = urljoin(self.source_root + "/", href)
                        file_ext = Path(href).suffix.lower()
                        if file_ext in ['.pdf', '.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp']:
                            files.append({
                                'url': full_url,
                                'filename': Path(href).name,
                                'file_type': file_ext[1:] if file_ext else 'unknown'
                            })

                # If no links found, try common file patterns
                if not files:
                    files = await self._try_common_patterns()

        except Exception as e:
            logger.error(f"Error discovering files: {e}")
            # Fallback: try common patterns
            files = await self._try_common_patterns()
        
        logger.info(f"Discovered {len(files)} files")
        return files

    def _candidate_listing_urls(self, base_url: str) -> List[str]:
        """
        Return candidate URLs to try for listing/discovery.
        Many CF Worker-backed endpoints use a JSON manifest route.
        NOTE: Order matters! URLs returning ALL files should be tried first.
        """
        base = base_url.rstrip("/")
        return [
            # Known endpoint for epstein-files - returns ALL files, not paginated
            base + "/api/all-files",
            base + "/all",
            base + "/all.json",
            # Generic endpoints (may be paginated)
            base,
            base + "/",
            base + "/index",
            base + "/index.html",
            base + "/index.json",
            base + "/manifest.json",
            base + "/files.json",  # JSON is less likely to be paginated
            base + "/list.json",
            base + "/api",
            base + "/api/list",
            base + "/api/files",
            base + "/files",  # Often paginated, try last
            base + "/list",
        ]

    def _extract_files_from_json(self, data, download_base: str) -> List[Dict[str, str]]:
        """
        Best-effort extraction from unknown JSON shapes.
        Supported shapes:
        - ["https://.../file.pdf", ...]
        - [{"url": "...", "name": "..."}]
        - {"files":[...]} or {"items":[...]}
        """
        out: List[Dict[str, str]] = []

        def handle_item(item):
            if isinstance(item, str):
                href = item
                full_url = urljoin(download_base + "/", href)
                ext = Path(href).suffix.lower()
                if ext in ['.pdf', '.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp']:
                    out.append({
                        "url": full_url,
                        "filename": Path(href).name,
                        "file_type": ext[1:] if ext else "unknown",
                    })
            elif isinstance(item, dict):
                # Epstein-files uses `key` (download is GET /{key})
                href = item.get("key") or item.get("url") or item.get("href") or item.get("path")
                name = item.get("filename") or item.get("name") or (Path(href).name if href else None)
                if not href or not name:
                    return
                full_url = urljoin(download_base + "/", str(href).lstrip("/"))
                ext = Path(name).suffix.lower()
                if ext in ['.pdf', '.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp']:
                    out.append({
                        "url": full_url,
                        "filename": name,
                        "file_type": ext[1:] if ext else "unknown",
                    })

        if isinstance(data, list):
            for item in data:
                handle_item(item)
        elif isinstance(data, dict):
            for key in ("files", "items", "data", "results"):
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        handle_item(item)
            # Sometimes it’s a dict of filename -> url
            if not out:
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        handle_item(v)

        return out
    
    async def _try_common_patterns(self) -> List[Dict[str, str]]:
        """Try common file naming patterns if directory listing fails."""
        files = []
        common_patterns = ['index', 'file', 'document', 'page']
        extensions = ['pdf', 'jpg', 'jpeg', 'png']
        
        for pattern in common_patterns:
            for ext in extensions:
                for i in range(1, 100):  # Try up to 100 files
                    test_url = f"{self.source_root}/{pattern}{i}.{ext}"
                    if await self._file_exists(test_url):
                        files.append({
                            'url': test_url,
                            'filename': f"{pattern}{i}.{ext}",
                            'file_type': ext
                        })
        
        return files
    
    async def _file_exists(self, url: str) -> bool:
        """Check if a file exists at the given URL."""
        try:
            async with self.session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                return response.status == 200
        except:
            return False
    
    async def fetch_file(self, url: str, save_path: Path) -> bool:
        """Fetch a file and save it to disk."""
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(save_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)
                    logger.info(f"Fetched {url} -> {save_path}")
                    return True
                else:
                    logger.warning(f"Failed to fetch {url}: status {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return False


async def crawl_and_fetch_all(output_dir: Path) -> List[Dict]:
    """Main function to crawl and fetch all documents."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    async with DocumentCrawler() as crawler:
        files = await crawler.discover_files()
        
        fetched_files = []
        for file_info in files:
            filename = file_info['filename']
            save_path = output_dir / filename
            
            if await crawler.fetch_file(file_info['url'], save_path):
                file_info['local_path'] = str(save_path)
                file_info['file_size'] = save_path.stat().st_size
                fetched_files.append(file_info)
        
        return fetched_files

