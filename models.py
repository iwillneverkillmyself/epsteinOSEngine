"""Database models for OCR RAG system."""
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON, Index, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime
import uuid

Base = declarative_base()


class Document(Base):
    """Stored document metadata."""
    __tablename__ = "documents"
    
    id = Column(String, primary_key=True)
    source_url = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_type = Column(String)  # pdf, jpg, png, etc.
    file_size = Column(Integer)
    page_count = Column(Integer, default=1)
    ingested_at = Column(DateTime, default=func.now())
    doc_metadata = Column(JSON, default={})  # Renamed from 'metadata' (reserved in SQLAlchemy)

    # S3 acceleration fields (avoid per-open S3 head checks + enable stable caching)
    s3_key_files = Column(Text, nullable=True)  # e.g. "files/<document_id>.pdf"
    s3_presigned_url = Column(Text, nullable=True)
    s3_presigned_expires_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index('idx_source_url', 'source_url'),
        Index('idx_file_name', 'file_name'),
    )


class ImagePage(Base):
    """Individual image pages extracted from documents."""
    __tablename__ = "image_pages"
    
    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    image_path = Column(String, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    ocr_processed = Column(Boolean, default=False)
    ocr_processed_at = Column(DateTime)
    
    __table_args__ = (
        # Index names must be unique across the whole SQLite database (not just per-table)
        Index('idx_image_pages_document_page', 'document_id', 'page_number'),
    )


class OCRText(Base):
    """Extracted OCR text with positional information."""
    __tablename__ = "ocr_text"
    
    id = Column(String, primary_key=True)
    image_page_id = Column(String, nullable=False, index=True)
    document_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    
    # Text content
    raw_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=False)
    
    # Positional data
    word_boxes = Column(JSON)  # List of {text, x, y, width, height, confidence}
    bbox_x = Column(Float)  # Overall bounding box
    bbox_y = Column(Float)
    bbox_width = Column(Float)
    bbox_height = Column(Float)
    
    # Metadata
    confidence = Column(Float)
    extracted_at = Column(DateTime, default=func.now())
    
    __table_args__ = (
        # Index names must be unique across the whole SQLite database (not just per-table)
        Index('idx_ocr_text_document_page', 'document_id', 'page_number'),
    )


class Entity(Base):
    """Detected entities (names, emails, phones, dates, keywords)."""
    __tablename__ = "entities"
    
    id = Column(String, primary_key=True)
    ocr_text_id = Column(String, nullable=False, index=True)
    document_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    
    entity_type = Column(String, nullable=False)  # name, email, phone, date, keyword
    entity_value = Column(String, nullable=False)
    normalized_value = Column(String)
    
    # Position in image
    bbox_x = Column(Float)
    bbox_y = Column(Float)
    bbox_width = Column(Float)
    bbox_height = Column(Float)
    
    confidence = Column(Float)
    detected_at = Column(DateTime, default=func.now())
    
    __table_args__ = (
        Index('idx_entity_type', 'entity_type'),
        Index('idx_entity_value', 'entity_value'),
        Index('idx_normalized_value', 'normalized_value'),
        Index('idx_document_entity', 'document_id', 'entity_type'),
    )


class SearchIndex(Base):
    """Full-text search index for fast keyword matching."""
    __tablename__ = "search_index"
    
    id = Column(String, primary_key=True)
    ocr_text_id = Column(String, nullable=False, index=True)
    document_id = Column(String, nullable=False, index=True)
    
    # Searchable text (normalized, tokenized)
    searchable_text = Column(Text, nullable=False)
    tokens = Column(JSON)  # List of tokens for fuzzy matching
    
    __table_args__ = ()


