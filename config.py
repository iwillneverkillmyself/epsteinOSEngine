"""Configuration management for OCR RAG system."""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""
    
    # API Configuration
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "sqlite:///./data/ocr.db"
    )
    
    # Storage paths
    BASE_DIR: Path = Path(__file__).parent
    STORAGE_PATH: Path = BASE_DIR / os.getenv("STORAGE_PATH", "./data/storage")
    IMAGES_PATH: Path = BASE_DIR / os.getenv("IMAGES_PATH", "./data/images")
    INDEXES_PATH: Path = BASE_DIR / os.getenv("INDEXES_PATH", "./data/indexes")
    
    # OCR Configuration
    # Engine: "textract" (AWS, recommended), "paddleocr", "easyocr", or "tesseract"
    OCR_ENGINE: str = os.getenv("OCR_ENGINE", "textract")
    OCR_LANGUAGES: list = os.getenv("OCR_LANGUAGES", "en").split(",")
    OCR_GPU: bool = os.getenv("OCR_GPU", "false").lower() == "true"
    OCR_PREPROCESS: bool = os.getenv("OCR_PREPROCESS", "true").lower() == "true"
    # Enable deskewing (rotation correction) for scanned documents
    OCR_DESKEW: bool = os.getenv("OCR_DESKEW", "true").lower() == "true"
    # Comma-separated list of scales to try for OCR (e.g. "1,2")
    OCR_SCALES: list = [float(x) for x in os.getenv("OCR_SCALES", "1,2").split(",") if x.strip()]
    
    # PaddleOCR Configuration (PP-OCRv4 models - state of the art)
    PADDLE_USE_ANGLE_CLS: bool = os.getenv("PADDLE_USE_ANGLE_CLS", "true").lower() == "true"
    PADDLE_DET_MODEL_DIR: str = os.getenv("PADDLE_DET_MODEL_DIR", "")  # Empty = auto-download
    PADDLE_REC_MODEL_DIR: str = os.getenv("PADDLE_REC_MODEL_DIR", "")
    PADDLE_CLS_MODEL_DIR: str = os.getenv("PADDLE_CLS_MODEL_DIR", "")
    # Detection parameters for better accuracy on noisy scans
    PADDLE_DET_DB_THRESH: float = float(os.getenv("PADDLE_DET_DB_THRESH", "0.3"))
    PADDLE_DET_DB_BOX_THRESH: float = float(os.getenv("PADDLE_DET_DB_BOX_THRESH", "0.5"))
    PADDLE_DET_DB_UNCLIP_RATIO: float = float(os.getenv("PADDLE_DET_DB_UNCLIP_RATIO", "1.6"))
    PADDLE_DET_LIMIT_SIDE_LEN: int = int(os.getenv("PADDLE_DET_LIMIT_SIDE_LEN", "2560"))
    # Recognition parameters
    PADDLE_REC_BATCH_NUM: int = int(os.getenv("PADDLE_REC_BATCH_NUM", "6"))
    PADDLE_DROP_SCORE: float = float(os.getenv("PADDLE_DROP_SCORE", "0.3"))
    # Use server-grade models for better accuracy (slower but more accurate)
    PADDLE_USE_SERVER_MODEL: bool = os.getenv("PADDLE_USE_SERVER_MODEL", "false").lower() == "true"
    
    # EasyOCR tuning (more aggressive defaults for low-quality scans)
    EASYOCR_TEXT_THRESHOLD: float = float(os.getenv("EASYOCR_TEXT_THRESHOLD", "0.6"))
    EASYOCR_LOW_TEXT: float = float(os.getenv("EASYOCR_LOW_TEXT", "0.3"))
    EASYOCR_LINK_THRESHOLD: float = float(os.getenv("EASYOCR_LINK_THRESHOLD", "0.4"))
    EASYOCR_CANVAS_SIZE: int = int(os.getenv("EASYOCR_CANVAS_SIZE", "2560"))
    EASYOCR_MAG_RATIO: float = float(os.getenv("EASYOCR_MAG_RATIO", "2.0"))
    # Tesseract tuning
    TESSERACT_PSM: str = os.getenv("TESSERACT_PSM", "6")  # 6=block of text, 11=sparse
    
    # Entity Detection
    ENABLE_NAME_DETECTION: bool = os.getenv("ENABLE_NAME_DETECTION", "true").lower() == "true"
    ENABLE_EMAIL_DETECTION: bool = os.getenv("ENABLE_EMAIL_DETECTION", "true").lower() == "true"
    ENABLE_PHONE_DETECTION: bool = os.getenv("ENABLE_PHONE_DETECTION", "true").lower() == "true"
    ENABLE_DATE_DETECTION: bool = os.getenv("ENABLE_DATE_DETECTION", "true").lower() == "true"
    
    # Search Configuration
    ENABLE_SEMANTIC_SEARCH: bool = os.getenv("ENABLE_SEMANTIC_SEARCH", "false").lower() == "true"
    SEMANTIC_MODEL: str = os.getenv("SEMANTIC_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    
    # Source Endpoint
    SOURCE_ENDPOINT: str = os.getenv(
        "SOURCE_ENDPOINT",
        "https://epstein-files.rhys-669.workers.dev"
    )
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # S3-backed asset serving (recommended for ECS/Fargate)
    # If S3_BUCKET is set, /files and /images can be served via presigned URLs.
    S3_BUCKET: Optional[str] = os.getenv("S3_BUCKET") or None
    S3_REGION: Optional[str] = os.getenv("S3_REGION") or None
    S3_FILES_PREFIX: str = os.getenv("S3_FILES_PREFIX", "files")
    S3_IMAGES_PREFIX: str = os.getenv("S3_IMAGES_PREFIX", "images")
    S3_PRESIGN_EXPIRES_SECONDS: int = int(os.getenv("S3_PRESIGN_EXPIRES_SECONDS", "3600"))
    
    @classmethod
    def ensure_directories(cls):
        """Create necessary directories if they don't exist."""
        cls.STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        cls.IMAGES_PATH.mkdir(parents=True, exist_ok=True)
        cls.INDEXES_PATH.mkdir(parents=True, exist_ok=True)


# Initialize directories
Config.ensure_directories()

