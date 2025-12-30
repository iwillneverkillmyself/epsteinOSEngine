"""
Always-on DOJ ingestion daemon.

Runs outside the API process so it survives API restarts and can be managed via DB state.
"""

from __future__ import annotations

import os
import time
import logging
import socket
from datetime import datetime, timedelta
from typing import Optional

from database import init_db, get_db
from models import IngestionState

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _utcnow() -> datetime:
    return datetime.utcnow()


def _get_or_create_state(name: str = "doj") -> IngestionState:
    with get_db() as db:
        st = db.query(IngestionState).filter(IngestionState.name == name).first()
        if st is None:
            st = IngestionState(name=name, enabled=True, paused=False)
            db.add(st)
            db.flush()
        return st


def _update_state(
    *,
    name: str = "doj",
    enabled: Optional[bool] = None,
    paused: Optional[bool] = None,
    last_error: Optional[str] = None,
    heartbeat: bool = False,
    run_started: bool = False,
    run_completed: bool = False,
):
    with get_db() as db:
        st = db.query(IngestionState).filter(IngestionState.name == name).first()
        if st is None:
            st = IngestionState(name=name, enabled=True, paused=False)
            db.add(st)
            db.flush()
        if enabled is not None:
            st.enabled = enabled
        if paused is not None:
            st.paused = paused
        if last_error is not None:
            st.last_error = last_error[:2000]
        if heartbeat:
            st.last_heartbeat_at = _utcnow()
        if run_started:
            st.last_run_started_at = _utcnow()
        if run_completed:
            st.last_run_completed_at = _utcnow()


def _lease_acquire(name: str, owner: str, lease_seconds: int) -> bool:
    """
    Best-effort single-leader lock so you can run desired_count > 1 without duplicated work.
    """
    now = _utcnow()
    expires = now + timedelta(seconds=lease_seconds)
    with get_db() as db:
        st = db.query(IngestionState).filter(IngestionState.name == name).with_for_update().first()
        if st is None:
            st = IngestionState(name=name, enabled=True, paused=False)
            db.add(st)
            db.flush()
        # If lease is free or expired, take it.
        if st.lease_expires_at is None or st.lease_expires_at < now or st.lease_owner == owner:
            st.lease_owner = owner
            st.lease_expires_at = expires
            st.last_heartbeat_at = now
            return True
        return False


def _lease_renew(name: str, owner: str, lease_seconds: int):
    now = _utcnow()
    expires = now + timedelta(seconds=lease_seconds)
    with get_db() as db:
        st = db.query(IngestionState).filter(IngestionState.name == name).first()
        if not st:
            return
        if st.lease_owner == owner:
            st.lease_expires_at = expires
            st.last_heartbeat_at = now


def _state_allows_run(name: str) -> bool:
    with get_db() as db:
        st = db.query(IngestionState).filter(IngestionState.name == name).first()
        if not st:
            return True
        if not st.enabled:
            return False
        if st.paused:
            return False
        return True


