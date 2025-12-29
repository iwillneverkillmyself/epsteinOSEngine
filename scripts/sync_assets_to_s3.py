#!/usr/bin/env python3
"""
Upload local assets to S3 so ECS/Fargate can serve /files and /images.

Defaults assume:
  - PDFs live in: data/storage/{document_id}.{ext}
  - Page images live in: data/images/{page_id}.png (or .jpg)
  - S3 keys:
      {S3_FILES_PREFIX}/{filename}
      {S3_IMAGES_PREFIX}/{filename}

Usage:
  python scripts/sync_assets_to_s3.py --bucket YOUR_BUCKET --region us-east-1
  python scripts/sync_assets_to_s3.py --bucket YOUR_BUCKET --dry-run
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3
from tqdm import tqdm


def iter_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync local assets to S3")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
    parser.add_argument("--files-dir", default="data/storage", help="Local files directory")
    parser.add_argument("--images-dir", default="data/images", help="Local images directory")
    parser.add_argument("--files-prefix", default=os.getenv("S3_FILES_PREFIX", "files"), help="S3 prefix for files")
    parser.add_argument("--images-prefix", default=os.getenv("S3_IMAGES_PREFIX", "images"), help="S3 prefix for images")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=args.region)

    files_dir = Path(args.files_dir)
    images_dir = Path(args.images_dir)

    to_upload = []
    if files_dir.exists():
        for p in iter_files(files_dir):
            key = f"{args.files_prefix.rstrip('/')}/{p.name}"
            to_upload.append((p, key))
    if images_dir.exists():
        for p in iter_files(images_dir):
            key = f"{args.images_prefix.rstrip('/')}/{p.name}"
            to_upload.append((p, key))

    if not to_upload:
        print("No assets found to upload.")
        return 0

    for p, key in tqdm(to_upload, desc="Uploading"):
        if args.dry_run:
            continue
        s3.upload_file(str(p), args.bucket, key)

    print(f"Done. Uploaded {len(to_upload)} objects to s3://{args.bucket}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




