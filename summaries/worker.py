"""Summarization + tagging worker utilities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_

from config import Config
from database import get_db
from models import Document, DocumentSummary, DocumentTag, OCRText, TagCategory
from summaries.bedrock_client import BedrockClient
from summaries.prompts import PROMPT_VERSION, build_summary_and_tags_prompt


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def chunk_text(text: str, max_chars: int = 20000) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + max_chars])
        i += max_chars
    return chunks


def load_document_text(document_id: str, max_chars_total: int = 120000) -> str:
    """
    Prefer OCR text from DB. If none exists, fallback to extracting text from the PDF via PyMuPDF.
    """
    with get_db() as db:
        rows = (
            db.query(OCRText.page_number, OCRText.raw_text)
            .filter(OCRText.document_id == document_id)
            .order_by(OCRText.page_number.asc())
            .all()
        )
        doc = db.query(Document).filter(Document.id == document_id).first()
        # Capture primitives to avoid DetachedInstanceError after session closes
        doc_file_type = (doc.file_type if doc else None)
        doc_source_url = (doc.source_url if doc else None)
        doc_s3_key_files = (doc.s3_key_files if doc else None)
    parts: List[str] = []
    total = 0
    for _, t in rows:
        if not t:
            continue
        t = str(t)
        if total + len(t) > max_chars_total:
            parts.append(t[: max_chars_total - total])
            break
        parts.append(t)
        total += len(t)
    ocr_text = "\n\n".join(parts).strip()
    if ocr_text:
        return ocr_text

    # Fallback: extract from PDF
    if doc_file_type is None and doc_source_url is None and doc_s3_key_files is None:
        return ""
    if (doc_file_type or "pdf").lower() != "pdf":
        return ""

    try:
        import fitz  # PyMuPDF
        import boto3
        import httpx

        pdf_bytes = None

        # Try S3 first (recommended on ECS)
        if Config.S3_BUCKET:
            s3 = boto3.client("s3", region_name=Config.S3_REGION or "us-east-1")
            key = doc_s3_key_files or f"{Config.S3_FILES_PREFIX.rstrip('/')}/{document_id}.pdf"
            try:
                obj = s3.get_object(Bucket=Config.S3_BUCKET, Key=key)
                pdf_bytes = obj["Body"].read()
            except Exception:
                pdf_bytes = None

            # If missing, download from source_url and (best-effort) upload to S3
            if pdf_bytes is None and doc_source_url:
                r = httpx.get(doc_source_url, timeout=60.0, follow_redirects=True)
                r.raise_for_status()
                pdf_bytes = r.content
                try:
                    s3.put_object(
                        Bucket=Config.S3_BUCKET,
                        Key=key,
                        Body=pdf_bytes,
                        ContentType="application/pdf",
                        CacheControl="public, max-age=31536000",
                    )
                except Exception:
                    pass

        if pdf_bytes is None:
            return ""

        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        extracted_parts: List[str] = []
        total = 0
        max_pages = min(pdf_doc.page_count, 20)
        for i in range(max_pages):
            t = pdf_doc.load_page(i).get_text("text") or ""
            if not t:
                continue
            if total + len(t) > max_chars_total:
                extracted_parts.append(t[: max_chars_total - total])
                break
            extracted_parts.append(t)
            total += len(t)
        return "\n\n".join(extracted_parts).strip()
    except Exception:
        return ""


def get_taxonomy_ids() -> List[str]:
    with get_db() as db:
        return [r[0] for r in db.query(TagCategory.id).order_by(TagCategory.id.asc()).all()]


def upsert_summary_status(document_id: str, status: str, **fields):
    with get_db() as db:
        row = db.query(DocumentSummary).filter(DocumentSummary.document_id == document_id).first()
        if not row:
            row = DocumentSummary(document_id=document_id, status=status)
            db.add(row)
        row.status = status
        for k, v in fields.items():
            setattr(row, k, v)


def replace_ai_tags(document_id: str, tags: List[Dict]):
    with get_db() as db:
        # Delete prior AI tags
        db.query(DocumentTag).filter(
            and_(DocumentTag.document_id == document_id, DocumentTag.source == "ai")
        ).delete(synchronize_session=False)

        for t in tags:
            tag_id = t.get("id")
            if not tag_id:
                continue
            conf = t.get("confidence")
            try:
                conf_val = float(conf) if conf is not None else None
            except Exception:
                conf_val = None
            db.add(
                DocumentTag(
                    document_id=document_id,
                    tag_id=str(tag_id),
                    confidence=conf_val,
                    source="ai",
                )
            )


def summarize_and_tag_document(document_id: str, *, bedrock: Optional[BedrockClient] = None) -> Tuple[str, List[Dict]]:
    """
    Generate summary + tags for a document and store in DB.
    Returns (status, tags).
    """
    bedrock = bedrock or BedrockClient()
    taxonomy = get_taxonomy_ids()

    text = load_document_text(document_id)
    if not text:
        upsert_summary_status(
            document_id,
            "failed",
            error="No OCR text available for document",
            prompt_version=PROMPT_VERSION,
            model_id=bedrock.model_id,
        )
        return "failed", []

    source_hash = sha256_text(text)

    # Skip if already summarized for this exact source hash
    with get_db() as db:
        existing = db.query(DocumentSummary).filter(DocumentSummary.document_id == document_id).first()
        if existing and existing.status == "succeeded" and existing.source_text_sha256 == source_hash:
            tags = (
                db.query(DocumentTag.tag_id, DocumentTag.confidence, DocumentTag.source)
                .filter(DocumentTag.document_id == document_id)
                .all()
            )
            return "succeeded", [{"id": t[0], "confidence": t[1], "source": t[2]} for t in tags]

    upsert_summary_status(
        document_id,
        "running",
        source_text_sha256=source_hash,
        prompt_version=PROMPT_VERSION,
        model_id=bedrock.model_id,
        error=None,
    )

    # If very long, summarize chunks then summarize the summaries.
    chunks = chunk_text(text, max_chars=20000)
    if len(chunks) == 1:
        prompt = build_summary_and_tags_prompt(chunks[0], taxonomy=taxonomy)
        result = bedrock.invoke_json(prompt, max_tokens=900)
        summary_md = result.summary_markdown
        tags = result.tags
    else:
        partials = []
        for c in chunks[:8]:  # cap to reduce cost
            p = build_summary_and_tags_prompt(c, taxonomy=taxonomy)
            r = bedrock.invoke_json(p, max_tokens=600)
            partials.append(r.summary_markdown)
        merged_text = "\n\n".join(partials)
        prompt = build_summary_and_tags_prompt(merged_text, taxonomy=taxonomy)
        result = bedrock.invoke_json(prompt, max_tokens=900)
        summary_md = result.summary_markdown
        tags = result.tags

    if not tags:
        tags = [{"id": "other", "confidence": 0.5}]

    upsert_summary_status(
        document_id,
        "succeeded",
        summary_markdown=summary_md,
        source_text_sha256=source_hash,
        prompt_version=PROMPT_VERSION,
        model_id=bedrock.model_id,
        error=None,
    )
    replace_ai_tags(document_id, tags)
    return "succeeded", tags


def backfill_documents(limit: int = 0, offset: int = 0, only_missing: bool = True, collection: Optional[str] = None) -> Dict[str, int]:
    """
    Backfill summaries + tags for documents.
    
    Args:
        limit: Number of documents to process (0 = all)
        offset: Offset for pagination
        only_missing: If True, only process docs without an existing succeeded summary
        collection: Optional collection filter (e.g., "deleted") - only process documents in this collection
    """
    with get_db() as db:
        q = db.query(Document.id).order_by(Document.ingested_at.desc())
        
        # Filter by collection if specified
        if collection is not None:
            q = q.filter(Document.collection == collection)
        
        if limit and limit > 0:
            q = q.offset(offset).limit(limit)
        doc_ids = [r[0] for r in q.all()]

    counts = {"total": len(doc_ids), "succeeded": 0, "failed": 0, "skipped": 0}
    bedrock = BedrockClient()
    for doc_id in doc_ids:
        if only_missing:
            with get_db() as db:
                existing = db.query(DocumentSummary).filter(DocumentSummary.document_id == doc_id).first()
                if existing and existing.status == "succeeded" and existing.summary_markdown:
                    counts["skipped"] += 1
                    continue
        status, _ = summarize_and_tag_document(doc_id, bedrock=bedrock)
        if status == "succeeded":
            counts["succeeded"] += 1
        else:
            counts["failed"] += 1
    return counts


