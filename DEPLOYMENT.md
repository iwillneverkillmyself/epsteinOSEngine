# Deployment Guide

This guide covers deploying the OCR RAG system on various platforms.

## Prerequisites

- Docker and Docker Compose (recommended)
- OR Python 3.11+ with pip
- 4GB+ RAM recommended
- 10GB+ disk space for data

## Quick Start (Docker)

### 1. Clone and Setup

```bash
git clone <repository>
cd epsteingptengine
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings (optional, defaults work)
```

### 3. Start Services

```bash
# Start API server
docker-compose up -d api

# Run ingestion (one-time or scheduled)
docker-compose run --rm worker
```

### 4. Access API

- API: http://localhost:8000
- Interactive Docs: http://localhost:8000/docs
- Health Check: http://localhost:8000/health

## Manual Deployment (Python)

### 1. System Dependencies

**Ubuntu/Debian**:
```bash
sudo apt-get update
sudo apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    python3.11 \
    python3.11-venv \
    python3-pip
```

**macOS**:
```bash
brew install tesseract poppler python@3.11
```

### 2. Python Setup

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Download spaCy model (optional, for better NER)
python -m spacy download en_core_web_sm
```

### 3. Configuration

```bash
# Copy and edit environment file
cp .env.example .env
nano .env  # or use your preferred editor
```

Key settings:
- `DATABASE_URL`: Use SQLite for simple setup, PostgreSQL for production
- `OCR_ENGINE`: `easyocr` (better accuracy) or `tesseract` (faster)
- `SOURCE_ENDPOINT`: Your document source URL

### 4. Initialize Database

```bash
python -c "from database import init_db; init_db()"
```

### 5. Run Ingestion

```bash
# Run full ingestion pipeline
python pipeline.py
```

This will:
1. Crawl and fetch documents
2. Convert PDFs to images
3. Run OCR on all images
4. Detect entities
5. Index for search

### 6. Start API Server

```bash
# Development mode (with auto-reload)
python main.py

# Production mode
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## Production Deployment

## AWS ECS Fargate (Recommended)

This is the cleanest path for “public website + stable API”, especially since you already use **RDS Postgres**.

### Architecture
- **ECS Fargate service** runs the FastAPI container behind an **Application Load Balancer (ALB)**
- **RDS Postgres** in the same VPC (private)
- **S3** stores binaries:
  - PDFs for `GET /files/{document_id}`
  - Page images for `GET /images/{page_id}`
- (Optional) **CloudFront** in front of S3 for cheaper/faster global delivery (recommended for lots of traffic)

### 0) Prepare S3 for assets
1. Create an S3 bucket (e.g. `epsteingptengine-assets`)
2. Upload your existing assets from your laptop:

```bash
python scripts/sync_assets_to_s3.py --bucket YOUR_BUCKET --region us-east-1
```

By default this uploads:
- `data/storage/*` → `s3://YOUR_BUCKET/files/*`
- `data/images/*` → `s3://YOUR_BUCKET/images/*`

### 1) Create an ECR repo + push the Docker image
```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=YOUR_ACCOUNT_ID
export ECR_REPO=epsteingptengine
export IMAGE_TAG=latest
./deploy/ecs/build_and_push_ecr.sh
```

### 2) Create IAM roles
You need **two roles**:

- **Execution role** (standard): `ecsTaskExecutionRole`
  - Attach AWS managed policy: `AmazonECSTaskExecutionRolePolicy`

- **Task role** (app permissions): `epsteingptengineTaskRole`
  - Allow:
    - `s3:GetObject` on your bucket (serving `/files` + `/images`)
    - `textract:*` calls you use (e.g. `DetectDocumentText`, `AnalyzeDocument`)
    - `rekognition:*` calls you use (e.g. `RecognizeCelebrities`, `DetectLabels`)

Template policy is in:
- `deploy/ecs/task-role-policy.json`

