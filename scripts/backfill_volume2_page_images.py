#!/usr/bin/env python3
"""
Backfill missing host image files for Volume 2 pages.

Problem this solves:
- Many ImagePage.image_path values were written from inside Docker (e.g. /app/...),
  and the referenced PNGs don't exist on the host.
- The API (and your website thumbnails) need the PNGs to exist locally.

What this does:
- Finds ImagePage rows whose Document.source_url contains VOL00002
- For each page where the current image_path doesn't exist on the host,
  renders the correct PDF page from ./data/storage/{document_id}.pdf
  into ./data/images/{page_id}.png
- Updates ImagePage.image_path (and width/height) to the new host path.

No AWS calls are made.
"""

import sys
from pathlib import Path
from typing import Optional, Tuple

sys.path.append(str(Path(__file__).parent.parent))

import logging
from tqdm import tqdm
from sqlalchemy import func, select

from config import Config
from database import init_db, get_db
from models import Document, ImagePage

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


def _pdf_path_for_document(document_id: str) -> Optional[Path]:
    p = Config.STORAGE_PATH / f"{document_id}.pdf"
    if p.exists():
        return p
    for ext in (".pdf", ".png", ".jpg", ".jpeg"):
        alt = Config.STORAGE_PATH / f"{document_id}{ext}"
        if alt.exists():
            return alt
    return None


def _render_pdf_page_to_png(pdf_path: Path, page_number: int, out_path: Path) -> Tuple[int, int]:
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


def main(limit: int | None = None) -> int:
    init_db()

    with get_db() as db:
        vol2_doc_ids = select(Document.id).where(func.lower(Document.source_url).contains("vol00002"))
        # IMPORTANT: do not keep ORM objects after session closes (DetachedInstanceError).
        # Fetch primitive fields only.
        q = (
            db.query(
                ImagePage.id,
                ImagePage.document_id,
                ImagePage.page_number,
                ImagePage.image_path,
            )
            .filter(ImagePage.document_id.in_(vol2_doc_ids))
            .order_by(ImagePage.id.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        pages = q.all()

    logger.info(f"Volume 2 total pages: {len(pages)}")
    if not pages:
        return 0

    fixed = 0
    skipped = 0
    errors = 0

    for (page_id, doc_id, page_number, image_path) in tqdm(pages, desc="Backfill VOL00002 images"):
        current = Path(image_path) if image_path else None
        target = Config.IMAGES_PATH / f"{page_id}.png"

        # If current path exists, nothing to do
        if current and current.exists():
            skipped += 1
            continue

        # If we already rendered this target, just repoint
        if target.exists():
            with get_db() as db:
                row = db.query(ImagePage).filter(ImagePage.id == page_id).first()
                if row:
                    row.image_path = str(target)
                    db.commit()
            fixed += 1
            continue

        pdf_path = _pdf_path_for_document(doc_id)
        if not pdf_path:
            errors += 1
            logger.error(f"Missing stored PDF for doc_id={doc_id} (page_id={page_id})")
            continue

        try:
            w, h = _render_pdf_page_to_png(pdf_path, page_number, target)
            with get_db() as db:
                row = db.query(ImagePage).filter(ImagePage.id == page_id).first()
                if row:
                    row.image_path = str(target)
                    if w and h:
                        row.width = w
                        row.height = h
                    db.commit()
            fixed += 1
        except Exception as e:
            errors += 1
            logger.error(f"Failed rendering {page_id} from {pdf_path.name}: {e}")

    logger.info(f"Done. fixed={fixed} skipped={skipped} errors={errors}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill missing Volume 2 page images into ./data/images")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for testing")
    args = parser.parse_args()
    raise SystemExit(main(limit=args.limit))


