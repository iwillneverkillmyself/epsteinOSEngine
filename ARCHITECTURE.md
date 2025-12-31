# System Architecture

## Overview

This document describes the architecture of the OCR-centric RAG system, designed for extracting, processing, and searching text from image-heavy documents.

## System Design Principles

1. **OCR-First**: All functionality is built around OCR text extraction
2. **Positional Awareness**: Preserves bounding box coordinates for highlighting
3. **Entity-Centric**: Focuses on detecting and searching entities
4. **Minimal LLM Usage**: LLMs only for normalization/disambiguation, not answering
5. **Self-Hostable**: Designed to run on VPS or Raspberry Pi

## Architecture Layers

### Layer 1: Ingestion

**Purpose**: Fetch and prepare documents for processing

**Components**:
- `DocumentCrawler`: Discovers files from source endpoint
- `PDFConverter`: Converts PDF pages to images
- `DocumentStorage`: Manages document metadata and storage

**Flow**:
```
Source Endpoint → Crawler → File Download → PDF Conversion (if needed) → Image Storage
```

**Key Features**:
- Async HTTP fetching
- PDF page extraction
- Stable document IDs (SHA256 hash)
- Metadata preservation

### Layer 2: OCR Processing

**Purpose**: Extract text from images with positional information

**Components**:
- `OCREngine`: Base interface for OCR engines
- `EasyOCREngine`: EasyOCR implementation (handwritten text support)
- `TesseractEngine`: Tesseract implementation (fast, traditional)
- `OCRProcessor`: Orchestrates OCR pipeline

**Flow**:
```
Image → OCR Engine → Text + Bounding Boxes → Database Storage
```

**Output Format**:
```python
{
    'text': 'Extracted text string',
    'word_boxes': [
        {
            'text': 'word',
            'x': 100.0,
            'y': 200.0,
            'width': 50.0,
            'height': 20.0,
            'confidence': 0.95
        }
    ],
    'confidence': 0.92
}
```

### Layer 3: Text Processing

**Purpose**: Normalize text and detect entities

**Components**:
- `TextNormalizer`: Cleans and normalizes OCR output
- `EntityDetector`: Detects names, emails, phones, dates
- `TextProcessor`: Main processing pipeline

**Entity Detection**:
- **Names**: Capitalized word sequences (heuristic-based)
- **Emails**: Regex pattern matching
- **Phones**: Multiple US phone format patterns
- **Dates**: Various date format patterns with parsing

**Flow**:
```
OCR Text → Normalization → Entity Detection → Entity Storage
```

### Layer 4: Indexing

**Purpose**: Create searchable indexes

**Components**:
- `SearchIndexer`: Creates full-text and semantic indexes
- Full-text index: SQLite/PostgreSQL with tokenized text
- Semantic index: ChromaDB with embeddings (optional)

**Index Types**:
1. **Full-Text Index**: Tokenized, normalized text for keyword search
2. **Semantic Index**: Vector embeddings for semantic search (optional)
3. **Entity Index**: Indexed by entity type and value

### Layer 5: Search API

**Purpose**: Provide search interface

**Components**:
- `SearchEngine`: Implements search strategies
- FastAPI endpoints: RESTful API

**Search Types**:
1. **Keyword Search**: Exact token matching
2. **Fuzzy Search**: Similarity-based matching (Jaccard similarity)
3. **Phrase Search**: Exact phrase matching
4. **Entity Search**: Search by entity type and value
5. **Semantic Search**: Vector similarity (optional)

## Data Flow

### Ingestion Flow
```
1. Crawler discovers files from endpoint
2. Files downloaded to temp directory
3. PDFs converted to images
4. Images stored with metadata
5. Document records created in database
```

### Processing Flow
```
1. Image pages queued for OCR
2. OCR extracts text + bounding boxes
3. Text normalized
4. Entities detected and stored
5. Text indexed for search
```

### Search Flow
```
1. User query received
2. Search type determined
3. Appropriate index queried
4. Results formatted with snippets
5. Bounding boxes included for highlighting
```

