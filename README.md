# OCR-Centric RAG Architecture

A production-ready OCR-centric RAG (Retrieval-Augmented Generation) system designed to ingest, process, and search image-heavy documents. The system extracts text from images using OCR, detects entities (names, emails, phones, dates), and provides powerful search capabilities.

## 🎯 Core Features

- **Image-First Ingestion**: Crawls and fetches documents from public endpoints
- **DOJ Epstein Files Integration**: Specialized crawler for justice.gov/epstein with smart filtering
- **AWS Textract OCR**: Industry-leading OCR with high accuracy for legal documents
- **State-of-the-Art OCR**: **PaddleOCR (PP-OCRv4)** with angle classification, deskewing, and multi-pass preprocessing for maximum accuracy on noisy scans
- **Multiple OCR Engines**: AWS Textract (recommended), PaddleOCR, EasyOCR (handwritten), Tesseract (legacy)
- **Entity Detection**: Automatically detects names, emails, phone numbers, and dates
- **Multi-Modal Search**: Keyword, fuzzy, phrase, and optional semantic search
- **Image Serving**: All images accessible via REST API endpoints
- **Positional Metadata**: Preserves word-level bounding boxes + confidence scores for highlighting
- **Production-Ready**: Dockerized, self-hostable, persistent storage

## 📋 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Source Endpoint                               │
│         https://epstein-files.rhys-669.workers.dev              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Ingestion Layer                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Crawler    │→ │ PDF Converter│→ │   Storage    │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OCR Processing Layer                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Preprocessing│→ │ OCR Engine   │→ │ BBox Extract │        │
│  │ (deskew,     │  │ (PaddleOCR/  │  │ + confidence │        │
│  │  denoise)    │  │  EasyOCR)    │  │              │        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Text Processing Layer                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Normalizer   │→ │ Entity       │→ │ Entity       │        │
│  │              │  │ Detector     │  │ Storage      │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Indexing Layer                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Full-Text    │  │ Semantic     │  │ Entity       │        │
│  │ Index        │  │ Index        │  │ Index        │        │
│  │ (SQLite/     │  │ (ChromaDB)   │  │ (PostgreSQL) │        │
│  │  PostgreSQL) │  │              │  │              │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Search API Layer                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Keyword      │  │ Fuzzy        │  │ Entity       │        │
│  │ Search       │  │ Search       │  │ Search       │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│  ┌──────────────┐  ┌──────────────┐                           │
│  │ Phrase       │  │ Semantic     │                           │
│  │ Search       │  │ Search       │                           │
│  └──────────────┘  └──────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

## 🏗️ System Components

### 1. Ingestion Pipeline (`ingestion/`)
- **Crawler**: Discovers and fetches files from source endpoint
- **PDF Converter**: Converts PDF pages to images
- **Storage**: Manages document and image storage with metadata

### 2. OCR Engine (`ocr/`)
- **PaddleOCR Engine** (recommended): PP-OCRv4 models with text detection, angle classification, and recognition
- **EasyOCR Engine**: Good for handwritten text
- **Tesseract Engine**: Fast, traditional OCR (fallback)
- **Ensemble Engine**: Combines multiple engines for best results
- **Preprocessing**: Deskewing, denoising, CLAHE contrast normalization, adaptive thresholding
- **Processor**: Orchestrates OCR with bounding box extraction

### 3. Text Processing (`processing/`)
- **Normalizer**: Cleans and normalizes OCR output
- **Entity Detector**: Detects names, emails, phones, dates
- **Text Processor**: Main processing pipeline

### 4. Search & Indexing (`search/`)
- **Indexer**: Creates searchable indexes
- **Searcher**: Implements multiple search strategies

### 5. API (`api/`)
- **FastAPI Application**: RESTful search API
- **Search Endpoints**: Keyword, fuzzy, phrase, entity, semantic search

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker and Docker Compose (for containerized deployment)
- Tesseract OCR (if using Tesseract engine)
- poppler-utils (for PDF conversion)

### Installation

1. **Clone and setup**:
```bash
cd epsteingptengine
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. **Configure environment**:
```bash
cp .env.example .env
# Edit .env with your settings
```

3. **Initialize database**:
```bash
python -c "from database import init_db; init_db()"
```

4. **Run ingestion pipeline**:
```bash
python pipeline.py
```

4a. **Ingest DOJ Epstein Files** (optional):
```bash
# Preview what files would be downloaded
python scripts/ingest_doj_files.py --preview

