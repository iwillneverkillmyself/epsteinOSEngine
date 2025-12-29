# DOJ Epstein Files Ingestion Guide

This guide explains how to ingest files from the Department of Justice Epstein page (justice.gov/epstein) into your OCR RAG system.

## Overview

The DOJ ingestion feature:
- ✅ Crawls justice.gov/epstein for all available documents
- ✅ **Automatically excludes** "Epstein Files Transparency Act" files (already in your `data/images` folder)
- ✅ Downloads all other PDFs and images
- ✅ Processes them through **AWS Textract** for high-accuracy OCR
- ✅ Extracts entities (names, emails, phones, dates)
- ✅ Indexes all text for fast search
- ✅ Makes all images accessible via API at `/images/{page_id}`

## Prerequisites

### 1. AWS Textract Credentials

You need AWS credentials to process the documents. Add these to your `.env` file:

```bash
# AWS Textract Configuration
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_DEFAULT_REGION=us-east-1

# Set OCR engine to textract
OCR_ENGINE=textract
```

**Cost Estimate**: AWS Textract charges approximately **$1.50 per 1000 pages**. A typical DOJ document set might cost $5-20 total.

### 2. System Requirements

- Python 3.11+
- poppler-utils (for PDF conversion)
- At least 2GB free disk space for downloaded files
- Internet connection for downloading files

## Usage Methods

### Method 1: Command-Line Script (Recommended)

#### Preview Files First
```bash
# See what files would be downloaded (no actual download)
python scripts/ingest_doj_files.py --preview
```

This will show:
- Total number of files discovered
- Files grouped by section
- Confirmation that Transparency Act files are excluded

#### Run Full Ingestion
```bash
# Download and process all DOJ files
python scripts/ingest_doj_files.py
```

This will:
1. ✓ Crawl justice.gov/epstein
2. ✓ Download all files (excluding Transparency Act)
3. ✓ Convert PDFs to images
4. ✓ Run AWS Textract OCR on all pages
5. ✓ Extract entities
6. ✓ Index for search
7. ✓ Store images in `data/images/`

**Options:**
- `--skip-existing` (default): Skip files already in database
- `--no-skip-existing`: Re-process all files even if already in database

### Method 2: API Endpoint

#### Preview via API
```bash
curl "http://localhost:8000/ingest/doj/preview"
```

Response:
```json
{
  "total_files": 45,
  "sections": {
    "General Documents": [...],
    "Court Filings": [...],
    ...
  },
  "note": "Files from 'Epstein Files Transparency Act' are excluded"
}
```

#### Trigger Ingestion via API
```bash
# Run in foreground (wait for completion)
curl -X POST "http://localhost:8000/ingest/doj?background=false"

# OR run in background (returns immediately)
curl -X POST "http://localhost:8000/ingest/doj?background=true"
```

Response:
```json
{
  "status": "completed",
  "files_discovered": 45,
  "files_downloaded": 45,
  "files_processed": 43,
  "message": "Successfully processed 43/45 files",
  "errors": null
}
```

## What Happens During Ingestion?

### Step-by-Step Process

1. **Discovery Phase**
   - Scrapes justice.gov/epstein HTML
   - Identifies all PDF and image links
   - Filters out "Epstein Files Transparency Act" files
   - Logs all sections found

2. **Download Phase**
   - Downloads each file to `data/storage/doj_temp/`
   - Verifies successful download
   - Tracks file metadata (size, type, section)

3. **Processing Phase**
   - Stores document in database
   - Converts PDFs to high-res PNG images
   - Stores each page image in `data/images/`
   - Assigns unique page IDs like `{doc_id}_page_0001`

4. **OCR Phase**
   - Sends each page image to AWS Textract
   - Extracts text with word-level bounding boxes
   - Records confidence scores
   - Stores OCR results in database

