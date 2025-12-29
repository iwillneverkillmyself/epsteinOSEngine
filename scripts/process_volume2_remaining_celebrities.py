#!/usr/bin/env python3
"""
Process remaining (unprocessed) Volume 2 pages through AWS Rekognition Celebrity detection.

Volume 2 is identified by Document.source_url containing "VOL00002".

Important:
- In some setups, ImagePage.image_path was written from inside a Docker container
  (e.g. "/app/data/storage/temp/...") and does not exist on the host.
- This script therefore regenerates missing page images from the stored PDFs in
  ./data/storage/{document_id}.pdf and saves them to ./data/images/{page_id}.png,
  then updates ImagePage.image_path to the correct host path before calling Rekognition.

This is deliberately page-level (not document-level) so we don't skip documents
that already have *some* celebrity rows but still have unprocessed pages.
"""

import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.append(str(Path(__file__).parent.parent))

import logging
from tqdm import tqdm
from sqlalchemy import func, select

from database import init_db, get_db
from models import Document, ImagePage, Celebrity
from ocr.rekognition import RekognitionProcessor
from config import Config

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _get_unprocessed_volume2_page_ids(limit: int | None = None) -> List[str]:
    """
    Return ImagePage IDs for Volume 2 (VOL00002) that do not yet have any Celebrity rows.
    """
    with get_db() as db:
        vol2_doc_ids = select(Document.id).where(
            func.lower(Document.source_url).contains("vol00002")
        )

        vol2_pages = select(ImagePage.id).where(ImagePage.document_id.in_(vol2_doc_ids))

        processed_page_ids = (
            select(Celebrity.image_page_id)
            .where(Celebrity.image_page_id.in_(vol2_pages))
            .distinct()
        )

        q = (
            db.query(ImagePage.id)
            .filter(ImagePage.id.in_(vol2_pages))
            .filter(~ImagePage.id.in_(processed_page_ids))
            .order_by(ImagePage.id.asc())
        )

        if limit is not None:
            q = q.limit(limit)

        return [row[0] for row in q.all()]


def _pdf_path_for_document(document_id: str) -> Optional[Path]:
    """
    Locate the stored PDF for a document ID.
    Expected location: Config.STORAGE_PATH/{document_id}.pdf
    """
    p = Config.STORAGE_PATH / f"{document_id}.pdf"
    if p.exists():
        return p
    for ext in (".pdf", ".png", ".jpg", ".jpeg"):
        alt = Config.STORAGE_PATH / f"{document_id}{ext}"
        if alt.exists():
            return alt
    return None


def _render_pdf_page_to_png(pdf_path: Path, page_number: int, out_path: Path) -> Tuple[int, int]:
    """
    Render a single PDF page (1-indexed) to PNG at out_path.
    Returns (width, height).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if _HAS_FITZ:
        doc = fitz.open(pdf_path)
        try:
            if page_number < 1 or page_number > len(doc):
                raise ValueError(f"page_number out of range: {page_number} (pages={len(doc)})")
            page = doc[page_number - 1]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            pix.save(out_path)
        finally:
            doc.close()
    else:
        from pdf2image import convert_from_path

        images = convert_from_path(
            str(pdf_path),
            first_page=page_number,
            last_page=page_number,
            dpi=200,
        )
        if not images:
            raise RuntimeError(f"pdf2image returned no images for page {page_number}")
        images[0].save(out_path, "PNG")
        images[0].close()

    if _HAS_PIL:
        img = Image.open(out_path)
        try:
            w, h = img.size
        finally:
            img.close()
        return w, h

    return 0, 0


def _ensure_page_image_exists(page_id: str) -> None:
    """
    Ensure ImagePage.image_path points to a real PNG on this machine.
    If the current path doesn't exist, regenerate from stored PDF and update the DB row.
    """
    doc_id = page_id.split("_page_")[0]

    with get_db() as db:
        page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
        if not page:
            raise RuntimeError(f"ImagePage not found: {page_id}")

        current = Path(page.image_path) if page.image_path else None
        target = Config.IMAGES_PATH / f"{page_id}.png"

        if current and current.exists():
            return
        if target.exists():
            page.image_path = str(target)
            db.commit()
            return

        pdf_path = _pdf_path_for_document(doc_id)
        if not pdf_path:
            raise RuntimeError(f"Stored PDF not found for document_id={doc_id}")

        w, h = _render_pdf_page_to_png(pdf_path, page.page_number, target)
        page.image_path = str(target)
        if w and h:
            page.width = w
            page.height = h
        db.commit()


def main(min_confidence: float = 90.0, limit: int | None = None) -> int:
    init_db()

    processor = RekognitionProcessor()
    if not processor.enabled:
        logger.error(
            "AWS Rekognition not configured/enabled. "
            "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and AWS_DEFAULT_REGION) and retry."
        )
        return 1

    page_ids = _get_unprocessed_volume2_page_ids(limit=limit)
    logger.info(f"Volume 2 pages missing celebrity detections: {len(page_ids)}")

    if not page_ids:
        logger.info("Nothing to do — all Volume 2 pages already processed for celebrities.")
        return 0

    total_found = 0
    errors = 0

    for page_id in tqdm(page_ids, desc="Rekognition celebrities (VOL00002)"):
        try:
            _ensure_page_image_exists(page_id)
            found = processor.process_celebrities(page_id, min_confidence=min_confidence)
            total_found += int(found or 0)
        except Exception as e:
            errors += 1
            logger.error(f"Failed processing {page_id}: {e}")

    logger.info(
        f"Done. Pages processed={len(page_ids)} celebs_found={total_found} errors={errors}"
    )
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Process remaining VOL00002 pages for celebrity detection")
    parser.add_argument("--min-confidence", type=float, default=90.0, help="Minimum celebrity confidence (0-100)")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit of pages to process (debug/testing)")
    args = parser.parse_args()

    raise SystemExit(main(min_confidence=args.min_confidence, limit=args.limit))