# Download and process DOJ files (excludes Transparency Act files already in images)
python scripts/ingest_doj_files.py
```

5. **Start API server**:
```bash
python main.py
# Or: uvicorn api.main:app --reload
```

### Docker Deployment

1. **Build and start services**:
```bash
docker-compose up -d
```

2. **Run ingestion**:
```bash
docker-compose run --rm worker
```

3. **Access API**:
- API: http://localhost:8000
- Docs: http://localhost:8000/docs

## 📖 API Usage

### Search Endpoints

#### Keyword Search
```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "example text",
    "search_type": "keyword",
    "limit": 50
  }'
```

#### Fuzzy Search
```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "exampl text",
    "search_type": "fuzzy",
    "fuzzy_threshold": 0.6,
    "limit": 50
  }'
```

#### Entity Search
```bash
curl -X GET "http://localhost:8000/search/entity?entity_type=name&entity_value=John&limit=50"
```

#### Phrase Search
```bash
curl -X GET "http://localhost:8000/search?q=exact%20phrase&search_type=phrase&limit=50"
```

### Response Format

```json
{
  "results": [
    {
      "ocr_text_id": "uuid",
      "document_id": "doc_id",
      "page_number": 1,
      "snippet": "...context around match...",
      "full_text": "Full extracted text...",
      "confidence": 0.95,
      "image_path": "/path/to/image.png",
      "bbox": {
        "x": 100.0,
        "y": 200.0,
        "width": 300.0,
        "height": 50.0
      },
      "word_boxes": [
        {
          "text": "word",
          "x": 100.0,
          "y": 200.0,
          "width": 50.0,
          "height": 20.0,
          "confidence": 0.95
        }
      ]
    }
  ],
  "count": 1,
  "query": "example text",
  "search_type": "keyword"
}
```

### Image Serving Endpoints

#### Get Image
```bash
# Get full-size image by page ID
curl "http://localhost:8000/images/{page_id}" -o image.png

# Example
curl "http://localhost:8000/images/1b433488ca0ef07d_page_0001" -o page.png
```

#### Get Thumbnail
```bash
# Get resized thumbnail
curl "http://localhost:8000/thumbnails/{page_id}?width=300" -o thumbnail.png
```

#### List All Images
```bash
# List all available images
curl "http://localhost:8000/images?limit=100&offset=0"
```

### DOJ File Ingestion Endpoints

#### Preview DOJ Files
```bash
# Preview what files would be downloaded (no actual download)
curl "http://localhost:8000/ingest/doj/preview"
```

#### Ingest DOJ Files
```bash
# Download and process DOJ Epstein files via API
curl -X POST "http://localhost:8000/ingest/doj?background=false"

# Or run in background (returns immediately)
curl -X POST "http://localhost:8000/ingest/doj?background=true"
```

**Note**: The DOJ ingestion automatically:
- Crawls justice.gov/epstein for documents
- Excludes "Epstein Files Transparency Act" files (already in images)
- Downloads all other PDFs and images
- Processes them through AWS Textract OCR
- Indexes the text for search
- Makes images available at `/images/{page_id}`

### File Management Endpoints

#### Search Files
```bash
# Search for files by name or content
curl "http://localhost:8000/search/files?q=EFTA00000001"