5. **Entity Detection Phase**
   - Scans extracted text for entities:
     - Names (people)
     - Email addresses
     - Phone numbers
     - Dates
   - Stores entities with references to source text

6. **Indexing Phase**
   - Tokenizes text for search
   - Creates full-text search indexes
   - Enables fuzzy and phrase search

## Accessing Ingested Files

### Search Text Content
```bash
# Keyword search
curl "http://localhost:8000/search?q=subpoena&search_type=keyword"

# Entity search
curl "http://localhost:8000/search/entity?entity_type=name&entity_value=John"
```

### View Images
```bash
# List all images
curl "http://localhost:8000/images?limit=100"

# Get specific image
curl "http://localhost:8000/images/{page_id}" -o image.png

# Get thumbnail
curl "http://localhost:8000/thumbnails/{page_id}?width=300" -o thumb.png
```

### Download Original Files
```bash
# Get original PDF
curl "http://localhost:8000/files/{document_id}" -o document.pdf
```

### Search Files
```bash
# Search by filename or content
curl "http://localhost:8000/search/files?q=motion"

# List all files
curl "http://localhost:8000/files"
```

## Why Are Transparency Act Files Excluded?

The "Epstein Files Transparency Act" section under "DOJ Disclosures" contains **images that are already in your `data/images/` folder**. These files:

- Are typically named `EFTA00000001.png`, `EFTA00000002.png`, etc.
- Have already been processed through OCR
- Are already searchable in your system

Re-downloading and processing them would:
- Waste AWS Textract credits
- Duplicate content in your database
- Slow down the ingestion process

The crawler automatically detects and excludes these files based on:
- Section name: "DOJ Disclosures" or similar
- Link text containing: "Transparency Act" or "EFTA"
- URL patterns

## Monitoring Progress

### Via Logs
When running the script, you'll see detailed progress:
```
2024-01-15 10:30:45 - INFO - Starting DOJ file ingestion...
2024-01-15 10:30:46 - INFO - Discovered 45 DOJ Epstein files (excluding Transparency Act)
2024-01-15 10:30:47 - INFO - Sections found: General Documents, Court Filings, Reports
2024-01-15 10:31:00 - INFO - Downloaded motion_to_unseal.pdf (1.2 MB)
2024-01-15 10:31:05 - INFO - Processing: motion_to_unseal.pdf
2024-01-15 10:31:06 - INFO -   Converting PDF to images...
2024-01-15 10:31:08 - INFO -   Generated 15 page images
2024-01-15 10:31:10 - INFO -   Running AWS Textract OCR...
2024-01-15 10:32:30 - INFO -   Processed 15 pages with OCR
2024-01-15 10:32:35 - INFO -   Extracting entities...
2024-01-15 10:32:40 - INFO -   Indexing for search...
2024-01-15 10:32:42 - INFO -   ✓ Successfully processed motion_to_unseal.pdf
...
2024-01-15 11:00:00 - INFO - INGESTION COMPLETE
2024-01-15 11:00:00 - INFO - Files downloaded:    45
2024-01-15 11:00:00 - INFO - Files processed:     43
2024-01-15 11:00:00 - INFO - Files skipped:       0
2024-01-15 11:00:00 - INFO - Files failed:        2
```

### Via API Stats
```bash
curl "http://localhost:8000/stats"
```

Response:
```json
{
  "documents": 143,
  "image_pages": 542,
  "ocr_texts": 542,
  "entities": 1847,
  "search_index_rows": 542,
  "processed_pages": 542,
  "processing_rate": "542/542"
}
```

## Troubleshooting

### "AWS credentials not configured"
**Solution**: Add AWS credentials to your `.env` file:
```bash
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
```

### "No files discovered from DOJ website"
**Possible causes**:
- justice.gov/epstein page structure changed
- Network/firewall blocking access
- Website temporarily down

**Solution**: Try the preview first to see what's happening:
```bash
python scripts/ingest_doj_files.py --preview
```

