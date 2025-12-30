#!/usr/bin/env python3
"""Check processing status: documents with/without summaries, OCR, etc."""

import sys
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from database import get_db, init_db
from models import Document, DocumentSummary, OCRText, ImagePage

def main():
    init_db()
    
    with get_db() as db:
        # Total documents
        total_docs = db.query(Document).count()
        
        # Documents with summaries (status=succeeded)
        docs_with_summaries = db.query(DocumentSummary).filter(
            DocumentSummary.status == "succeeded"
        ).count()
        
        # Documents with any summary row (including failed/pending)
        docs_with_summary_row = db.query(DocumentSummary).count()
        
        # Documents without summaries
        docs_without_summaries = total_docs - docs_with_summary_row
        
        # Documents with OCR text
        docs_with_ocr = db.query(OCRText.document_id).distinct().count()
        
        # Documents without OCR text
        docs_without_ocr = total_docs - docs_with_ocr
        
        # Documents with image pages
        docs_with_pages = db.query(ImagePage.document_id).distinct().count()
        
        # Documents pending/failed summaries
        docs_pending_summaries = db.query(DocumentSummary).filter(
            DocumentSummary.status.in_(["pending", "failed"])
        ).count()
        
        print("=" * 60)
        print("DOCUMENT PROCESSING STATUS")
        print("=" * 60)
        print(f"Total documents: {total_docs:,}")
        print()
        print("OCR Processing:")
        print(f"  - Documents with OCR text: {docs_with_ocr:,}")
        print(f"  - Documents without OCR text: {docs_without_ocr:,}")
        print(f"  - OCR completion: {docs_with_ocr/total_docs*100:.1f}%" if total_docs > 0 else "  - OCR completion: N/A")
        print()
        print("Summary Processing:")
        print(f"  - Documents with successful summaries: {docs_with_summaries:,}")
        print(f"  - Documents with pending/failed summaries: {docs_pending_summaries:,}")
        print(f"  - Documents without summaries: {docs_without_summaries:,}")
        print(f"  - Summary completion: {docs_with_summaries/total_docs*100:.1f}%" if total_docs > 0 else "  - Summary completion: N/A")
        print()
        print("Image Pages:")
        print(f"  - Documents with image pages: {docs_with_pages:,}")
        print()
        print("=" * 60)
        print(f"APPROXIMATELY {docs_without_summaries:,} documents still need summaries")
        print(f"APPROXIMATELY {docs_without_ocr:,} documents still need OCR processing")
        print("=" * 60)

if __name__ == "__main__":
    main()