# List all files
curl "http://localhost:8000/files?limit=50"
```

#### Get File
```bash
# Download original PDF/image file
curl "http://localhost:8000/files/{document_id}" -o document.pdf
```

## ⚙️ Configuration

Key configuration options in `.env`:

### OCR Engine Selection
- `OCR_ENGINE`: `textract` (recommended for production), `paddleocr`, `easyocr`, `tesseract`, or `ensemble`
- `OCR_LANGUAGES`: Comma-separated language codes (e.g., `en,es`)
- `OCR_GPU`: Enable GPU acceleration (`true`/`false`)
- `OCR_PREPROCESS`: Enable preprocessing pipeline (`true`/`false`)
- `OCR_DESKEW`: Enable automatic deskewing (`true`/`false`)
- `OCR_SCALES`: Comma-separated scale factors for multi-scale OCR (e.g., `1,2`)

### AWS Textract Configuration (Recommended)
- `AWS_ACCESS_KEY_ID`: Your AWS access key
- `AWS_SECRET_ACCESS_KEY`: Your AWS secret key
- `AWS_DEFAULT_REGION`: AWS region (default: `us-east-1`)

**Why Textract?**
- Industry-leading accuracy for legal documents
- Built-in handwriting recognition
- Word-level bounding boxes and confidence scores
- No GPU or model downloads required
- Pay-per-use pricing (~$1.50 per 1000 pages)

### PaddleOCR Settings (for maximum accuracy)
- `PADDLE_USE_ANGLE_CLS`: Enable angle classification for rotated text (`true`/`false`)
- `PADDLE_DET_DB_THRESH`: Detection threshold (lower = more aggressive, default: `0.3`)
- `PADDLE_DET_DB_BOX_THRESH`: Box threshold (default: `0.5`)
- `PADDLE_DET_DB_UNCLIP_RATIO`: Text box expansion ratio (default: `1.6`)
- `PADDLE_DET_LIMIT_SIDE_LEN`: Max side length for detection (default: `2560`)
- `PADDLE_DROP_SCORE`: Minimum recognition confidence (default: `0.3`)

### General Settings
- `ENABLE_SEMANTIC_SEARCH`: Enable semantic search (requires more resources)
- `DATABASE_URL`: Database connection string
- `SOURCE_ENDPOINT`: Source URL for document crawling

## 📁 Project Structure

```
epsteingptengine/
├── api/                    # FastAPI application
│   ├── __init__.py
│   └── main.py            # API endpoints
├── ingestion/             # Document ingestion
│   ├── __init__.py
│   ├── crawler.py         # Generic web crawler
│   ├── doj_crawler.py     # DOJ Epstein files crawler
│   ├── pdf_converter.py   # PDF to image conversion
│   └── storage.py         # Storage management
├── ocr/                   # OCR processing
│   ├── __init__.py
│   ├── engine.py          # OCR engines (PaddleOCR, EasyOCR, Tesseract)
│   ├── textract.py        # AWS Textract integration
│   ├── rekognition.py     # AWS Rekognition (labels, celebrities)
│   ├── preprocess.py      # Image preprocessing (deskew, denoise, CLAHE)
│   └── processor.py       # OCR pipeline
├── scripts/               # Utility scripts
│   ├── init_db.py         # Database initialization
│   ├── process_pending.py # Process pending pages
│   └── ingest_doj_files.py # DOJ file ingestion script
├── processing/            # Text processing
│   ├── __init__.py
│   ├── normalizer.py      # Text normalization
│   ├── entity_detector.py # Entity detection
│   └── text_processor.py  # Main processor
├── search/                # Search and indexing
│   ├── __init__.py
│   ├── indexer.py         # Index creation
│   └── searcher.py        # Search implementation
├── config.py              # Configuration
├── database.py             # Database setup
├── models.py               # Database models
├── pipeline.py             # Main ingestion pipeline
├── main.py                 # API entry point
├── requirements.txt        # Python dependencies
├── Dockerfile              # Docker image
├── docker-compose.yml      # Docker Compose config
└── README.md               # This file
```

## 🔧 Development

### Running Tests
```bash
pytest
```

### Code Formatting
```bash
black .
flake8 .
```

## 📊 Performance Considerations

- **OCR Processing**: Can be slow for large documents. Consider batch processing.
- **Semantic Search**: Requires more memory and CPU. Disable if not needed.
- **Database**: PostgreSQL recommended for production (SQLite for development).
- **Storage**: Ensure sufficient disk space for images and indexes.

## 🐛 Troubleshooting

### OCR Issues
- **PaddleOCR**: First run downloads PP-OCRv4 models (~100MB). Pre-downloaded in Docker.
- **EasyOCR**: First run downloads models. Set `OCR_GPU=true` for faster processing.
- **Tesseract**: Ensure Tesseract is installed: `apt-get install tesseract-ocr`
- **Low quality scans**: Enable `OCR_PREPROCESS=true` and `OCR_DESKEW=true`
- **Rotated text**: Enable `PADDLE_USE_ANGLE_CLS=true` (default)
- **Small text**: Add scale factor: `OCR_SCALES=1,2` for 2x upscaling

### Database Issues
- SQLite: Ensure write permissions on data directory
- PostgreSQL: Check connection string and credentials

### Memory Issues
- Reduce batch size in pipeline
- Disable semantic search if not needed
- Use GPU for OCR if available (set `OCR_GPU=true`)

## 📝 License

This project is provided as-is for educational and research purposes.

## 🤝 Contributing

Contributions welcome! Please ensure code follows PEP 8 and includes tests.

