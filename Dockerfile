FROM public.ecr.aws/docker/library/python:3.11-slim

# Install system dependencies for OCR engines
# - tesseract: Traditional OCR fallback
# - poppler-utils: PDF to image conversion
# - libgl1, libglib2.0-0, libsm6, libxext6, libxrender1: OpenCV/image processing
# - libgomp1: OpenMP for PaddlePaddle
RUN apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    # Additional dependencies for PaddleOCR
    libfontconfig1 \
    libfreetype6 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
# PaddlePaddle/PaddleOCR may take a while to install
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download PaddleOCR models (PP-OCRv4) to avoid first-run delay
# This downloads detection, recognition, and angle classification models
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', use_gpu=False, show_log=False)" || true

# Download spacy model for entity detection
RUN python -m spacy download en_core_web_sm || true

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /app/data/storage /app/data/images /app/data/indexes

# Expose API port
EXPOSE 8000

# Default command (can be overridden)
CMD ["sh", "-c", "python -m uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

