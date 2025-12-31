# Quick Start Guide

Get the OCR RAG system up and running in 5 minutes.

## Prerequisites

- Docker and Docker Compose installed
- OR Python 3.11+ with pip

## Option 1: Docker (Recommended)

```bash
# 1. Clone/navigate to project
cd epsteingptengine

# 2. Start API server
docker-compose up -d api

# 3. Run ingestion (fetches and processes documents)
docker-compose run --rm worker

# 4. Access API
# Open browser: http://localhost:8000/docs
```

## Option 2: Python (Manual)

```bash
# 1. Install system dependencies (Ubuntu/Debian)
sudo apt-get install tesseract-ocr poppler-utils

# 2. Setup Python environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Initialize database
python scripts/init_db.py

# 4. Run ingestion
python pipeline.py

# 5. Start API
python main.py
```

## Test the System

### 1. Check Health
```bash
curl http://localhost:8000/health
```

### 2. View Stats
```bash
curl http://localhost:8000/stats
```

### 3. Search Example
```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "search_type": "keyword", "limit": 10}'
```

### 4. Interactive API Docs
Open http://localhost:8000/docs in your browser for interactive API testing.

## Next Steps

- Read [README.md](README.md) for full documentation
- See [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Check [DEPLOYMENT.md](DEPLOYMENT.md) for production deployment
- Run `python example_usage.py` for more examples

## Troubleshooting

**Port already in use?**
```bash
# Change port in .env or docker-compose.yml
API_PORT=8001
```

**OCR not working?**
```bash
# Check Tesseract installation
tesseract --version

# Or use EasyOCR (slower but more accurate)
# Set OCR_ENGINE=easyocr in .env
```

**Database errors?**
```bash
# Reinitialize database
python scripts/init_db.py
```

## Configuration

Edit `.env` file to customize:
- OCR engine (EasyOCR vs Tesseract)
- Database (SQLite vs PostgreSQL)
- Source endpoint URL
- Search options

See `.env.example` for all options.




