# Docker Build Troubleshooting

## Common Issues and Solutions

### Issue: `apt-get update` fails with exit code 100

**Possible Causes:**
1. Network connectivity issues
2. Package repository issues
3. Architecture mismatch (ARM vs x86_64)
4. Outdated package lists

**Solutions:**

#### 1. Try building with network mode
```bash
docker build --network=host -t ocr-rag .
```

#### 2. Clear Docker cache
```bash
docker builder prune -a
docker build --no-cache -t ocr-rag .
```

#### 3. Use alternative Dockerfile
```bash
docker build -f Dockerfile.alternative -t ocr-rag .
```

#### 4. Build step by step to identify the issue
Modify Dockerfile temporarily to see which package fails:
```dockerfile
RUN apt-get update --fix-missing
RUN apt-get install -y tesseract-ocr || echo "tesseract-ocr failed"
RUN apt-get install -y tesseract-ocr-eng || echo "tesseract-ocr-eng failed"
RUN apt-get install -y poppler-utils || echo "poppler-utils failed"
```

#### 5. Check your Docker architecture
```bash
docker info | grep Architecture
# If ARM, you may need ARM-compatible packages
```

### Issue: Package not found

**Solution:** Update package lists or use different repository
```dockerfile
RUN apt-get update --fix-missing && \
    apt-get install -y --no-install-recommends \
    -o Acquire::Retries=3 \
    tesseract-ocr \
    ...
```

### Issue: Slow builds or timeouts

**Solutions:**
1. Use Docker BuildKit:
```bash
DOCKER_BUILDKIT=1 docker build -t ocr-rag .
```

2. Use build cache:
```bash
docker build --cache-from ocr-rag:latest -t ocr-rag .
```

3. Build without EasyOCR first (if not needed immediately):
```dockerfile
# Comment out EasyOCR in requirements.txt temporarily
# Or install it separately after other packages
```

### Issue: Memory errors during build

**Solution:** Increase Docker memory limit or build without semantic search:
```bash
# In .env, set:
ENABLE_SEMANTIC_SEARCH=false
```

### Issue: Permission errors

**Solution:** Ensure Docker has proper permissions:
```bash
sudo usermod -aG docker $USER
# Log out and back in
```

## Quick Fixes

### Minimal Dockerfile (for testing)
If you just want to test without all dependencies:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install only essential packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Note: This won't have Tesseract, use EasyOCR only
# Set OCR_ENGINE=easyocr in .env

COPY . .
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Build without OCR engines (for API only)
If you just want to run the API without processing:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements-api.txt .  # Create minimal requirements
RUN pip install --no-cache-dir -r requirements-api.txt

COPY . .
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Testing the Build

### Step 1: Test base image
```bash
docker run -it python:3.11-slim bash
# Inside container:
apt-get update
apt-get install -y tesseract-ocr
```

### Step 2: Test individual packages
```bash
docker run -it python:3.11-slim bash
apt-get update
apt-get install -y tesseract-ocr && echo "Success" || echo "Failed"
```

### Step 3: Build with verbose output
```bash
docker build --progress=plain -t ocr-rag . 2>&1 | tee build.log
```

## Alternative: Use Pre-built Image

If building continues to fail, you can:

1. **Use Python base and install manually:**
```bash
docker run -it python:3.11-slim bash
# Install packages manually inside
# Then commit the container
docker commit <container-id> ocr-rag:base
```

2. **Use multi-stage build:**
```dockerfile
FROM python:3.11-slim as base
RUN apt-get update && apt-get install -y tesseract-ocr poppler-utils

FROM base as app
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
```

## Platform-Specific Issues

### macOS (Apple Silicon)
If on M1/M2 Mac:
```bash
docker build --platform linux/amd64 -t ocr-rag .
# Or use linux/arm64 if packages support it
```

### Linux
Ensure Docker has proper network access:
```bash
sudo systemctl restart docker
```

### Windows
Use WSL2 and build from there, or ensure Docker Desktop has proper network access.

## Getting Help

If issues persist:
1. Check Docker logs: `docker logs <container-id>`
2. Check build output: `docker build -t ocr-rag . 2>&1 | tee build.log`
3. Try minimal build first, then add packages incrementally
4. Check if packages are available: `apt-cache search tesseract-ocr`