### 3) Networking (this is the part that usually trips people)
- Create/choose a VPC with **private subnets** for ECS tasks
- Create an **ALB** in **public subnets**
- Security Groups:
  - **ALB SG**: inbound `80/443` from internet; outbound to ECS SG
  - **ECS SG**: inbound `8000` from ALB SG; outbound `5432` to RDS SG; outbound `443` to S3/AWS APIs
  - **RDS SG**: inbound `5432` from ECS SG only (best practice)

### 4) Create ECS cluster + service
1. ECS → Clusters → Create (Networking only)
2. ECS → Task definitions → Create:
   - Use template: `deploy/ecs/task-definition.template.json`
   - Set env vars:
     - `DATABASE_URL` (your RDS endpoint)
     - `S3_BUCKET`, `S3_REGION`
3. ECS → Services → Create:
   - Launch type: **Fargate**
   - Desired tasks: 1 (start here)
   - Load balancer: attach your ALB target group
   - Health check path: `/health`

### 5) Verify
- ALB DNS → `GET /health` should return `{"status":"healthy"}`
- `GET /files/{document_id}` and `GET /images/{page_id}` should redirect to S3 presigned URLs (302)

### Environment variables to set in ECS
Minimum:
```env
DATABASE_URL=postgresql+psycopg2://postgres:*****@YOUR_RDS:5432/epsteingptengine?sslmode=require
S3_BUCKET=YOUR_BUCKET
S3_REGION=us-east-1
OCR_ENGINE=textract
LOG_LEVEL=INFO
```

## AWS RDS PostgreSQL (Recommended for public hosting)

SQLite is great locally, but for hosting your API for many users you should use Postgres.

### 1) Create an RDS Postgres instance
- Create **RDS → PostgreSQL**
- Choose a small instance to start (you can scale later)
- Set a username/password and database name (e.g. `ocr_db`)
- **Networking / Security Group**:
  - Allow inbound from your API server only (best practice)
  - For quick testing, you can temporarily allow your IP, but do not leave it open to `0.0.0.0/0`

### 2) Set `DATABASE_URL`
In your `.env` on the machine running the API:

```env
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@RDS_HOST:5432/ocr_db
```

### 3) Migrate your existing SQLite data into Postgres (one time)
Stop ingestion first (so the DB isn’t changing), then run:

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite "sqlite:///./data/ocr.db" \
  --postgres "postgresql+psycopg2://USER:PASSWORD@RDS_HOST:5432/ocr_db"
```

### 4) Important: files/images are NOT in the database
The DB migration copies database rows only. Your actual PDFs/images live on disk:
- `data/storage/` (original files served by `/files/{document_id}`)
- `data/images/` (page images served by `/images/{page_id}`)

For a public deployment you should either:
- Put these folders on persistent storage on the server, or
- Move them to **S3** and update the API to serve from S3/CloudFront.

### Using Docker Compose

1. **Use PostgreSQL** (edit docker-compose.yml):
```yaml
# Uncomment postgres service
# Uncomment depends_on in api and worker services
# Update DATABASE_URL environment variable
```

2. **Set production environment variables**:
```bash
# In .env file
DATABASE_URL=postgresql://user:password@postgres:5432/ocr_db
LOG_LEVEL=WARNING
```

3. **Deploy**:
```bash
docker-compose up -d
```

### Using Systemd (Linux)

Create `/etc/systemd/system/ocr-rag-api.service`:
```ini
[Unit]
Description=OCR RAG API Service
After=network.target

[Service]
Type=simple
User=ocruser
WorkingDirectory=/opt/ocr-rag
Environment="PATH=/opt/ocr-rag/venv/bin"
ExecStart=/opt/ocr-rag/venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable ocr-rag-api
sudo systemctl start ocr-rag-api
```

### Using Nginx Reverse Proxy

Example Nginx configuration (`/etc/nginx/sites-available/ocr-rag`):
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable:
```bash
sudo ln -s /etc/nginx/sites-available/ocr-rag /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## Raspberry Pi Deployment

