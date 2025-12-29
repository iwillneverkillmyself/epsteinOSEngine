# Examples

This directory contains example scripts demonstrating how to use the OCR RAG API.

## Image API Access Example

### `access_images_api.py`

Demonstrates how to work with images via the REST API.

**Prerequisites:**
- API server must be running (`python main.py`)
- At least some images in the database (run `python pipeline.py` or `python scripts/ingest_doj_files.py`)

**Usage:**
```bash
# Run the example script
python examples/access_images_api.py

# Or make it executable and run directly
chmod +x examples/access_images_api.py
./examples/access_images_api.py
```

**What it does:**
1. Lists all available images (first 5)
2. Downloads a full-size image
3. Downloads a thumbnail
4. Searches for text and downloads related images
5. Gets all pages for a document

**Output:**
- `./downloaded_images/` - Full images and thumbnails
- `./search_results/` - Images from search results

**Example output:**
```
================================================================================
IMAGE API ACCESS EXAMPLES
================================================================================

Make sure the API server is running:
  python main.py
  # or
  uvicorn api.main:app --reload

================================================================================

================================================================================
LISTING ALL IMAGES
================================================================================

Total images: 100
Showing: 5 (offset: 0)

Page ID: 1b433488ca0ef07d_page_0001
  Document: EFTA00000001.png
  Page: 1
  Size: 1700x2200
  OCR Processed: True
  Image URL: /images/1b433488ca0ef07d_page_0001
  Thumbnail URL: /thumbnails/1b433488ca0ef07d_page_0001

...

================================================================================
DOWNLOADING IMAGE: 1b433488ca0ef07d_page_0001
================================================================================
✓ Saved to: ./downloaded_images/1b433488ca0ef07d_page_0001.png
  Size: 342.5 KB

================================================================================
DOWNLOADING THUMBNAIL: 1b433488ca0ef07d_page_0001
================================================================================
✓ Saved to: ./downloaded_images/1b433488ca0ef07d_page_0001_thumb.png
  Size: 45.2 KB

================================================================================
SEARCHING FOR: flight
================================================================================

Found 3 results

Document: 1b433488ca0ef07d
Page: 1
Confidence: 0.95
Snippet: ...flight log showing passengers...

================================================================================
EXAMPLES COMPLETE
================================================================================

Check these directories:
  ./downloaded_images/  - Full images and thumbnails
  ./search_results/     - Images from search results
```

## API Endpoints Used

The example demonstrates these endpoints:

### List Images
```python
GET /images?limit=10&offset=0
```

Returns:
```json
{
  "images": [...],
  "count": 10,
  "total": 100,
  "offset": 0,
  "limit": 10
}
```

### Get Image
```python
GET /images/{page_id}
```

Returns: PNG image file

### Get Thumbnail
```python
GET /thumbnails/{page_id}?width=300
```

Returns: Resized PNG image

### Search
```python
GET /search?q=flight&search_type=keyword&limit=5
```

Returns:
```json
{
  "results": [...],
  "count": 3,
  "query": "flight",
  "search_type": "keyword"
}
```

### Get Document Pages
```python
GET /documents/{document_id}/pages
```

Returns:
```json
{
  "document_id": "...",
  "page_count": 15,
  "pages": [...]
}
```

## Customization

You can modify the script to:

### Change API URL
```python
API_BASE = "http://your-server:8000"
```

### List More Images
```python
list_all_images(limit=100)
```

### Download Different Thumbnail Sizes
```python
download_thumbnail(page_id, width=500)
```

### Search for Different Terms
```python
search_and_download("subpoena", limit=10)
```

## Troubleshooting

### Connection Error
```
❌ Error: Could not connect to API server
```

**Solution**: Make sure the API is running:
```bash
python main.py
# or
uvicorn api.main:app --reload
```

### No Images Found
```
⚠️  No images found. Run the ingestion pipeline first:
```

**Solution**: Ingest some documents:
```bash
# Option 1: Ingest from default source
python pipeline.py

# Option 2: Ingest DOJ files
python scripts/ingest_doj_files.py
```

### Search Returns No Results
- Make sure OCR has been run on the images
- Check that search indexes have been created
- Try a broader search term

## More Examples

Want to contribute more examples? Consider adding:

- **Batch download script**: Download all images for a document
- **Search interface**: Terminal-based search UI
- **Image viewer**: Display images in terminal or browser
- **Entity browser**: Browse detected entities
- **Celebrity search**: Find specific people in images
- **Label search**: Find images by detected objects/scenes

## API Documentation

For complete API documentation, visit:
```
http://localhost:8000/docs
```

This provides interactive Swagger UI for testing all endpoints.



