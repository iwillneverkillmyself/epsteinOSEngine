"""Specialized crawler for Department of Justice Epstein files (justice.gov/epstein)."""

import aiohttp
import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class DOJEpsteinCrawler:
    """
    Crawls and downloads files from the DOJ Epstein page.

    Requirements:
    - Discover downloadable files linked from https://www.justice.gov/epstein
    - Exclude items under "DOJ Disclosures" → "Epstein Files Transparency Act"
    """

    def __init__(self, base_url: str = "https://www.justice.gov/epstein"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None

        # IMPORTANT: justice.gov/epstein is protected by Akamai.
        # We reliably get an interstitial challenge with a curl-like UA, but often get
        # a hard 401 "apology" page with browser-like UAs.
        self.default_headers = {
            "User-Agent": "curl/8.5.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }

    async def __aenter__(self):
        # Cookie jar is critical: interstitial verification sets cookies.
        self.session = aiohttp.ClientSession(
            headers=self.default_headers,
            cookie_jar=aiohttp.CookieJar(),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _should_exclude(self, section_name: str, link_text: str, href: str) -> bool:
        """
        Exclude items under "DOJ Disclosures" that are part of the "Epstein Files Transparency Act".
        """
        section_lower = (section_name or "").lower()
        link_lower = (link_text or "").lower()
        href_lower = (href or "").lower()

        is_doj_disclosures = any(
            s in section_lower
            for s in (
                "doj disclosures",
                "doj disclosure",
                "department of justice disclosures",
                "department of justice disclosure",
            )
        )

        # If we're in a subsection explicitly titled "Epstein Files Transparency Act" under DOJ Disclosures,
        # exclude everything in it.
        if is_doj_disclosures and "epstein files transparency act" in section_lower:
            return True

        is_transparency_act = any(
            t in link_lower
            for t in (
                "epstein files transparency act",
                "transparency act",
                "efta",
            )
        ) or ("transparency-act" in href_lower)

        return bool(is_doj_disclosures and is_transparency_act)

    async def _fetch_html(self, url: str) -> str:
        """
        Fetch HTML; if we hit Akamai interstitial, emulate its verification flow and retry.
        """
        if not self.session:
            raise RuntimeError("Session not initialized")

        async def _get(u: str) -> tuple[int, str]:
            async with self.session.get(u, allow_redirects=True) as resp:
                return resp.status, await resp.text()

        status, html = await _get(url)

        # Akamai interstitial is usually status=200 with a tiny HTML containing bm-verify + /_sec/verify.
        if status == 200 and "/_sec/verify?provider=interstitial" in html and "bm-verify" in html:
            token_match = re.search(r'"bm-verify"\s*:\s*"([^"]+)"', html)
            if not token_match:
                token_match = re.search(r"bm-verify=([^'\"&]+)", html)
            i_match = re.search(r"var\s+i\s*=\s*(\d+)\s*;", html)
            num_match = re.search(r'Number\("(\d+)"\s*\+\s*"(\d+)"\)', html)

            if token_match and i_match and num_match:
                bm_verify = token_match.group(1)
                i_val = int(i_match.group(1))
                pow_val = i_val + int(num_match.group(1) + num_match.group(2))

                verify_url = urljoin(url, "/_sec/verify?provider=interstitial")
                payload = {"bm-verify": bm_verify, "pow": pow_val}

                logger.info("Akamai interstitial detected; performing verification handshake")
                async with self.session.post(verify_url, json=payload) as resp:
                    # cookies are the important part; response may direct reload/location
                    try:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict) and data.get("location"):
                            await _get(urljoin(url, data["location"]))
                    except Exception:
                        pass

                status, html = await _get(url)
            else:
                logger.warning("Akamai interstitial detected but could not parse token/pow; returning interstitial HTML")

        # Some clients get a 401 "apology" page; return body anyway (caller can detect no links).
        return html

    async def discover_files(self) -> List[Dict[str, str]]:
        """
        Discover all downloadable files on the DOJ Epstein page.
        Returns list of:
          {url, filename, file_type, section, description, source}
        """
        files: List[Dict[str, str]] = []

        try:
            logger.info(f"Crawling {self.base_url}")

            root_html = await self._fetch_html(self.base_url)
            root_soup = BeautifulSoup(root_html, "html.parser")

            # Discover relevant subpages linked from the Epstein library landing page.
            pages: List[tuple[str, str]] = [(self.base_url, "Epstein Library")]
            for a in root_soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                if not href.startswith("/epstein/"):
                    continue
                full = urljoin(self.base_url, href)
                label = href.split("/epstein/", 1)[1].replace("-", " ").strip() or "Epstein"
                label = " ".join(w.capitalize() for w in label.split())
                pages.append((full, label))

            # de-dupe while keeping order
            seen = set()
            pages = [(u, l) for (u, l) in pages if not (u in seen or seen.add(u))]

            found_urls: set[str] = set()

            def _maybe_add(href: str, link_text: str, section_name: str, description: str):
                if not href:
                    return
                full_url = urljoin(self.base_url, href)
                if full_url in found_urls:
                    return

                ext = Path(urlparse(full_url).path).suffix.lower()
                if ext not in [".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".doc", ".docx"]:
                    return

                if self._should_exclude(section_name, link_text, href):
                    return

                found_urls.add(full_url)
                files.append(
                    {
                        "url": full_url,
                        "filename": Path(urlparse(full_url).path).name,
                        "file_type": ext[1:] if ext else "unknown",
                        "section": section_name or "General",
                        "description": (description or link_text or "")[:200],
                        "source": "doj-epstein",
                    }
                )

            for page_url, page_label in pages:
                logger.info(f"Scanning DOJ Epstein subpage: {page_url}")
                html_content = await self._fetch_html(page_url)
                soup = BeautifulSoup(html_content, "html.parser")

                # Collect sections by containers; DOJ pages are Drupal and can vary.
                sections = soup.find_all(
                    ["div", "section", "article"],
                    class_=re.compile(r"(content|document|file|download|view|field|block)", re.IGNORECASE),
                )

                # Parse within sections to capture context
                for section in sections:
                    subsection = ""
                    header = section.find(["h1", "h2", "h3", "h4", "h5"])
                    if header:
                        subsection = header.get_text(strip=True)
                    section_name = page_label if not subsection else f"{page_label} - {subsection}"

                    for a in section.find_all("a", href=True):
                        href = a.get("href") or ""
                        link_text = a.get_text(strip=True) or ""
                        parent = a.parent
                        desc = parent.get_text(strip=True) if parent and parent.name in ["li", "p", "div"] else link_text
                        _maybe_add(href, link_text, section_name, desc)

                # Also scan entire page for direct links (some pages don't wrap in consistent blocks)
                for a in soup.find_all("a", href=True):
                    href = a.get("href") or ""
                    link_text = a.get_text(strip=True) or ""
                    _maybe_add(href, link_text, page_label, link_text)

            logger.info(f"Discovered {len(files)} DOJ Epstein files (excluding Transparency Act)")
            return files

        except Exception as e:
            logger.exception(f"Error discovering files from DOJ: {e}")
            return files

    async def fetch_file(self, url: str, save_path: Path) -> bool:
        """Fetch a file and save it to disk."""
        if not self.session:
            raise RuntimeError("Session not initialized")

        try:
            async with self.session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to fetch {url}: status {resp.status}")
                    return False

                save_path.parent.mkdir(parents=True, exist_ok=True)
                total_size = 0
                with open(save_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        total_size += len(chunk)
                logger.info(f"Downloaded {save_path.name} ({total_size / 1024:.1f} KB)")
                return True

        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return False

    async def crawl_and_fetch_all(self, output_dir: Path, limit: int | None = None) -> List[Dict]:
        """Discover and download DOJ Epstein files (excluding Transparency Act items)."""
        output_dir.mkdir(parents=True, exist_ok=True)

        files = await self.discover_files()
        if not files:
            logger.warning("No files discovered from DOJ website")
            return []

        if limit is not None:
            files = files[: max(int(limit), 0)]

        fetched_files: List[Dict] = []
        for file_info in files:
            filename = file_info["filename"]
            safe_basename = re.sub(r"[^\w\-_\.]", "_", filename)
            # IMPORTANT: many DOJ subpages reuse the same basenames (e.g. 001.pdf) in different folders.
            # Use a stable URL hash prefix to avoid collisions and to enable "skip if already downloaded".
            url_hash = hashlib.sha256(file_info["url"].encode("utf-8")).hexdigest()[:16]
            safe_filename = f"{url_hash}_{safe_basename}"
            save_path = output_dir / safe_filename

            # Skip download if already present on disk
            if save_path.exists() and save_path.stat().st_size > 0:
                file_info["local_path"] = str(save_path)
                file_info["file_size"] = save_path.stat().st_size
                fetched_files.append(file_info)
                continue

            if await self.fetch_file(file_info["url"], save_path):
                file_info["local_path"] = str(save_path)
                file_info["file_size"] = save_path.stat().st_size
                fetched_files.append(file_info)

            await asyncio.sleep(0.5)

        logger.info(f"Successfully fetched {len(fetched_files)}/{len(files)} files")
        return fetched_files


async def crawl_doj_epstein(output_dir: Path) -> List[Dict]:
    """Convenience wrapper."""
    async with DOJEpsteinCrawler() as crawler:
        return await crawler.crawl_and_fetch_all(output_dir)