class ImageLabel(Base):
    """AWS Rekognition detected labels (objects, scenes, concepts)."""
    __tablename__ = "image_labels"
    
    id = Column(String, primary_key=True)
    image_page_id = Column(String, nullable=False, index=True)
    document_id = Column(String, nullable=False, index=True)
    
    # Label info
    label_name = Column(String, nullable=False)  # e.g., "Floor", "Person", "Car"
    label_name_lower = Column(String, nullable=False, index=True)  # lowercase for search
    confidence = Column(Float, nullable=False)
    
    # Parent labels (hierarchy)
    parent_labels = Column(JSON)  # e.g., ["Furniture", "Indoors"]
    
    # Bounding box (if object has specific location)
    has_bbox = Column(Boolean, default=False)
    bbox_left = Column(Float)
    bbox_top = Column(Float)
    bbox_width = Column(Float)
    bbox_height = Column(Float)
    
    # Categories from Rekognition
    categories = Column(JSON)  # e.g., ["Home and Garden", "Interior"]
    
    detected_at = Column(DateTime, default=func.now())
    
    __table_args__ = (
        Index('idx_label_name', 'label_name'),
        Index('idx_label_document', 'document_id', 'label_name_lower'),
    )


class Celebrity(Base):
    """AWS Rekognition detected celebrities."""
    __tablename__ = "celebrities"
    
    id = Column(String, primary_key=True)
    image_page_id = Column(String, nullable=False, index=True)
    document_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    
    # Celebrity info
    name = Column(String, nullable=False)
    name_lower = Column(String, nullable=False, index=True)  # lowercase for search
    confidence = Column(Float, nullable=False)
    
    # Reference URLs (Wikipedia, IMDB, etc.)
    urls = Column(JSON)  # e.g., ["https://www.imdb.com/name/..."]
    
    # Bounding box for face
    bbox_left = Column(Float)
    bbox_top = Column(Float)
    bbox_width = Column(Float)
    bbox_height = Column(Float)
    
    detected_at = Column(DateTime, default=func.now())
    
    __table_args__ = (
        Index('idx_celebrity_name', 'name'),
        Index('idx_celebrity_name_lower', 'name_lower'),
        Index('idx_celebrity_document', 'document_id'),
    )


class DocumentSummary(Base):
    """Cached AI summary for a document."""
    __tablename__ = "document_summaries"

    document_id = Column(String, primary_key=True)
    summary_markdown = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending|running|succeeded|failed
    model_id = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    source_text_sha256 = Column(String, nullable=True, index=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_document_summaries_status", "status"),
    )


class TagCategory(Base):
    """Approved tag taxonomy (fixed list)."""
    __tablename__ = "tag_categories"

    id = Column(String, primary_key=True)  # e.g. "financial"
    label = Column(String, nullable=False)  # e.g. "Financial"


class DocumentTag(Base):
    """Tag assignments for documents (many-to-many)."""
    __tablename__ = "document_tags"

    document_id = Column(String, primary_key=True)
    tag_id = Column(String, primary_key=True)
    confidence = Column(Float, nullable=True)
    source = Column(String, nullable=False, default="ai")  # ai|manual
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_document_tags_document", "document_id"),
        Index("idx_document_tags_tag", "tag_id"),
    )


class Comment(Base):
    """Anonymous comments on documents (optionally per page) or image pages, with one-level replies."""
    __tablename__ = "comments"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    target_type = Column(String, nullable=False)  # "document" | "image"

    document_id = Column(String, nullable=True, index=True)
    page_number = Column(Integer, nullable=True, index=True)
    image_page_id = Column(String, nullable=True, index=True)

    parent_id = Column(String, nullable=True, index=True)  # NULL = top-level, non-NULL = reply

    username = Column(String, nullable=False)
    body = Column(Text, nullable=False)

    ip_hash = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=func.now(), index=True)
    
    # Reaction counts (denormalized for fast reads)
    likes_count = Column(Integer, default=0, nullable=False)
    dislikes_count = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_comments_target_doc", "target_type", "document_id", "page_number", "created_at"),
        Index("idx_comments_target_image", "target_type", "image_page_id", "created_at"),
        Index("idx_comments_parent", "parent_id", "created_at"),
    )


class CommentReaction(Base):
    """Individual like/dislike reactions on comments (prevents duplicate reactions from same IP)."""
    __tablename__ = "comment_reactions"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    comment_id = Column(String, nullable=False, index=True)
    reaction_type = Column(String, nullable=False)  # "like" | "dislike"
    ip_hash = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    __table_args__ = (
        Index("idx_comment_reactions_comment_ip", "comment_id", "ip_hash", unique=True),
    )