### Special Considerations

1. **Use SQLite** (PostgreSQL may be too heavy)
2. **Use Tesseract** (EasyOCR is resource-intensive)
3. **Disable semantic search** (requires significant RAM)
4. **Process in smaller batches**

### Configuration for Pi

`.env` settings:
```env
OCR_ENGINE=tesseract
DATABASE_URL=sqlite:///./data/ocr.db
ENABLE_SEMANTIC_SEARCH=false
OCR_GPU=false
```

### Resource Monitoring

Monitor system resources:
```bash
# Watch CPU and memory
htop

# Check disk space
df -h
```

## Scheduled Ingestion

### Using Cron

Add to crontab (`crontab -e`):
```bash
# Run ingestion daily at 2 AM
0 2 * * * cd /path/to/epsteingptengine && /path/to/venv/bin/python pipeline.py >> /var/log/ocr-ingestion.log 2>&1
```

### Using Docker

```bash
# Add to crontab
0 2 * * * cd /path/to/epsteingptengine && docker-compose run --rm worker >> /var/log/ocr-ingestion.log 2>&1
```

## Monitoring

### Health Checks

```bash
# API health
curl http://localhost:8000/health

# System stats
curl http://localhost:8000/stats
```

### Logs

**Docker**:
```bash
docker-compose logs -f api
docker-compose logs -f worker
```

**Systemd**:
```bash
journalctl -u ocr-rag-api -f
```

## Backup and Recovery

### Database Backup

**SQLite**:
```bash
cp data/ocr.db data/ocr.db.backup
```

**PostgreSQL**:
```bash
pg_dump -U ocr_user ocr_db > backup.sql
```

### Full Backup

```bash
# Backup data directory
tar -czf ocr-rag-backup-$(date +%Y%m%d).tar.gz data/
```

### Recovery

```bash
# Restore database
cp data/ocr.db.backup data/ocr.db

# Or for PostgreSQL
psql -U ocr_user ocr_db < backup.sql
```

## Troubleshooting

### OCR Not Working

1. Check Tesseract installation:
```bash
tesseract --version
```

2. Check EasyOCR (if using):
```bash
python -c "import easyocr; print('OK')"
```

### Database Connection Issues

1. Check database URL in `.env`
2. Verify database is running (PostgreSQL)
3. Check file permissions (SQLite)

### Memory Issues

1. Reduce batch sizes in pipeline
2. Disable semantic search
3. Use Tesseract instead of EasyOCR
4. Process documents in smaller batches

### API Not Starting

1. Check port availability:
```bash
netstat -tulpn | grep 8000
```

2. Check logs for errors
3. Verify all dependencies installed

## Performance Tuning

### For Large Document Collections

1. **Use PostgreSQL** instead of SQLite
2. **Enable connection pooling** in database
3. **Process in batches** with delays
4. **Use GPU** for OCR if available
5. **Separate API and worker** to different machines

### Database Optimization

```sql
-- PostgreSQL: Create additional indexes
CREATE INDEX idx_ocr_text_doc_page ON ocr_text(document_id, page_number);
CREATE INDEX idx_entity_doc_type ON entities(document_id, entity_type);
```

## Security Hardening

1. **Add authentication** to API (not included by default)
2. **Use HTTPS** with reverse proxy
3. **Restrict file system access**
4. **Regular security updates**
5. **Monitor logs** for suspicious activity

## Scaling

### Horizontal Scaling

- Run multiple API instances behind load balancer
- Use shared database (PostgreSQL)
- Use shared storage (NFS or object storage)

### Vertical Scaling

- More CPU cores for OCR processing
- More RAM for semantic search
- GPU for faster OCR (EasyOCR)
- Faster storage (SSD) for database