## Database Schema

### Documents Table
- `id`: Document ID (SHA256 hash)
- `source_url`: Original URL
- `file_name`: Original filename
- `file_type`: File extension
- `file_size`: Size in bytes
- `page_count`: Number of pages
- `metadata`: JSON metadata

### ImagePages Table
- `id`: Page ID
- `document_id`: Reference to document
- `page_number`: Page number
- `image_path`: Path to image file
- `width`, `height`: Image dimensions
- `ocr_processed`: Processing status

### OCRText Table
- `id`: OCR text ID
- `image_page_id`: Reference to image page
- `document_id`: Reference to document
- `raw_text`: Original OCR output
- `normalized_text`: Normalized text
- `word_boxes`: JSON array of bounding boxes
- `bbox_x`, `bbox_y`, `bbox_width`, `bbox_height`: Overall bounding box
- `confidence`: OCR confidence score

### Entities Table
- `id`: Entity ID
- `ocr_text_id`: Reference to OCR text
- `entity_type`: name, email, phone, date, keyword
- `entity_value`: Original value
- `normalized_value`: Normalized value
- `bbox_x`, `bbox_y`, `bbox_width`, `bbox_height`: Position
- `confidence`: Detection confidence

### SearchIndex Table
- `id`: Index ID
- `ocr_text_id`: Reference to OCR text
- `searchable_text`: Normalized, lowercased text
- `tokens`: JSON array of tokens

## Deployment Architecture

### Container Structure
```
┌─────────────────┐
│   API Service   │  ← FastAPI server (port 8000)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   PostgreSQL    │  ← Database (optional, can use SQLite)
└─────────────────┘

┌─────────────────┐
│  Worker Service │  ← Ingestion/processing (runs on demand)
└─────────────────┘
```

### Storage Layout
```
data/
├── storage/          # Original documents
├── images/           # Extracted image pages
├── indexes/          # Search indexes
│   └── chroma_db/    # ChromaDB data (if semantic search enabled)
└── ocr.db            # SQLite database (if using SQLite)
```

## Performance Characteristics

### OCR Processing
- **EasyOCR**: ~2-5 seconds per page (CPU), ~0.5-1 second (GPU)
- **Tesseract**: ~1-2 seconds per page (CPU)

### Search Performance
- **Keyword Search**: <100ms for typical queries
- **Fuzzy Search**: ~200-500ms (depends on corpus size)
- **Semantic Search**: ~300-800ms (includes embedding computation)

### Storage Requirements
- **Images**: ~1-5 MB per page (PNG)
- **Database**: ~10-50 KB per OCR text record
- **Indexes**: ~5-20 KB per indexed text

## Scalability Considerations

### Horizontal Scaling
- API service can be scaled horizontally
- Database can be separated to dedicated server
- Worker can run on separate machines

### Vertical Scaling
- GPU acceleration for OCR (EasyOCR)
- More RAM for larger semantic indexes
- Faster CPU for Tesseract

### Optimization Strategies
1. Batch processing for OCR
2. Async processing pipeline
3. Caching frequently accessed data
4. Database query optimization with indexes
5. Image compression for storage

## Security Considerations

1. **Input Validation**: All API inputs validated
2. **SQL Injection**: SQLAlchemy ORM prevents injection
3. **File System**: Sandboxed storage paths
4. **Rate Limiting**: Can be added to API (not included by default)
5. **Authentication**: Can be added for production (not included by default)

## Monitoring and Logging

- Structured logging with configurable levels
- Database query logging (optional)
- Processing metrics available via `/stats` endpoint
- Error tracking in logs

## Future Enhancements

1. **Advanced NER**: Use spaCy NER models for better name detection
2. **Image Preprocessing**: Enhance images before OCR
3. **Multi-language Support**: Better language detection
4. **Distributed Processing**: Queue-based processing (Celery, RabbitMQ)
5. **Caching Layer**: Redis for frequently accessed data
6. **Web UI**: Frontend for search interface