### "Error processing file: PDF conversion failed"
**Solution**: Ensure poppler-utils is installed:
```bash
# Ubuntu/Debian
sudo apt-get install poppler-utils

# macOS
brew install poppler

# Check installation
pdftoppm -v
```

### "Textract API error: Throttling"
**Cause**: AWS rate limits being hit (too many requests too fast)

**Solution**: The script already has rate limiting built in, but if you're hitting limits:
- Wait a few minutes and retry
- Process files in smaller batches
- Contact AWS to increase your Textract rate limits

### Files are processed but not appearing in search
**Solution**: Check that indexing completed successfully:
```bash
# Re-index all documents
python -c "
from search.indexer import SearchIndexer
from database import get_db
from models import Document

indexer = SearchIndexer()
with get_db() as db:
    docs = db.query(Document).all()
    for doc in docs:
        indexer.index_document(doc.id)
"
```

## Cost Optimization

### Preview First
Always run `--preview` first to see how many files you'll be processing:
```bash
python scripts/ingest_doj_files.py --preview
```

### Skip Existing Files
Use `--skip-existing` (default) to avoid reprocessing:
```bash
python scripts/ingest_doj_files.py --skip-existing
```

### Estimate Costs
- AWS Textract: **$1.50 per 1000 pages**
- Typical DOJ document: 10-50 pages
- Total estimate: Count pages from preview × $0.0015

Example:
- 45 documents × 20 pages average = 900 pages
- Cost: 900 × $0.0015 = **$1.35**

## Advanced Usage

### Process Specific Documents
If you want to process only specific files, you can modify the crawler to filter by filename:

```python
# In doj_crawler.py, add to _should_exclude method:
wanted_files = ['motion_to_unseal.pdf', 'court_order.pdf']
if file_info['filename'] not in wanted_files:
    return True  # Exclude
```

### Custom Download Location
Modify the temp directory in the script:
```python
temp_dir = Path("/custom/path/doj_downloads")
```

### Batch Processing
For very large document sets, process in batches:
1. Run preview to get file list
2. Modify crawler to process first N files
3. Run ingestion multiple times

## Integration with Existing Images

The system automatically manages all images in `data/images/`:

### Existing Images (Transparency Act)
- Already in `data/images/` as PNG files
- Already processed and searchable
- Accessible via `/images/{page_id}`

### New Images (DOJ Downloads)
- Downloaded and converted from PDFs
- Stored in `data/images/` alongside existing ones
- Processed through AWS Textract
- Accessible via `/images/{page_id}`

All images are treated equally by the API:
```bash
# Works for both existing and new images
curl "http://localhost:8000/images/1b433488ca0ef07d_page_0001"
curl "http://localhost:8000/images/doj_doc_abc123_page_0005"
```

## Next Steps

After ingestion, you can:

1. **Search the documents**:
   ```bash
   curl "http://localhost:8000/search?q=flight+log"
   ```

2. **Find specific entities**:
   ```bash
   curl "http://localhost:8000/search/entity?entity_type=name&entity_value=Clinton"
   ```

3. **View document images**:
   ```bash
   curl "http://localhost:8000/images?limit=100"
   ```

4. **Build a frontend**: Use the API to create a web interface for browsing and searching

5. **Export data**: Query the database directly for custom analysis

## Support

For issues or questions:
- Check logs in console output
- Review API documentation: http://localhost:8000/docs
- Check database stats: `curl http://localhost:8000/stats`

## Summary

The DOJ ingestion feature provides a complete, automated pipeline for ingesting legal documents from justice.gov/epstein:

✅ Smart filtering (excludes duplicates)  
✅ High-accuracy OCR (AWS Textract)  
✅ Entity extraction  
✅ Full-text search indexing  
✅ API access to all images and documents  
✅ Cost-effective (~$1-5 per batch)  
✅ Idempotent (safe to run multiple times)  

Happy searching! 🔍



