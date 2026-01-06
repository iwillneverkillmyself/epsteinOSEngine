#!/usr/bin/env python3
"""
Ingest deleted/removed files from an S3 "incoming" prefix into the main system.

Why this exists:
  - Your production RDS is VPC-only, so you can't insert rows from your laptop.
  - You can upload the 9 local files to S3, then run this script as an ECS one-off task.

What it does (store-only by default):
  - Lists objects under s3://bucket/prefix
  - Downloads each file to a temp directory
  - Creates Document rows with collection="deleted"
  - Uploads the canonical files to S3 under Config.S3_FILES_PREFIX (e.g. files/<doc_id>.pdf)
  - Creates ImagePage rows (and uploads page PNGs to S3 under Config.S3_IMAGES_PREFIX)

It does NOT run OCR, entity extraction, or search indexing.

Usage (inside ECS task):
  python scripts/ingest_deleted_from_s3.py --bucket epsteingptengine-assets-... --prefix incoming/deleted/
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import Config
from database import init_db
from ingestion.pdf_converter import is_pdf, pdf_to_images
from ingestion.storage import DocumentStorage


logger = logging.getLogger(__name__)


def _s3_client():
    import boto3

    return boto3.client("s3", region_name=Config.S3_REGION or os.getenv("AWS_REGION") or "us-east-1")


def list_s3_files(bucket: str, prefix: str) -> List[Dict]:
    s3 = _s3_client()
    paginator = s3.get_paginator("list_objects_v2")

    out: List[Dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj.get("Key")
            size = int(obj.get("Size") or 0)
            if not key or key.endswith("/"):
                continue
            out.append({"key": key, "size": size})
    return out


def download_s3_file(bucket: str, key: str, dest_dir: Path) -> Path:
    s3 = _s3_client()
    dest_dir.mkdir(parents=True, exist_ok=True)
    local_path = dest_dir / Path(key).name
    s3.download_file(bucket, key, str(local_path))
    return local_path


def _file_type_from_path(p: Path) -> str:
    ext = p.suffix.lower().lstrip(".")
    return ext or "unknown"


def ingest(bucket: str, prefix: str, *, collection: str = "deleted", skip_existing: bool = True) -> Dict[str, int]:
    init_db()

    if not Config.S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be set in the task environment (Config.S3_BUCKET is empty)")

    storage = DocumentStorage()

    files = list_s3_files(bucket, prefix)
    logger.info(f"Discovered {len(files)} S3 objects under s3://{bucket}/{prefix}")

    temp_root = Path(os.getenv("TMPDIR") or "/tmp") / "deleted_ingest_s3"
    temp_root.mkdir(parents=True, exist_ok=True)

    counts = {"discovered": len(files), "processed": 0, "skipped": 0, "failed": 0}

    for f in files:
        key = f["key"]
        try:
            local_path = download_s3_file(bucket, key, temp_root)
            file_info = {
                "url": f"s3://{bucket}/{key}",
                "filename": local_path.name,
                "file_type": _file_type_from_path(local_path),
                "local_path": str(local_path),
                "file_size": local_path.stat().st_size,
                "source": "s3_deleted_ingest",
                "s3_incoming_bucket": bucket,
                "s3_incoming_key": key,
            }

            doc_id, is_new = storage.store_document(file_info, collection=collection)
            if (not is_new) and skip_existing:
                counts["skipped"] += 1
                continue

            # Convert/store pages for UI previews (no OCR).
            if is_pdf(local_path):
                images_dir = temp_root / f"{doc_id}_images"
                image_paths = pdf_to_images(local_path, images_dir)
            else:
                image_paths = [local_path]

            from PIL import Image

            for page_num, image_path in enumerate(image_paths, start=1):
                img = Image.open(image_path)
                width, height = img.size
                storage.store_image_page(doc_id, page_num, image_path, width, height)

            counts["processed"] += 1
        except Exception as e:
            logger.exception(f"Failed ingest for s3://{bucket}/{key}: {e}")
            counts["failed"] += 1

    return counts


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    parser = argparse.ArgumentParser(description="Ingest deleted files from S3 prefix (store-only)")
    parser.add_argument("--bucket", required=True, help="S3 bucket containing incoming deleted files")
    parser.add_argument("--prefix", required=True, help="S3 prefix containing incoming deleted files")
    parser.add_argument("--collection", default="deleted", help="Document.collection value (default: deleted)")
    parser.add_argument(
        "--skip-existing",
        type=str,
        default="true",
        help="Skip documents already in DB (default: true)",
    )
    args = parser.parse_args()

    skip_existing = args.skip_existing.lower() in ("1", "true", "yes", "y")
    counts = ingest(args.bucket, args.prefix, collection=args.collection, skip_existing=skip_existing)
    print(counts)


if __name__ == "__main__":
    main()


