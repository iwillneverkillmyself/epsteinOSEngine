#!/usr/bin/env python3
"""
Sync missing files from database to S3.

This script finds documents in the database that don't have corresponding files in S3,
and attempts to upload them if they exist locally, or re-download them from source.
"""
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
from tqdm import tqdm
from database import get_db
from models import Document
from config import Config

def check_s3_file_exists(s3_client, bucket: str, key: str) -> bool:
    """Check if a file exists in S3."""
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        raise

def main():
    if not Config.S3_BUCKET:
        print("ERROR: S3_BUCKET not configured")
        return 1
    
    s3_client = boto3.client('s3', region_name=Config.S3_REGION or 'us-east-1')
    
    # Get all documents from database
    with get_db() as db:
        documents = db.query(Document).all()
    
    print(f"Found {len(documents)} documents in database")
    
    missing_files = []
    for doc in tqdm(documents, desc="Checking S3"):
        # Check if file exists in S3
        file_ext = doc.file_type or 'pdf'
        s3_key = f"{Config.S3_FILES_PREFIX.rstrip('/')}/{doc.id}.{file_ext}"
        
        if not check_s3_file_exists(s3_client, Config.S3_BUCKET, s3_key):
            # Try PDF extension as fallback
            pdf_key = f"{Config.S3_FILES_PREFIX.rstrip('/')}/{doc.id}.pdf"
            if not check_s3_file_exists(s3_client, Config.S3_BUCKET, pdf_key):
                missing_files.append((doc, s3_key))
    
    print(f"\nFound {len(missing_files)} missing files in S3")
    
    if not missing_files:
        print("All files are in S3!")
        return 0
    
    # Try to upload from local storage
    uploaded = 0
    for doc, s3_key in tqdm(missing_files, desc="Uploading missing files"):
        file_ext = doc.file_type or 'pdf'
        local_path = Config.STORAGE_PATH / f"{doc.id}.{file_ext}"
        
        if not local_path.exists():
            # Try PDF extension
            local_path = Config.STORAGE_PATH / f"{doc.id}.pdf"
        
        if local_path.exists():
            try:
                s3_client.upload_file(str(local_path), Config.S3_BUCKET, s3_key)
                uploaded += 1
            except Exception as e:
                print(f"\nFailed to upload {doc.id}: {e}")
        else:
            print(f"\nFile not found locally for {doc.id} ({doc.file_name})")
            print(f"  Source URL: {doc.source_url}")
    
    print(f"\nUploaded {uploaded}/{len(missing_files)} missing files to S3")
    if uploaded < len(missing_files):
        print(f"  {len(missing_files) - uploaded} files need to be re-downloaded from source")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())


