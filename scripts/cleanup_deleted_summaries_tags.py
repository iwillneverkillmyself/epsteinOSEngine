#!/usr/bin/env python3
"""
Delete summaries + tags for documents in collection="deleted".

Rationale:
  Deleted files are intended to be storage-only (no summarization/tagging).
  This script removes any existing DocumentSummary/DocumentTag rows for those docs.

Usage:
  python scripts/cleanup_deleted_summaries_tags.py
  python scripts/cleanup_deleted_summaries_tags.py --dry-run false
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed as a script
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from database import init_db, get_db
from models import Document, DocumentSummary, DocumentTag


def main():
    parser = argparse.ArgumentParser(description="Cleanup deleted docs summaries/tags")
    parser.add_argument(
        "--dry-run",
        type=str,
        default="true",
        help="If true, only report what would be deleted (default: true)",
    )
    args = parser.parse_args()
    dry_run = args.dry_run.lower() in ("1", "true", "yes", "y")

    init_db()

    with get_db() as db:
        deleted_ids = [r[0] for r in db.query(Document.id).filter(Document.collection == "deleted").all()]
        if not deleted_ids:
            print(json.dumps({"deleted_docs": 0, "summaries_deleted": 0, "tags_deleted": 0}, indent=2))
            return

        summaries_q = db.query(DocumentSummary).filter(DocumentSummary.document_id.in_(deleted_ids))
        tags_q = db.query(DocumentTag).filter(DocumentTag.document_id.in_(deleted_ids))

        summaries_count = summaries_q.count()
        tags_count = tags_q.count()

        if not dry_run:
            summaries_q.delete(synchronize_session=False)
            tags_q.delete(synchronize_session=False)

        print(
            json.dumps(
                {
                    "deleted_docs": len(deleted_ids),
                    "summaries_deleted": 0 if dry_run else summaries_count,
                    "tags_deleted": 0 if dry_run else tags_count,
                    "summaries_present": summaries_count,
                    "tags_present": tags_count,
                    "dry_run": dry_run,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()


