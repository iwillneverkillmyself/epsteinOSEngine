"""Backfill Bedrock summaries + tags for documents into Postgres.

Usage:
  python scripts/backfill_summaries_and_tags.py --limit 100
  python scripts/backfill_summaries_and_tags.py --only-missing false
  python scripts/backfill_summaries_and_tags.py --collection deleted
  python scripts/backfill_summaries_and_tags.py --collection deleted --limit 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed as a script (sys.path[0] becomes /app/scripts).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from database import init_db
from summaries.worker import backfill_documents, summarize_and_tag_document


def main():
    parser = argparse.ArgumentParser(description="Backfill AI summaries and tags")
    parser.add_argument("--document-id", type=str, default=None, help="Process a single document_id")
    parser.add_argument("--limit", type=int, default=0, help="Number of documents to process (0 = all)")
    parser.add_argument("--offset", type=int, default=0, help="Offset for pagination")
    parser.add_argument(
        "--only-missing",
        type=str,
        default="true",
        help="If true, only summarize docs without an existing succeeded summary",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Filter by collection (e.g., 'deleted') - only process documents in this collection",
    )
    args = parser.parse_args()

    only_missing = args.only_missing.lower() in ("1", "true", "yes", "y")

    init_db()
    if args.document_id:
        status, tags = summarize_and_tag_document(args.document_id)
        print(json.dumps({"document_id": args.document_id, "status": status, "tags": tags}, indent=2))
    else:
        counts = backfill_documents(limit=args.limit, offset=args.offset, only_missing=only_missing, collection=args.collection)
        print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()