def run_once(skip_existing: bool = True, limit: Optional[int] = None):
    """
    Perform one DOJ crawl + download + OCR + index pass using the existing ingestion logic.

    Note: this reuses the same codepaths as the API endpoint, but runs in a dedicated process.
    """
    import asyncio
    from pathlib import Path
    from ingestion.doj_crawler import DOJEpsteinCrawler
    from ingestion.storage import DocumentStorage
    from ingestion.pdf_converter import pdf_to_images, is_pdf
    from ocr.processor import OCRProcessor
    from ocr.textract import TextractEngine
    from processing.text_processor import TextProcessor
    from search.indexer import SearchIndexer
    from PIL import Image
    import hashlib
    import re
    from database import get_db as _get_db
    from models import OCRText
    from config import Config

    async def _do():
        storage = DocumentStorage()
        textract = TextractEngine()
        if not getattr(textract, "enabled", False):
            raise RuntimeError(
                "AWS Textract is not configured/enabled for DOJ ingestion service. "
                "Ensure the ECS task role has Textract permissions and region is set."
            )
        ocr_processor = OCRProcessor(ocr_engine=textract)
        text_processor = TextProcessor()
        indexer = SearchIndexer()

        temp_dir = Config.STORAGE_PATH / "doj_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        files_processed = 0
        files_downloaded = 0

        logger.info("Starting DOJ file ingestion (service run_once)...")

        async with DOJEpsteinCrawler() as crawler:
            files = await crawler.discover_files()
            if limit is not None:
                files = files[: max(int(limit), 0)]

        if not files:
            logger.info("No DOJ files discovered.")
            return

        async with DOJEpsteinCrawler() as crawler:
            for file_info in files:
                # External pause/stop is handled at the loop level via DB state.

                url_hash = hashlib.sha256(file_info["url"].encode("utf-8")).hexdigest()[:16]
                safe_basename = re.sub(r"[^\w\-_\.]", "_", file_info["filename"])
                download_path = temp_dir / f"{url_hash}_{safe_basename}"
                if not download_path.exists() or download_path.stat().st_size == 0:
                    ok = await crawler.fetch_file(file_info["url"], download_path)
                    if not ok:
                        errors.append(f"Download failed: {file_info.get('filename', 'unknown')}")
                        continue
                    files_downloaded += 1

                file_info["local_path"] = str(download_path)
                file_info["file_size"] = download_path.stat().st_size

                try:
                    doc_id, is_new = storage.store_document(file_info)
                    if not is_new and skip_existing:
                        continue

                    file_path = Path(file_info["local_path"])
                    if is_pdf(file_path):
                        images_dir = temp_dir / f"{doc_id}_images"
                        image_paths = pdf_to_images(file_path, images_dir)
                    else:
                        image_paths = [file_path]

                    for page_num, image_path in enumerate(image_paths, start=1):
                        img = Image.open(image_path)
                        width, height = img.size
                        storage.store_image_page(doc_id, page_num, image_path, width, height)

                    logger.info(f"Processing OCR for document {doc_id} with AWS Textract (service)")
                    ocr_processor.process_document(doc_id)

                    with _get_db() as db:
                        ocr_text_ids = [
                            row[0]
                            for row in db.query(OCRText.id)
                            .filter(OCRText.document_id == doc_id)
                            .all()
                        ]
                    for ocr_text_id in ocr_text_ids:
                        text_processor.process_ocr_text(ocr_text_id)

                    indexed = indexer.index_document(doc_id)
                    logger.info(f"Indexed {indexed} OCR texts for document {doc_id} (service)")
                    files_processed += 1
                except Exception as e:
                    msg = f"Error processing {file_info.get('filename', 'unknown')}: {e}"
                    logger.exception(msg)
                    errors.append(msg)

        logger.info(f"DOJ ingestion run_once complete: processed={files_processed} downloaded={files_downloaded} errors={len(errors)}")

    asyncio.run(_do())


def run_loop():
    """
    Long-running service loop.

    Behavior:
    - Maintains a lease to ensure single leader.
    - Periodically runs DOJ ingestion (crawl+download+OCR+index) while enabled & not paused.
    - Writes heartbeats to DB so the API can report status across restarts.
    """
    init_db()

    name = os.getenv("INGESTION_NAME", "doj")
    poll_seconds = _env_int("DOJ_INGEST_POLL_SECONDS", 60)
    run_interval_seconds = _env_int("DOJ_INGEST_RUN_INTERVAL_SECONDS", 10 * 60)
    lease_seconds = _env_int("DOJ_INGEST_LEASE_SECONDS", 120)
    skip_existing = os.getenv("DOJ_SKIP_EXISTING", "true").lower() != "false"

    owner = os.getenv("INGESTION_LEASE_OWNER") or f"{socket.gethostname()}:{os.getpid()}"

    _get_or_create_state(name)
    logger.info(
        f"DOJ ingestion service starting (owner={owner}, poll={poll_seconds}s, interval={run_interval_seconds}s, skip_existing={skip_existing})"
    )

    last_run_at: Optional[datetime] = None

    while True:
        try:
            if not _lease_acquire(name, owner, lease_seconds):
                time.sleep(poll_seconds)
                continue

            _lease_renew(name, owner, lease_seconds)
            _update_state(name=name, heartbeat=True)

            if not _state_allows_run(name):
                time.sleep(poll_seconds)
                continue

            now = _utcnow()
            if last_run_at is not None and (now - last_run_at).total_seconds() < run_interval_seconds:
                time.sleep(poll_seconds)
                continue

            _update_state(name=name, run_started=True, last_error=None)
            try:
                run_once(skip_existing=skip_existing, limit=None)
                _update_state(name=name, run_completed=True, last_error=None)
            except Exception as e:
                _update_state(name=name, last_error=str(e))
                logger.exception(f"DOJ ingestion service run failed: {e}")
            finally:
                last_run_at = _utcnow()
        except Exception as e:
            logger.exception(f"DOJ ingestion service loop error: {e}")
            _update_state(name=name, last_error=str(e), heartbeat=True)
            time.sleep(max(poll_seconds, 5))


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_loop()


if __name__ == "__main__":
    main()


