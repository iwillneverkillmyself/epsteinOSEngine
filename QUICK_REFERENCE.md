# Quick Reference Guide

## 🚀 Images API (All 100+ existing images are accessible!)

### List All Images
```bash
curl "http://localhost:8000/images?limit=100"
```

### Get Specific Image
```bash
curl "http://localhost:8000/images/1b433488ca0ef07d_page_0001" -o image.png
```

### Get Thumbnail
```bash
curl "http://localhost:8000/thumbnails/1b433488ca0ef07d_page_0001?width=300" -o thumb.png
```

### List Images in Python
```python
import requests
response = requests.get("http://localhost:8000/images?limit=100")
images = response.json()['images']
for img in images:
    print(f"{img['page_id']}: {img['file_name']}")
```

---

## 📥 DOJ File Ingestion

### Step 1: Add AWS Credentials to `.env`
```bash
AWS_ACCESS_KEY_ID=your_access_key_here
AWS_SECRET_ACCESS_KEY=your_secret_key_here
AWS_DEFAULT_REGION=us-east-1
OCR_ENGINE=textract
```

### Step 2: Preview Files (No Download)
```bash
python scripts/ingest_doj_files.py --preview
```

### Step 3: Download & Process Everything
```bash
python scripts/ingest_doj_files.py
```

**Or via API:**
```bash
# Preview
curl "http://localhost:8000/ingest/doj/preview"

# Ingest (background)
curl -X POST "http://localhost:8000/ingest/doj?background=true"
```

---

## 🔍 Search Examples

### Keyword Search
```bash
curl "http://localhost:8000/search?q=flight+log&search_type=keyword"
```

### Entity Search (Find Names)
```bash
curl "http://localhost:8000/search/entity?entity_type=name&entity_value=Clinton"
```

### File Search
```bash
curl "http://localhost:8000/search/files?q=motion"
```

### Label Search (Rekognition)
```bash
curl "http://localhost:8000/search/labels?q=Document&min_confidence=80"
```

### Celebrity Search
```bash
curl "http://localhost:8000/search/celebrities?q=Bill+Clinton&min_confidence=90"
```

---

## 📊 System Stats

### Get Overview
```bash
curl "http://localhost:8000/stats"
```

### List Common Entities
```bash
curl "http://localhost:8000/suggest/entities?entity_type=name&limit=50"
```

### List Common Labels
```bash
curl "http://localhost:8000/suggest/labels?limit=50"
```

### List All Celebrities
```bash
curl "http://localhost:8000/celebrities"
```

---

## 📁 File Management

### List All Files
```bash
curl "http://localhost:8000/files?limit=50"
```

### Get Original File (PDF)
```bash
curl "http://localhost:8000/files/{document_id}" -o document.pdf
```

### Get All Pages of Document
```bash
curl "http://localhost:8000/documents/{document_id}/pages"
```

---

## 🎯 Common Workflows

### Workflow 1: Browse Images
```bash
# List all images
curl "http://localhost:8000/images" | jq '.images[].page_id'

# Download specific image
curl "http://localhost:8000/images/1b433488ca0ef07d_page_0001" -o page.png
```

### Workflow 2: Search and View
```bash
# Search for text
curl "http://localhost:8000/search?q=subpoena" | jq '.results[0]'

# Get the image for that result
curl "http://localhost:8000/images/{page_id_from_result}" -o result.png
```

### Workflow 3: Ingest DOJ Files
```bash
# 1. Preview what you'll get
python scripts/ingest_doj_files.py --preview

# 2. Run ingestion
python scripts/ingest_doj_files.py

# 3. Search the new files
curl "http://localhost:8000/search?q=court+order"
```

### Workflow 4: Find Person Across All Documents
```bash
# Search by name entity
curl "http://localhost:8000/search/entity?entity_type=name&entity_value=John" | jq '.results[]'

# Or use celebrity search (if AWS Rekognition enabled)
curl "http://localhost:8000/search/celebrities?q=John+Doe"
```

### Workflow 5: Batch Download Images
```python
import requests

# Get all images
response = requests.get("http://localhost:8000/images?limit=1000")
images = response.json()['images']

# Download each
for img in images:
    page_id = img['page_id']
    response = requests.get(f"http://localhost:8000/images/{page_id}")
    with open(f"downloads/{page_id}.png", 'wb') as f:
        f.write(response.content)
    print(f"Downloaded {page_id}")
```

---

## 🛠️ Troubleshooting

### API Not Running
```bash
# Start the API
python main.py
# or
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### No Images Found
```bash
# Check if images exist
ls data/images/*.png

# If empty, run ingestion
python pipeline.py
```

### AWS Credentials Error
```bash
# Check .env file
cat .env | grep AWS

# Set manually if needed
export AWS_ACCESS_KEY_ID="your_key"
export AWS_SECRET_ACCESS_KEY="your_secret"
```

### Database Not Initialized
```bash
python -c "from database import init_db; init_db()"
```

---

## 📚 Documentation Files

- **`README.md`** - Main project documentation
- **`DOJ_INGESTION_GUIDE.md`** - Complete DOJ ingestion guide
- **`IMPLEMENTATION_SUMMARY.md`** - Technical implementation details
- **`QUICK_REFERENCE.md`** - This file
- **`examples/README.md`** - Example scripts documentation
- **`DEPLOYMENT.md`** - Deployment guide
- **`ARCHITECTURE.md`** - System architecture

---

## 🌐 API Documentation

Interactive API docs (Swagger UI):
```
http://localhost:8000/docs
```

Alternative API docs (ReDoc):
```
http://localhost:8000/redoc
```

---

## 💡 Pro Tips

1. **Use `jq` for JSON parsing**:
   ```bash
   curl "http://localhost:8000/search?q=test" | jq '.results[].snippet'
   ```

2. **Batch operations with xargs**:
   ```bash
   curl "http://localhost:8000/images" | jq -r '.images[].page_id' | \
     xargs -I {} curl "http://localhost:8000/images/{}" -o "images/{}.png"
   ```

3. **Preview before ingesting**:
   ```bash
   # Always preview DOJ files first to estimate costs
   python scripts/ingest_doj_files.py --preview
   ```

4. **Use background mode for long operations**:
   ```bash
   curl -X POST "http://localhost:8000/ingest/doj?background=true"
   # Then check logs for progress
   ```

5. **Get stats regularly**:
   ```bash
   # Monitor your database size
   watch -n 5 'curl -s http://localhost:8000/stats | jq'
   ```

---

## ✅ Quick Checklist

- [ ] API server running (`python main.py`)
- [ ] Database initialized (`python -c "from database import init_db; init_db()"`)
- [ ] Images exist (`ls data/images/*.png`)
- [ ] AWS credentials configured (for DOJ ingestion)
- [ ] Can access images API (`curl http://localhost:8000/images`)
- [ ] Can search (`curl "http://localhost:8000/search?q=test"`)

---

## 🎉 You're All Set!

**All 100+ existing images are now accessible via the Images API!**

**DOJ files can be ingested automatically with smart filtering!**

Start exploring:
```bash
# List images
curl "http://localhost:8000/images" | jq

# Search
curl "http://localhost:8000/search?q=epstein" | jq

# Get stats
curl "http://localhost:8000/stats" | jq
```

For more details, see the comprehensive guides in:
- `DOJ_INGESTION_GUIDE.md`
- `IMPLEMENTATION_SUMMARY.md`
- `README.md`



