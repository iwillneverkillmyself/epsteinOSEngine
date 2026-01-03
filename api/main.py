"""FastAPI application for OCR RAG search."""
from fastapi import FastAPI, HTTPException, Query, Request, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, JSONResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from pydantic import BaseModel, root_validator
from typing import List, Optional, Dict, Any
from pathlib import Path
from io import BytesIO
import logging
from collections import Counter
import re
from datetime import datetime, timedelta
import os
import hmac
import hashlib
import time
import json
import base64
from search.searcher import SearchEngine
from config import Config
from database import init_db

logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    """
    Best-effort client IP extraction behind ALB.
    """
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    except Exception:
        pass
    try:
        return request.client.host  # type: ignore[attr-defined]
    except Exception:
        return "unknown"


def _generate_random_username() -> str:
    """Generate a random username (adjective + noun style)."""
    from comments.avatars import _generate_username
    return _generate_username()


def _ip_hash(request) -> str:
    # salted hash; do not store raw IP
    ip = _client_ip(request)
    secret = os.getenv("COMMENTS_HMAC_SECRET", "salt")
    return hashlib.sha256(f"{secret}:{ip}".encode("utf-8")).hexdigest()


# Best-effort in-memory rate limiter (per ECS task).
_COMMENTS_RATE_STATE: Dict[str, Dict[str, float]] = {}

#
# Best-effort in-memory presign cache (per ECS task).
# This avoids an AWS call to generate a new presigned URL on every asset request.
#
_PRESIGN_CACHE: Dict[str, Dict[str, Any]] = {}


def _presign_cached(
    *,
    key: str,
    expires_seconds: Optional[int] = None,
    response_content_type: Optional[str] = None,
    response_content_disposition: Optional[str] = None,
) -> str:
    """
    Cache presigned URLs in-process. This improves repeated opens of images/PDFs/thumbnails.
    """
    if expires_seconds is None:
        expires_seconds = int(getattr(Config, "S3_PRESIGN_EXPIRES_SECONDS", 3600))
    expires_seconds = max(60, int(expires_seconds))

    cache_key = f"{key}|ct={response_content_type or ''}|cd={response_content_disposition or ''}|exp={expires_seconds}"
    now = time.time()
    hit = _PRESIGN_CACHE.get(cache_key)
    if hit and hit.get("exp_ts", 0) > now:
        return hit["url"]

    from storage.s3_assets import presign_get

    url = presign_get(
        key,
        expires_seconds=expires_seconds,
        response_content_type=response_content_type,
        response_content_disposition=response_content_disposition,
    )
    # Refresh a bit early so clients don’t get an about-to-expire URL.
    _PRESIGN_CACHE[cache_key] = {"url": url, "exp_ts": now + max(30, expires_seconds - 30)}
    return url


# Tag category caching (small, rarely changing)
_TAG_CATEGORIES_CACHE: Dict[str, Any] = {"ts": 0.0, "etag": None, "items": None, "labels": None}


def _tag_categories_cache_ttl_seconds() -> int:
    try:
        return int(os.getenv("TAG_CATEGORIES_CACHE_TTL_SECONDS", "300"))
    except Exception:
        return 300


def _compute_etag(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _get_tag_categories_cached() -> Dict[str, Any]:
    """
    Returns { items: [{id,label}], labels: {id: label}, etag: str }.
    Uses an in-memory cache with TTL to avoid DB hits on every request.
    """
    now = time.time()
    ttl = max(1, _tag_categories_cache_ttl_seconds())
    if (
        _TAG_CATEGORIES_CACHE.get("items") is not None
        and _TAG_CATEGORIES_CACHE.get("etag") is not None
        and _TAG_CATEGORIES_CACHE.get("labels") is not None
        and (now - float(_TAG_CATEGORIES_CACHE.get("ts", 0.0))) < ttl
    ):
        return {
            "items": _TAG_CATEGORIES_CACHE["items"],
            "labels": _TAG_CATEGORIES_CACHE["labels"],
            "etag": _TAG_CATEGORIES_CACHE["etag"],
        }

    from database import get_db
    from models import TagCategory

    with get_db() as db:
        rows = db.query(TagCategory.id, TagCategory.label).order_by(TagCategory.label.asc()).all()
    items = [{"id": r[0], "label": r[1]} for r in rows]
    labels = {r[0]: r[1] for r in rows}
    etag = _compute_etag(items)
    _TAG_CATEGORIES_CACHE.update({"ts": now, "etag": etag, "items": items, "labels": labels})
    return {"items": items, "labels": labels, "etag": etag}


# -------------------------------------------------------------------
# Public share links (stateless HMAC-signed tokens)
# -------------------------------------------------------------------

def _share_secret() -> str:
    # Required in production. In dev, fall back so local testing works.
    return os.getenv("SHARE_LINK_SECRET", "dev-share-secret")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))


def _share_sign(payload: str) -> str:
    return hmac.new(_share_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _share_token(kind: str, target_id: str) -> str:
    """
    Deterministic token for a given object so each file/photo has a stable share link.
    kind: 'd' (document) or 'i' (image page)
    """
    payload = f"{kind}:{target_id}"
    sig = _share_sign(payload)
    return f"{_b64url_encode(payload.encode('utf-8'))}.{sig}"


def _share_path(kind: str, target_id: str) -> str:
    return f"/s/{_share_token(kind, target_id)}"


def _share_verify(token: str) -> tuple[str, str]:
    try:
        payload_b64, sig = token.split(".", 1)
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid share token")
    try:
        payload = _b64url_decode(payload_b64).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid share token")
    expected = _share_sign(payload)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=404, detail="Invalid share token")
    if ":" not in payload:
        raise HTTPException(status_code=404, detail="Invalid share token")
    kind, target_id = payload.split(":", 1)
    if kind not in ("d", "i") or not target_id:
        raise HTTPException(status_code=404, detail="Invalid share token")
    return kind, target_id


def _comments_rate_limit_per_minute() -> int:
    try:
        return int(os.getenv("COMMENTS_RATE_LIMIT_PER_MINUTE", "20"))
    except Exception:
        return 20


def _rate_limit_comments_or_429(request):
    limit_per_min = _comments_rate_limit_per_minute()
    if limit_per_min <= 0:
        return

    key = _ip_hash(request)
    now = time.time()
    rate_per_sec = limit_per_min / 60.0
    capacity = float(limit_per_min)

    st = _COMMENTS_RATE_STATE.get(key)
    if not st:
        st = {"tokens": capacity, "ts": now}
        _COMMENTS_RATE_STATE[key] = st

    elapsed = max(0.0, now - st.get("ts", now))
    st["ts"] = now
    st["tokens"] = min(capacity, st.get("tokens", capacity) + elapsed * rate_per_sec)

    if st["tokens"] < 1.0:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    st["tokens"] -= 1.0

class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to require API key authentication on all endpoints except /health."""
    
    async def dispatch(self, request: StarletteRequest, call_next):
        # Allow health check and root endpoint without auth
        if request.url.path in ["/health", "/"]:
            return await call_next(request)

        # Allow public share endpoints without auth.
        if request.url.path.startswith("/s/") or request.url.path == "/s":
            return await call_next(request)
        
        # Allow OPTIONS requests (CORS preflight) without auth
        if request.method == "OPTIONS":
            return await call_next(request)
        
        # Check for API key (try both X-API-Key and x-api-key for case-insensitivity)
        api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        expected_key = os.getenv("MASTER_API_KEY")
        
        if expected_key:
            if not api_key or api_key != expected_key:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key. This API requires authentication. Include X-API-Key header."}
                )
        
        return await call_next(request)


app = FastAPI(
    title="OCR RAG Search API",
    description="Search and analyze text extracted from images via OCR",
    version="1.0.0"
)

# API Key authentication middleware (must be before CORS)
app.add_middleware(APIKeyMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key", "x-api-key"],  # Explicitly allow API key header
    expose_headers=["*"],
)

# Initialize search engine
search_engine = SearchEngine()

@app.on_event("startup")
async def _startup():
    """
    Ensure DB tables exist before serving requests.
    With SQLite, the DB file may exist but tables won't until we create them.
    """
    try:
        init_db()
    except Exception as e:
        # Don't crash the server, but make it obvious in logs and on /stats calls.
        logger.exception(f"Database initialization failed: {e}")


# Request/Response models
class SearchRequest(BaseModel):
    query: str
    search_type: str = "keyword"  # keyword, fuzzy, semantic, phrase
    limit: int = 50
    fuzzy_threshold: Optional[float] = 0.6


class SearchResult(BaseModel):
    ocr_text_id: str
    document_id: str
    page_number: int
    snippet: str
    full_text: str
    confidence: Optional[float]
    similarity: Optional[float] = None
    image_path: Optional[str] = None
    bbox: Dict[str, float]
    word_boxes: Optional[List[Dict]] = None


class EntitySearchRequest(BaseModel):
    entity_type: str  # name, email, phone, date, keyword
    entity_value: str
    limit: int = 50


class EntityResult(BaseModel):
    entity_id: str
    entity_type: str
    entity_value: str
    ocr_text_id: str
    document_id: str
    page_number: int
    snippet: str
    full_text: str
    confidence: Optional[float]
    image_path: Optional[str] = None
    bbox: Dict[str, float]


class SearchResponse(BaseModel):
    results: List[SearchResult]
    count: int
    query: str
    search_type: str


class EntitySearchResponse(BaseModel):
    results: List[EntityResult]
    count: int
    entity_type: str
    entity_value: str


# API Endpoints
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "OCR RAG Search API",
        "version": "1.0.0",
        "description": "Search and analyze text extracted from images via OCR"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


# ============================================
# Public share links (landing page + OG card)
# ============================================

@app.get("/s/{token}")
async def share_landing(token: str, request: Request):
    kind, target_id = _share_verify(token)
    base = str(request.base_url).rstrip("/")
    share_url = f"{base}/s/{token}"
    og_image_url = f"{base}/s/{token}/og.png"
    open_url = f"{base}/s/{token}/open"

    title = "epsteinOS"
    subtitle = None

    try:
        from database import get_db
        from models import Document, ImagePage

        with get_db() as db:
            if kind == "d":
                doc = db.query(Document).filter(Document.id == target_id).first()
                if not doc:
                    raise HTTPException(status_code=404, detail="Not found")
                title = f"epsteinOS — {doc.file_name}"
                subtitle = doc.source_url
            else:
                page = db.query(ImagePage).filter(ImagePage.id == target_id).first()
                if not page:
                    raise HTTPException(status_code=404, detail="Not found")
                doc = db.query(Document).filter(Document.id == page.document_id).first()
                doc_name = doc.file_name if doc else page.document_id
                title = f"epsteinOS — Photo (page {page.page_number})"
                subtitle = doc_name
    except HTTPException:
        raise
    except Exception:
        # Keep landing page resilient: metadata still works even if DB lookup fails.
        pass

    description = "Shared via epsteinOS"
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <meta name="description" content="{description}" />
    <meta property="og:site_name" content="epsteinOS" />
    <meta property="og:title" content="{title}" />
    <meta property="og:description" content="{description}" />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="{share_url}" />
    <meta property="og:image" content="{og_image_url}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{title}" />
    <meta name="twitter:description" content="{description}" />
    <meta name="twitter:image" content="{og_image_url}" />
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 0; background: #0b0f16; color: #e6edf3; }}
      .wrap {{ max-width: 920px; margin: 0 auto; padding: 28px 18px 48px; }}
      .brand {{ font-weight: 700; letter-spacing: 0.2px; opacity: 0.95; }}
      .card {{ margin-top: 18px; background: #111827; border: 1px solid #22304a; border-radius: 14px; overflow: hidden; }}
      .meta {{ padding: 16px 16px 10px; }}
      .title {{ font-size: 18px; font-weight: 650; margin: 0; }}
      .sub {{ margin-top: 6px; font-size: 13px; opacity: 0.7; word-break: break-word; }}
      .preview {{ width: 100%; display: block; background: #0b0f16; }}
      .actions {{ display: flex; gap: 10px; padding: 14px 16px 18px; }}
      a.btn {{ display: inline-flex; align-items: center; justify-content: center; padding: 10px 12px; border-radius: 10px; text-decoration: none; font-weight: 600; }}
      a.primary {{ background: #3b82f6; color: white; }}
      a.secondary {{ background: transparent; color: #e6edf3; border: 1px solid #334155; }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="brand">epsteinOS</div>
      <div class="card">
        <img class="preview" src="/s/{token}/og.png" alt="Preview" />
        <div class="meta">
          <p class="title">{title}</p>
          {f'<div class="sub">{subtitle}</div>' if subtitle else ''}
        </div>
        <div class="actions">
          <a class="btn primary" href="/s/{token}/open">Open</a>
          <a class="btn secondary" href="/s/{token}">Copy Link</a>
        </div>
      </div>
    </div>
  </body>
</html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "private, max-age=300"})


@app.get("/s/{token}/open")
async def share_open(token: str):
    kind, target_id = _share_verify(token)
    if kind == "d":
        return _serve_document_file_by_id(target_id)
    return _serve_image_by_id(target_id)


@app.get("/s/{token}/og.png")
async def share_og_image(token: str):
    """
    A stable PNG used for social previews (Open Graph / Twitter card).
    """
    kind, target_id = _share_verify(token)
    # Use a larger width for cards.
    width = 1200
    if kind == "d":
        return await get_file_thumbnail(document_id=target_id, width=width)
    return await get_thumbnail(page_id=target_id, width=width)


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """
    Perform search on OCR text.
    
    Search types:
    - keyword: Exact keyword matching
    - fuzzy: Fuzzy matching with similarity threshold
    - semantic: Semantic similarity search (if enabled)
    - phrase: Exact phrase matching
    """
    try:
        if request.search_type == "keyword":
            results = search_engine.keyword_search(request.query, request.limit)
        elif request.search_type == "fuzzy":
            threshold = request.fuzzy_threshold or 0.6
            results = search_engine.fuzzy_search(request.query, threshold, request.limit)
        elif request.search_type == "semantic":
            if not Config.ENABLE_SEMANTIC_SEARCH:
                raise HTTPException(
                    status_code=400,
                    detail="Semantic search is not enabled"
                )
            results = search_engine.semantic_search(request.query, request.limit)
        elif request.search_type == "phrase":
            results = search_engine.phrase_search(request.query, request.limit)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown search type: {request.search_type}"
            )
        
        # Convert to response models
        search_results = [
            SearchResult(**result) for result in results
        ]
        
        return SearchResponse(
            results=search_results,
            count=len(search_results),
            query=request.query,
            search_type=request.search_type
        )
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Comments (anonymous username via signed header)
# ============================================

class CommentCreateRequest(BaseModel):
    body: str
    page_number: Optional[int] = None

    @root_validator(pre=True)
    def _normalize_body(cls, values):
        # Be forgiving about frontend payload keys.
        if isinstance(values, dict) and "body" not in values:
            for k in ("text", "content", "comment", "message"):
                if k in values and values.get(k) is not None:
                    values["body"] = values.get(k)
                    break
        return values


class ReplyCreateRequest(BaseModel):
    body: str

    @root_validator(pre=True)
    def _normalize_body(cls, values):
        # Be forgiving about frontend payload keys.
        if isinstance(values, dict) and "body" not in values:
            for k in ("text", "content", "reply", "message"):
                if k in values and values.get(k) is not None:
                    values["body"] = values.get(k)
                    break
        return values


def _comments_body_max_len() -> int:
    try:
        return int(os.getenv("COMMENTS_BODY_MAX_LEN", "4000"))
    except Exception:
        return 4000


def _comment_to_dict(
    *,
    c,
    avatar_url: Optional[str] = None,
    replies: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Normalize a Comment (or a SQLAlchemy row with same attrs) to a stable JSON shape.
    """
    return {
        "id": getattr(c, "id", None),
        "target_type": getattr(c, "target_type", None),
        "document_id": getattr(c, "document_id", None),
        "page_number": getattr(c, "page_number", None),
        "image_page_id": getattr(c, "image_page_id", None),
        "parent_id": getattr(c, "parent_id", None),
        "username": getattr(c, "username", None),
        "avatar_url": avatar_url,
        "body": getattr(c, "body", None),
        "created_at": (getattr(c, "created_at", None).isoformat() if getattr(c, "created_at", None) else None),
        "likes_count": getattr(c, "likes_count", 0) or 0,
        "dislikes_count": getattr(c, "dislikes_count", 0) or 0,
        "replies": replies or [],
    }


def _ensure_comment_matches_context(
    *,
    comment,
    expected_target_type: str,
    expected_document_id: Optional[str] = None,
    expected_image_page_id: Optional[str] = None,
):
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if getattr(comment, "target_type", None) != expected_target_type:
        raise HTTPException(status_code=404, detail="Comment not found")
    if expected_document_id is not None and getattr(comment, "document_id", None) != expected_document_id:
        raise HTTPException(status_code=404, detail="Comment not found")
    if expected_image_page_id is not None and getattr(comment, "image_page_id", None) != expected_image_page_id:
        raise HTTPException(status_code=404, detail="Comment not found")


@app.get("/documents/{document_id}/comments")
async def get_document_comments(
    document_id: str,
    page_number: Optional[int] = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from database import get_db
    from models import Comment, Document

    with get_db() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

        q = (
            db.query(
                Comment.id.label("id"),
                Comment.target_type.label("target_type"),
                Comment.document_id.label("document_id"),
                Comment.page_number.label("page_number"),
                Comment.image_page_id.label("image_page_id"),
                Comment.parent_id.label("parent_id"),
                Comment.username.label("username"),
                Comment.body.label("body"),
                Comment.created_at.label("created_at"),
                Comment.likes_count.label("likes_count"),
                Comment.dislikes_count.label("dislikes_count"),
            )
            .filter(Comment.target_type == "document")
            .filter(Comment.document_id == document_id)
            .filter(Comment.parent_id.is_(None))
        )
        if page_number is not None:
            q = q.filter(Comment.page_number == page_number)

        top = q.order_by(Comment.created_at.desc()).offset(offset).limit(limit).all()
        top_ids = [r.id for r in top]

        replies_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        if top_ids:
            replies = (
                db.query(
                    Comment.id.label("id"),
                    Comment.target_type.label("target_type"),
                    Comment.document_id.label("document_id"),
                    Comment.page_number.label("page_number"),
                    Comment.image_page_id.label("image_page_id"),
                    Comment.parent_id.label("parent_id"),
                    Comment.username.label("username"),
                    Comment.body.label("body"),
                    Comment.created_at.label("created_at"),
                    Comment.likes_count.label("likes_count"),
                    Comment.dislikes_count.label("dislikes_count"),
                )
                .filter(Comment.parent_id.in_(top_ids))
                .order_by(Comment.created_at.asc())
                .all()
            )

            for r in replies:
                replies_by_parent.setdefault(r.parent_id, []).append(
                    {
                        "id": r.id,
                        "target_type": r.target_type,
                        "document_id": r.document_id,
                        "page_number": r.page_number,
                        "image_page_id": r.image_page_id,
                        "parent_id": r.parent_id,
                        "username": r.username,
                        "body": r.body,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "likes_count": r.likes_count or 0,
                        "dislikes_count": r.dislikes_count or 0,
                    }
                )

        # Add avatar URLs to comments and replies
        from comments.avatars import get_avatar_url
        
        comments = []
        for c in top:
            avatar_url = get_avatar_url(c.username)
            replies_with_avatars = []
            for r in replies_by_parent.get(c.id, []):
                replies_with_avatars.append({
                    **r,
                    "avatar_url": get_avatar_url(r["username"]),
                })
            comments.append(
                {
                    "id": c.id,
                    "target_type": c.target_type,
                    "document_id": c.document_id,
                    "page_number": c.page_number,
                    "image_page_id": c.image_page_id,
                    "parent_id": c.parent_id,
                    "username": c.username,
                    "avatar_url": avatar_url,
                    "body": c.body,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "likes_count": c.likes_count or 0,
                    "dislikes_count": c.dislikes_count or 0,
                    "replies": replies_with_avatars,
                }
            )

        return {
            "document_id": document_id,
            "page_number": page_number,
            "comments": comments,
            "count": len(comments),
            "limit": limit,
            "offset": offset,
        }


@app.get("/images/{image_page_id}/comments")
async def get_image_comments(
    image_page_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    from database import get_db
    from models import Comment, ImagePage

    with get_db() as db:
        img = db.query(ImagePage).filter(ImagePage.id == image_page_id).first()
        if not img:
            raise HTTPException(status_code=404, detail=f"Image page {image_page_id} not found")

        top = (
            db.query(
                Comment.id.label("id"),
                Comment.target_type.label("target_type"),
                Comment.document_id.label("document_id"),
                Comment.page_number.label("page_number"),
                Comment.image_page_id.label("image_page_id"),
                Comment.parent_id.label("parent_id"),
                Comment.username.label("username"),
                Comment.body.label("body"),
                Comment.created_at.label("created_at"),
                Comment.likes_count.label("likes_count"),
                Comment.dislikes_count.label("dislikes_count"),
            )
            .filter(Comment.target_type == "image")
            .filter(Comment.image_page_id == image_page_id)
            .filter(Comment.parent_id.is_(None))
            .order_by(Comment.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        top_ids = [r.id for r in top]

        replies_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        if top_ids:
            replies = (
                db.query(
                    Comment.id.label("id"),
                    Comment.target_type.label("target_type"),
                    Comment.document_id.label("document_id"),
                    Comment.page_number.label("page_number"),
                    Comment.image_page_id.label("image_page_id"),
                    Comment.parent_id.label("parent_id"),
                    Comment.username.label("username"),
                    Comment.body.label("body"),
                    Comment.created_at.label("created_at"),
                    Comment.likes_count.label("likes_count"),
                    Comment.dislikes_count.label("dislikes_count"),
                )
                .filter(Comment.parent_id.in_(top_ids))
                .order_by(Comment.created_at.asc())
                .all()
            )
            for r in replies:
                replies_by_parent.setdefault(r.parent_id, []).append(
                    {
                        "id": r.id,
                        "target_type": r.target_type,
                        "document_id": r.document_id,
                        "page_number": r.page_number,
                        "image_page_id": r.image_page_id,
                        "parent_id": r.parent_id,
                        "username": r.username,
                        "body": r.body,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "likes_count": r.likes_count or 0,
                        "dislikes_count": r.dislikes_count or 0,
                    }
                )

        # Add avatar URLs to comments and replies
        from comments.avatars import get_avatar_url
        
        comments = []
        for c in top:
            avatar_url = get_avatar_url(c.username)
            replies_with_avatars = []
            for r in replies_by_parent.get(c.id, []):
                replies_with_avatars.append({
                    **r,
                    "avatar_url": get_avatar_url(r["username"]),
                })
            comments.append(
                {
                    "id": c.id,
                    "target_type": c.target_type,
                    "document_id": c.document_id,
                    "page_number": c.page_number,
                    "image_page_id": c.image_page_id,
                    "parent_id": c.parent_id,
                    "username": c.username,
                    "avatar_url": avatar_url,
                    "body": c.body,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "likes_count": c.likes_count or 0,
                    "dislikes_count": c.dislikes_count or 0,
                    "replies": replies_with_avatars,
                }
            )

        return {
            "image_page_id": image_page_id,
            "comments": comments,
            "count": len(comments),
            "limit": limit,
            "offset": offset,
        }


@app.post("/documents/{document_id}/comments")
async def post_document_comment(document_id: str, req: CommentCreateRequest, request: Request):
    from database import get_db
    from models import Comment, Document

    username = _generate_random_username()
    _rate_limit_comments_or_429(request)
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body is required")
    if len(body) > _comments_body_max_len():
        raise HTTPException(status_code=400, detail="Comment body too long")

    # Generate avatar for this username
    from comments.avatars import generate_and_upload_avatar
    avatar_url = generate_and_upload_avatar(username)

    with get_db() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

        page_number = req.page_number
        if page_number is not None:
            if page_number < 1:
                raise HTTPException(status_code=400, detail="Invalid page_number")

            # Prefer a trustworthy Document.page_count; many docs may have default=1 even when multi-page.
            max_pages = doc.page_count if (doc.page_count and doc.page_count > 1) else 0

            from models import ImagePage
            img_pages = (
                db.query(ImagePage)
                .filter(ImagePage.document_id == document_id)
                .count()
            )
            if img_pages and img_pages > max_pages:
                max_pages = img_pages

            if max_pages and max_pages > 0 and page_number > max_pages:
                raise HTTPException(status_code=400, detail="page_number out of bounds")

        c = Comment(
            target_type="document",
            document_id=document_id,
            page_number=page_number,
            image_page_id=None,
            parent_id=None,
            username=username,
            body=body,
            ip_hash=_ip_hash(request),
        )
        db.add(c)
        db.flush()
        return {"comment": _comment_to_dict(c=c, avatar_url=avatar_url, replies=[])}


@app.post("/images/{image_page_id}/comments")
async def post_image_comment(image_page_id: str, req: CommentCreateRequest, request: Request):
    from database import get_db
    from models import Comment, ImagePage

    username = _generate_random_username()
    _rate_limit_comments_or_429(request)
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body is required")
    if len(body) > _comments_body_max_len():
        raise HTTPException(status_code=400, detail="Comment body too long")

    # Generate avatar for this username
    from comments.avatars import generate_and_upload_avatar
    avatar_url = generate_and_upload_avatar(username)

    with get_db() as db:
        img = db.query(ImagePage).filter(ImagePage.id == image_page_id).first()
        if not img:
            raise HTTPException(status_code=404, detail=f"Image page {image_page_id} not found")

        c = Comment(
            target_type="image",
            document_id=None,
            page_number=None,
            image_page_id=image_page_id,
            parent_id=None,
            username=username,
            body=body,
            ip_hash=_ip_hash(request),
        )
        db.add(c)
        db.flush()
        return {"comment": _comment_to_dict(c=c, avatar_url=avatar_url, replies=[])}


@app.post("/comments/{comment_id}/replies")
async def post_reply(comment_id: str, req: ReplyCreateRequest, request: Request):
    from database import get_db
    from models import Comment

    username = _generate_random_username()
    _rate_limit_comments_or_429(request)
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Reply body is required")
    if len(body) > _comments_body_max_len():
        raise HTTPException(status_code=400, detail="Reply body too long")

    # Generate avatar for this username
    from comments.avatars import generate_and_upload_avatar
    avatar_url = generate_and_upload_avatar(username)

    with get_db() as db:
        parent = db.query(Comment).filter(Comment.id == comment_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Comment not found")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Cannot reply to a reply")

        r = Comment(
            target_type=parent.target_type,
            document_id=parent.document_id,
            page_number=parent.page_number,
            image_page_id=parent.image_page_id,
            parent_id=parent.id,
            username=username,
            body=body,
            ip_hash=_ip_hash(request),
        )
        db.add(r)
        db.flush()
        return {"reply": _comment_to_dict(c=r, avatar_url=avatar_url, replies=[])}


@app.post("/comments/{comment_id}/like")
async def like_comment(comment_id: str, request: Request):
    """Like a comment or reply. One like per IP per comment (can toggle by liking again to remove)."""
    from database import get_db
    from models import Comment, CommentReaction
    
    _rate_limit_comments_or_429(request)
    ip_hash = _ip_hash(request)
    
    with get_db() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        # Check if user already reacted
        existing = db.query(CommentReaction).filter(
            CommentReaction.comment_id == comment_id,
            CommentReaction.ip_hash == ip_hash
        ).first()
        
        if existing:
            if existing.reaction_type == "like":
                # Already liked: remove like (toggle off)
                db.delete(existing)
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
                action = "removed"
            else:
                # Had a dislike: change to like
                existing.reaction_type = "like"
                comment.dislikes_count = max(0, (comment.dislikes_count or 0) - 1)
                comment.likes_count = (comment.likes_count or 0) + 1
                action = "changed_to_like"
        else:
            # New like
            reaction = CommentReaction(
                comment_id=comment_id,
                reaction_type="like",
                ip_hash=ip_hash,
            )
            db.add(reaction)
            comment.likes_count = (comment.likes_count or 0) + 1
            action = "added"
        
        db.flush()
        return {
            "comment_id": comment_id,
            "action": action,
            "likes_count": comment.likes_count or 0,
            "dislikes_count": comment.dislikes_count or 0,
            # Backward/forward compatible shape for UIs expecting a nested comment object.
            "comment": {
                "id": comment_id,
                "likes_count": comment.likes_count or 0,
                "dislikes_count": comment.dislikes_count or 0,
            },
        }


@app.post("/comments/{comment_id}/dislike")
async def dislike_comment(comment_id: str, request: Request):
    """Dislike a comment or reply. One dislike per IP per comment (can toggle by disliking again to remove)."""
    from database import get_db
    from models import Comment, CommentReaction
    
    _rate_limit_comments_or_429(request)
    ip_hash = _ip_hash(request)
    
    with get_db() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        # Check if user already reacted
        existing = db.query(CommentReaction).filter(
            CommentReaction.comment_id == comment_id,
            CommentReaction.ip_hash == ip_hash
        ).first()
        
        if existing:
            if existing.reaction_type == "dislike":
                # Already disliked: remove dislike (toggle off)
                db.delete(existing)
                comment.dislikes_count = max(0, (comment.dislikes_count or 0) - 1)
                action = "removed"
            else:
                # Had a like: change to dislike
                existing.reaction_type = "dislike"
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
                comment.dislikes_count = (comment.dislikes_count or 0) + 1
                action = "changed_to_dislike"
        else:
            # New dislike
            reaction = CommentReaction(
                comment_id=comment_id,
                reaction_type="dislike",
                ip_hash=ip_hash,
            )
            db.add(reaction)
            comment.dislikes_count = (comment.dislikes_count or 0) + 1
            action = "added"
        
        db.flush()
        return {
            "comment_id": comment_id,
            "action": action,
            "likes_count": comment.likes_count or 0,
            "dislikes_count": comment.dislikes_count or 0,
            # Backward/forward compatible shape for UIs expecting a nested comment object.
            "comment": {
                "id": comment_id,
                "likes_count": comment.likes_count or 0,
                "dislikes_count": comment.dislikes_count or 0,
            },
        }


# -------------------------------------------------------------------
# Compatibility aliases for frontends that use nested comment routes
# -------------------------------------------------------------------

@app.post("/images/{image_page_id}/comments/{comment_id}/replies")
async def post_image_reply(image_page_id: str, comment_id: str, req: ReplyCreateRequest, request: Request):
    """
    Alias for POST /comments/{comment_id}/replies scoped to an image page.
    """
    from database import get_db
    from models import Comment

    username = _generate_random_username()
    _rate_limit_comments_or_429(request)
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Reply body is required")
    if len(body) > _comments_body_max_len():
        raise HTTPException(status_code=400, detail="Reply body too long")

    from comments.avatars import generate_and_upload_avatar
    avatar_url = generate_and_upload_avatar(username)

    with get_db() as db:
        parent = db.query(Comment).filter(Comment.id == comment_id).first()
        _ensure_comment_matches_context(comment=parent, expected_target_type="image", expected_image_page_id=image_page_id)
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Cannot reply to a reply")

        r = Comment(
            target_type=parent.target_type,
            document_id=parent.document_id,
            page_number=parent.page_number,
            image_page_id=parent.image_page_id,
            parent_id=parent.id,
            username=username,
            body=body,
            ip_hash=_ip_hash(request),
        )
        db.add(r)
        db.flush()
        return {"reply": _comment_to_dict(c=r, avatar_url=avatar_url, replies=[])}


@app.post("/documents/{document_id}/comments/{comment_id}/replies")
async def post_document_reply(document_id: str, comment_id: str, req: ReplyCreateRequest, request: Request):
    """
    Alias for POST /comments/{comment_id}/replies scoped to a document.
    """
    from database import get_db
    from models import Comment

    username = _generate_random_username()
    _rate_limit_comments_or_429(request)
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Reply body is required")
    if len(body) > _comments_body_max_len():
        raise HTTPException(status_code=400, detail="Reply body too long")

    from comments.avatars import generate_and_upload_avatar
    avatar_url = generate_and_upload_avatar(username)

    with get_db() as db:
        parent = db.query(Comment).filter(Comment.id == comment_id).first()
        _ensure_comment_matches_context(comment=parent, expected_target_type="document", expected_document_id=document_id)
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Cannot reply to a reply")

        r = Comment(
            target_type=parent.target_type,
            document_id=parent.document_id,
            page_number=parent.page_number,
            image_page_id=parent.image_page_id,
            parent_id=parent.id,
            username=username,
            body=body,
            ip_hash=_ip_hash(request),
        )
        db.add(r)
        db.flush()
        return {"reply": _comment_to_dict(c=r, avatar_url=avatar_url, replies=[])}


@app.post("/images/{image_page_id}/comments/{comment_id}/like")
async def like_image_comment(image_page_id: str, comment_id: str, request: Request):
    """
    Alias for POST /comments/{comment_id}/like scoped to an image page.
    """
    from database import get_db
    from models import Comment

    _rate_limit_comments_or_429(request)
    ip_hash = _ip_hash(request)

    with get_db() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        _ensure_comment_matches_context(comment=comment, expected_target_type="image", expected_image_page_id=image_page_id)

        from models import CommentReaction
        existing = db.query(CommentReaction).filter(
            CommentReaction.comment_id == comment_id,
            CommentReaction.ip_hash == ip_hash,
        ).first()

        if existing:
            if existing.reaction_type == "like":
                db.delete(existing)
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
                action = "removed"
            else:
                existing.reaction_type = "like"
                comment.dislikes_count = max(0, (comment.dislikes_count or 0) - 1)
                comment.likes_count = (comment.likes_count or 0) + 1
                action = "changed_to_like"
        else:
            reaction = CommentReaction(comment_id=comment_id, reaction_type="like", ip_hash=ip_hash)
            db.add(reaction)
            comment.likes_count = (comment.likes_count or 0) + 1
            action = "added"

        db.flush()
        return {
            "comment_id": comment_id,
            "action": action,
            "likes_count": comment.likes_count or 0,
            "dislikes_count": comment.dislikes_count or 0,
            "comment": {"id": comment_id, "likes_count": comment.likes_count or 0, "dislikes_count": comment.dislikes_count or 0},
        }


@app.post("/images/{image_page_id}/comments/{comment_id}/dislike")
async def dislike_image_comment(image_page_id: str, comment_id: str, request: Request):
    """
    Alias for POST /comments/{comment_id}/dislike scoped to an image page.
    """
    from database import get_db
    from models import Comment

    _rate_limit_comments_or_429(request)
    ip_hash = _ip_hash(request)

    with get_db() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        _ensure_comment_matches_context(comment=comment, expected_target_type="image", expected_image_page_id=image_page_id)

        from models import CommentReaction
        existing = db.query(CommentReaction).filter(
            CommentReaction.comment_id == comment_id,
            CommentReaction.ip_hash == ip_hash,
        ).first()

        if existing:
            if existing.reaction_type == "dislike":
                db.delete(existing)
                comment.dislikes_count = max(0, (comment.dislikes_count or 0) - 1)
                action = "removed"
            else:
                existing.reaction_type = "dislike"
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
                comment.dislikes_count = (comment.dislikes_count or 0) + 1
                action = "changed_to_dislike"
        else:
            reaction = CommentReaction(comment_id=comment_id, reaction_type="dislike", ip_hash=ip_hash)
            db.add(reaction)
            comment.dislikes_count = (comment.dislikes_count or 0) + 1
            action = "added"

        db.flush()
        return {
            "comment_id": comment_id,
            "action": action,
            "likes_count": comment.likes_count or 0,
            "dislikes_count": comment.dislikes_count or 0,
            "comment": {"id": comment_id, "likes_count": comment.likes_count or 0, "dislikes_count": comment.dislikes_count or 0},
        }


@app.post("/documents/{document_id}/comments/{comment_id}/like")
async def like_document_comment(document_id: str, comment_id: str, request: Request):
    """
    Alias for POST /comments/{comment_id}/like scoped to a document.
    """
    from database import get_db
    from models import Comment

    _rate_limit_comments_or_429(request)
    ip_hash = _ip_hash(request)

    with get_db() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        _ensure_comment_matches_context(comment=comment, expected_target_type="document", expected_document_id=document_id)

        from models import CommentReaction
        existing = db.query(CommentReaction).filter(
            CommentReaction.comment_id == comment_id,
            CommentReaction.ip_hash == ip_hash,
        ).first()

        if existing:
            if existing.reaction_type == "like":
                db.delete(existing)
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
                action = "removed"
            else:
                existing.reaction_type = "like"
                comment.dislikes_count = max(0, (comment.dislikes_count or 0) - 1)
                comment.likes_count = (comment.likes_count or 0) + 1
                action = "changed_to_like"
        else:
            reaction = CommentReaction(comment_id=comment_id, reaction_type="like", ip_hash=ip_hash)
            db.add(reaction)
            comment.likes_count = (comment.likes_count or 0) + 1
            action = "added"

        db.flush()
        return {
            "comment_id": comment_id,
            "action": action,
            "likes_count": comment.likes_count or 0,
            "dislikes_count": comment.dislikes_count or 0,
            "comment": {"id": comment_id, "likes_count": comment.likes_count or 0, "dislikes_count": comment.dislikes_count or 0},
        }


@app.post("/documents/{document_id}/comments/{comment_id}/dislike")
async def dislike_document_comment(document_id: str, comment_id: str, request: Request):
    """
    Alias for POST /comments/{comment_id}/dislike scoped to a document.
    """
    from database import get_db
    from models import Comment

    _rate_limit_comments_or_429(request)
    ip_hash = _ip_hash(request)

    with get_db() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        _ensure_comment_matches_context(comment=comment, expected_target_type="document", expected_document_id=document_id)

        from models import CommentReaction
        existing = db.query(CommentReaction).filter(
            CommentReaction.comment_id == comment_id,
            CommentReaction.ip_hash == ip_hash,
        ).first()

        if existing:
            if existing.reaction_type == "dislike":
                db.delete(existing)
                comment.dislikes_count = max(0, (comment.dislikes_count or 0) - 1)
                action = "removed"
            else:
                existing.reaction_type = "dislike"
                comment.likes_count = max(0, (comment.likes_count or 0) - 1)
                comment.dislikes_count = (comment.dislikes_count or 0) + 1
                action = "changed_to_dislike"
        else:
            reaction = CommentReaction(comment_id=comment_id, reaction_type="dislike", ip_hash=ip_hash)
            db.add(reaction)
            comment.dislikes_count = (comment.dislikes_count or 0) + 1
            action = "added"

        db.flush()
        return {
            "comment_id": comment_id,
            "action": action,
            "likes_count": comment.likes_count or 0,
            "dislikes_count": comment.dislikes_count or 0,
            "comment": {"id": comment_id, "likes_count": comment.likes_count or 0, "dislikes_count": comment.dislikes_count or 0},
        }


@app.get("/search", response_model=SearchResponse)
async def search_get(
    q: str = Query(..., description="Search query"),
    search_type: str = Query("keyword", description="Search type: keyword, fuzzy, semantic, phrase"),
    limit: int = Query(50, ge=1, le=200),
    fuzzy_threshold: Optional[float] = Query(0.6, ge=0.0, le=1.0)
):
    """GET endpoint for search (same as POST)."""
    request = SearchRequest(
        query=q,
        search_type=search_type,
        limit=limit,
        fuzzy_threshold=fuzzy_threshold
    )
    return await search(request)


@app.post("/search/entity", response_model=EntitySearchResponse)
async def search_entity(request: EntitySearchRequest):
    """
    Search for specific entities (names, emails, phones, dates).
    
    Entity types: name, email, phone, date, keyword
    """
    try:
        results = search_engine.entity_search(
            request.entity_type,
            request.entity_value,
            request.limit
        )
        
        entity_results = [
            EntityResult(**result) for result in results
        ]
        
        return EntitySearchResponse(
            results=entity_results,
            count=len(entity_results),
            entity_type=request.entity_type,
            entity_value=request.entity_value
        )
    except Exception as e:
        logger.error(f"Entity search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search/entity", response_model=EntitySearchResponse)
async def search_entity_get(
    entity_type: str = Query(..., description="Entity type: name, email, phone, date, keyword"),
    entity_value: str = Query(..., description="Entity value to search for"),
    limit: int = Query(50, ge=1, le=200)
):
    """GET endpoint for entity search."""
    request = EntitySearchRequest(
        entity_type=entity_type,
        entity_value=entity_value,
        limit=limit
    )
    return await search_entity(request)


# ============================================
# Chat API (EpsteinGPT)
# ============================================

class ChatMessage(BaseModel):
    """A single chat message."""
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    """Request for chat API."""
    messages: List[ChatMessage]
    top_k: Optional[int] = 8  # Max passages to retrieve
    search_type: Optional[str] = "keyword"  # "keyword" | "phrase" | "fuzzy" | "semantic"
    debug: Optional[bool] = False


class Citation(BaseModel):
    """A citation to a document passage."""
    document_id: str
    page_id: Optional[str] = None
    page_number: int
    snippet: str
    score: float
    file_url: str
    thumbnail_url: str


class ChatResponse(BaseModel):
    """Response from chat API."""
    answer: str
    citations: List[Citation]
    debug: Optional[Dict[str, Any]] = None



@app.post("/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, http_request: Request):
    """
    Stateless chat API for EpsteinGPT.
    
    Requires X-API-Key header with valid API key (checked by middleware).
    
    Returns:
    - answer: The model's answer in plain text
    - citations: List of document citations used in the answer
    - debug: Optional debug information (if debug=true in request)
    """
    # Note: API key is already verified by APIKeyMiddleware, no need to check again
    
    # Extract latest user question
    user_messages = [msg for msg in request_body.messages if msg.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="At least one user message is required")
    
    user_question = user_messages[-1].content.strip()
    if not user_question:
        raise HTTPException(status_code=400, detail="User question cannot be empty")
    
    # Guardrails: max message length
    max_message_length = 2000
    if len(user_question) > max_message_length:
        raise HTTPException(
            status_code=400,
            detail=f"User question too long (max {max_message_length} characters)"
        )
    
    # Guardrails: max total context length
    total_context = sum(len(msg.content) for msg in request_body.messages)
    max_context_length = 10000
    if total_context > max_context_length:
        raise HTTPException(
            status_code=400,
            detail=f"Total context too long (max {max_context_length} characters)"
        )
    
    # Guardrails: hard cap top_k
    top_k = min(request_body.top_k or 8, 20)  # Max 20 passages
    
    try:
        # Retrieve passages
        from chat.retriever import retrieve_passages
        
        search_type = request_body.search_type or "keyword"
        if search_type not in ["keyword", "phrase", "fuzzy", "semantic"]:
            search_type = "keyword"
        
        citations = retrieve_passages(user_question, top_k=top_k, search_type=search_type)
        
        # Build conversation history (last N messages, excluding current question)
        conversation_history = []
        for msg in request_body.messages[:-1]:  # Exclude the last (current) user message
            conversation_history.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # Generate answer using Bedrock
        from chat.bedrock_chat import generate_answer
        
        bedrock_result = generate_answer(
            user_question=user_question,
            evidence_passages=citations,
            conversation_history=conversation_history if conversation_history else None,
            max_tokens=2000,
        )
        
        # Map citations to response format
        citation_models = []
        for cit in citations:
            citation_models.append(Citation(
                document_id=cit["document_id"],
                page_id=cit.get("page_id"),
                page_number=cit["page_number"],
                snippet=cit["snippet"],
                score=cit["score"],
                file_url=cit["file_url"],
                thumbnail_url=cit["thumbnail_url"],
            ))
        
        # Build response - convert markdown to plain text if needed
        answer_text = bedrock_result.get("answer_markdown", "") or ""
        # Remove markdown formatting for cleaner output
        import re
        # Remove markdown headers, bold, italic, code blocks, etc.
        answer_text = re.sub(r'#{1,6}\s+', '', answer_text)  # Remove headers
        answer_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', answer_text)  # Remove bold
        answer_text = re.sub(r'\*([^*]+)\*', r'\1', answer_text)  # Remove italic
        answer_text = re.sub(r'`([^`]+)`', r'\1', answer_text)  # Remove inline code
        answer_text = re.sub(r'```[\s\S]*?```', '', answer_text)  # Remove code blocks
        answer_text = re.sub(r'^\s*[-*+]\s+', '', answer_text, flags=re.MULTILINE)  # Remove list markers
        answer_text = answer_text.strip()
        
        # Fallback: if Bedrock didn't generate an answer but we have citations, provide a basic answer
        if not answer_text and citation_models:
            doc_ids = list(set(cit.document_id for cit in citation_models[:5]))  # Unique document IDs, max 5
            if len(doc_ids) == 1:
                answer_text = f"I found 1 document that mentions \"{user_question}\". See the citations below for details."
            else:
                answer_text = f"I found {len(citation_models)} relevant passages across {len(doc_ids)} documents related to \"{user_question}\". See the citations below for details."
        
        response = ChatResponse(
            answer=answer_text,
            citations=citation_models,
        )
        
        # Add debug info if requested
        if request_body.debug:
            response.debug = {
                "query": user_question,
                "passages_retrieved": len(citations),
                "search_type": search_type,
                "top_k": top_k,
            }
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat API error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat API error: {str(e)}")


@app.get("/stats")
async def get_stats():
    """Get system statistics."""
    from database import get_db
    from models import Document, ImagePage, OCRText, Entity, SearchIndex, ImageLabel
    
    try:
        with get_db() as db:
            doc_count = db.query(Document).count()
            page_count = db.query(ImagePage).count()
            ocr_count = db.query(OCRText).count()
            entity_count = db.query(Entity).count()
            search_index_count = db.query(SearchIndex).count()
            
            # Try to get label count (may not exist in older DBs)
            try:
                label_count = db.query(ImageLabel).count()
            except:
                label_count = 0
            
            processed_pages = db.query(ImagePage).filter(
                ImagePage.ocr_processed == True
            ).count()
    except Exception as e:
        logger.exception(f"Stats query failed: {e}")
        msg = str(e)
        if "no such column" in msg and "doc_metadata" in msg:
            raise HTTPException(
                status_code=500,
                detail=(
                    "SQLite schema is from an older version (documents.metadata). "
                    "Delete ./data/ocr.db and rerun the worker so tables are recreated."
                ),
            )
        raise HTTPException(
            status_code=500,
            detail=(
                "Database not initialized or unreachable. Check logs. "
                "If you previously ran an older version, delete ./data/ocr.db and restart."
            )
        )
    
    return {
        "documents": doc_count,
        "image_pages": page_count,
        "ocr_texts": ocr_count,
        "entities": entity_count,
        "image_labels": label_count,
        "search_index_rows": search_index_count,
        "processed_pages": processed_pages,
        "processing_rate": f"{processed_pages}/{page_count}" if page_count > 0 else "0/0"
    }


@app.get("/suggest/entities")
async def suggest_entities(
    entity_type: str = Query("name", description="Entity type: name, email, phone, date"),
    limit: int = Query(50, ge=1, le=500),
):
    """
    Return the most common detected entities so you can copy/paste terms that OCR actually recognized.
    """
    from database import get_db
    from models import Entity
    from sqlalchemy import func

    with get_db() as db:
        rows = (
            db.query(Entity.entity_value, func.count(Entity.id).label("count"))
            .filter(Entity.entity_type == entity_type)
            .group_by(Entity.entity_value)
            .order_by(func.count(Entity.id).desc())
            .limit(limit)
            .all()
        )

    return {
        "entity_type": entity_type,
        "results": [{"value": v, "count": int(c)} for (v, c) in rows],
        "count": len(rows),
    }


@app.get("/suggest/tokens")
async def suggest_tokens(
    limit: int = Query(100, ge=1, le=1000),
    min_len: int = Query(5, ge=1, le=30),
):
    """
    Return frequent OCR tokens from the search index (useful when exact names aren't detected).
    """
    from database import get_db
    from models import SearchIndex

    # A small, high-signal stoplist (OCR corpora often contain these).
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "have", "are", "was", "were",
        "page", "pages", "fax", "phone", "email", "date", "subject", "to", "cc", "bcc",
        "http", "https", "www", "com",
    }

    token_counts = Counter()
    with get_db() as db:
        rows = db.query(SearchIndex.tokens).all()

    for (tokens,) in rows:
        for t in (tokens or []):
            if not isinstance(t, str):
                continue
            t = t.strip().lower()
            if len(t) < min_len:
                continue
            if t in stop:
                continue
            if not re.match(r"^[a-z0-9]+$", t):
                continue
            token_counts[t] += 1

    top = token_counts.most_common(limit)
    return {
        "min_len": min_len,
        "results": [{"token": tok, "count": int(cnt)} for (tok, cnt) in top],
        "count": len(top),
    }


# ============================================
# Image Label Search (AWS Rekognition)
# ============================================

class LabelSearchResult(BaseModel):
    """Result from label search."""
    image_page_id: str
    document_id: str
    label_name: str
    confidence: float
    image_path: str
    has_bbox: bool
    bbox: Optional[Dict[str, float]] = None
    parent_labels: Optional[List[str]] = None
    categories: Optional[List[str]] = None


class LabelSearchResponse(BaseModel):
    """Response for label search."""
    results: List[LabelSearchResult]
    count: int
    query: str


@app.get("/search/labels")
async def search_labels(
    q: str = Query(..., description="Label to search for (e.g., 'Floor', 'Person', 'Car')"),
    min_confidence: float = Query(70.0, ge=0, le=100, description="Minimum confidence"),
    limit: int = Query(50, ge=1, le=200)
):
    """
    Search images by detected labels (objects, scenes, concepts).
    
    Labels are detected by AWS Rekognition. Examples:
    - Objects: Person, Car, Dog, Phone, Computer
    - Scenes: Indoors, Outdoors, Beach, Office
    - Concepts: Document, Text, Handwriting
    """
    from database import get_db
    from models import ImageLabel, ImagePage
    
    try:
        with get_db() as db:
            # Search for labels (case-insensitive)
            query_lower = q.lower()
            
            labels = db.query(ImageLabel, ImagePage).join(
                ImagePage, ImageLabel.image_page_id == ImagePage.id
            ).filter(
                ImageLabel.label_name_lower.contains(query_lower),
                ImageLabel.confidence >= min_confidence
            ).order_by(
                ImageLabel.confidence.desc()
            ).limit(limit).all()
            
            results = []
            for label, page in labels:
                result = {
                    "image_page_id": label.image_page_id,
                    "document_id": label.document_id,
                    "label_name": label.label_name,
                    "confidence": label.confidence,
                    "image_path": page.image_path,
                    "has_bbox": label.has_bbox,
                    "parent_labels": label.parent_labels,
                    "categories": label.categories
                }
                
                if label.has_bbox:
                    result["bbox"] = {
                        "left": label.bbox_left,
                        "top": label.bbox_top,
                        "width": label.bbox_width,
                        "height": label.bbox_height
                    }
                
                results.append(result)
            
            return {
                "results": results,
                "count": len(results),
                "query": q
            }
            
    except Exception as e:
        logger.error(f"Label search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/suggest/labels")
async def suggest_labels(
    limit: int = Query(50, ge=1, le=200),
    min_confidence: float = Query(80.0, ge=0, le=100)
):
    """
    Get the most common detected labels across all images.
    
    Useful to discover what objects/scenes are present in the dataset.
    """
    from database import get_db
    from models import ImageLabel
    from sqlalchemy import func
    
    try:
        with get_db() as db:
            rows = (
                db.query(
                    ImageLabel.label_name,
                    func.count(ImageLabel.id).label("count"),
                    func.avg(ImageLabel.confidence).label("avg_confidence")
                )
                .filter(ImageLabel.confidence >= min_confidence)
                .group_by(ImageLabel.label_name)
                .order_by(func.count(ImageLabel.id).desc())
                .limit(limit)
                .all()
            )
            
            return {
                "labels": [
                    {
                        "name": name,
                        "count": int(count),
                        "avg_confidence": round(float(avg_conf), 1)
                    }
                    for name, count, avg_conf in rows
                ],
                "count": len(rows)
            }
            
    except Exception as e:
        logger.error(f"Suggest labels error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process/labels")
async def process_labels(
    limit: int = Query(100, ge=1, le=1000, description="Max pages to process")
):
    """
    Process images through AWS Rekognition to detect labels.
    
    This runs label detection on unprocessed image pages.
    Requires AWS credentials to be configured.
    """
    from database import get_db
    from models import ImagePage, ImageLabel
    from ocr.rekognition import RekognitionProcessor
    
    processor = RekognitionProcessor()
    
    if not processor.enabled:
        raise HTTPException(
            status_code=400,
            detail="AWS Rekognition not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )
    
    # Find pages without labels
    with get_db() as db:
        # Get pages that don't have any labels yet
        processed_page_ids = db.query(ImageLabel.image_page_id).distinct().subquery()
        
        unprocessed = db.query(ImagePage.id).filter(
            ~ImagePage.id.in_(processed_page_ids)
        ).limit(limit).all()
        
        page_ids = [row[0] for row in unprocessed]
    
    # Process pages
    total_labels = 0
    processed = 0
    
    for page_id in page_ids:
        labels_added = processor.process_image_page(page_id)
        total_labels += labels_added
        processed += 1
    
    return {
        "pages_processed": processed,
        "labels_added": total_labels,
        "remaining": len(page_ids) - processed if len(page_ids) > processed else 0
    }


# ============================================
# File and Image Serving Endpoints
# ============================================

_IMAGE_S3_KEY_CACHE: Dict[str, Dict[str, Any]] = {}


def _resolve_s3_image_key(page_id: str) -> Optional[str]:
    """
    Best-effort: image pages may be stored as png/jpg/jpeg depending on the pipeline.
    Cache the resolved key in-process to avoid repeated HEADs.
    """
    now = time.time()
    hit = _IMAGE_S3_KEY_CACHE.get(page_id)
    if hit and hit.get("exp_ts", 0) > now and hit.get("key"):
        return hit["key"]

    if not Config.S3_BUCKET:
        return None
    try:
        import boto3
        s3 = boto3.client("s3", region_name=Config.S3_REGION or "us-east-1")
        base = f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}"
        candidates = [f"{base}.png", f"{base}.jpg", f"{base}.jpeg"]
        for k in candidates:
            try:
                s3.head_object(Bucket=Config.S3_BUCKET, Key=k)
                _IMAGE_S3_KEY_CACHE[page_id] = {"key": k, "exp_ts": now + 3600}
                return k
            except Exception:
                continue
    except Exception:
        return None
    return None


def _serve_image_by_id(page_id: str):
    """
    Shared helper for serving an ImagePage PNG (local dev or S3).
    """
    from database import get_db
    from models import ImagePage

    # Handle both with and without extension
    page_id = page_id.replace(".png", "").replace(".jpg", "")

    with get_db() as db:
        page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
        if not page:
            raise HTTPException(status_code=404, detail=f"Image page {page_id} not found")
        image_path = Path(page.image_path)

    if image_path.exists():
        # Let FileResponse infer the correct type from the file; default to png for consistency.
        return FileResponse(path=image_path, media_type="image/png", filename=f"{page_id}.png")

    if Config.S3_BUCKET:
        key = _resolve_s3_image_key(page_id) or f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}.png"
        url = _presign_cached(key=key)
        return RedirectResponse(url=url, status_code=302, headers={"Cache-Control": "private, max-age=300"})

    raise HTTPException(status_code=404, detail=f"Image file not found: {image_path}")


@app.get("/images/{page_id}")
async def get_image(page_id: str):
    """
    Serve an extracted page image by page ID.
    
    Example: GET /images/1b433488ca0ef07d_page_0001
    Returns: PNG image
    """
    return _serve_image_by_id(page_id)


@app.get("/images/{page_id}/share")
async def get_image_share_url(page_id: str):
    """
    Returns a share link for a specific image page.
    Intended for the Photos app so it can share a photo without exposing per-page share URLs in document listings.
    """
    from database import get_db
    from models import ImagePage

    page_id = page_id.replace(".png", "").replace(".jpg", "")
    with get_db() as db:
        page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
        if not page:
            raise HTTPException(status_code=404, detail=f"Image page {page_id} not found")
    return {"page_id": page_id, "share_url": _share_path("i", page_id)}


@app.get("/thumbnails/{page_id}")
async def get_thumbnail(
    page_id: str,
    width: int = Query(300, ge=50, le=800, description="Thumbnail width")
):
    """
    Serve a resized thumbnail of a page image.
    
    Example: GET /thumbnails/1b433488ca0ef07d_page_0001?width=200
    Returns: PNG thumbnail
    """
    from database import get_db
    from models import ImagePage
    from PIL import Image
    
    # Handle both with and without extension
    page_id = page_id.replace('.png', '').replace('.jpg', '')
    
    with get_db() as db:
        page = db.query(ImagePage).filter(ImagePage.id == page_id).first()
        if not page:
            raise HTTPException(status_code=404, detail=f"Image page {page_id} not found")

        # Capture primitive values before leaving session to avoid DetachedInstanceError
        _document_id = page.document_id
        _page_number = page.page_number
        image_path = Path(page.image_path)
    
    # If local disk doesn't have it (ECS/Fargate), fall back to S3 or render from PDF.
    if not image_path.exists():
        if Config.S3_BUCKET:
            try:
                import boto3
                import botocore
                s3 = boto3.client("s3", region_name=Config.S3_REGION or "us-east-1")

                # Optional: cache thumbnails in S3 so repeated requests are fast.
                thumb_key = f"thumbnails/{page_id}_w{width}.png"
                try:
                    s3.head_object(Bucket=Config.S3_BUCKET, Key=thumb_key)
                    url = _presign_cached(key=thumb_key)
                    return RedirectResponse(
                        url=url,
                        status_code=302,
                        headers={"Cache-Control": "private, max-age=300"},
                    )
                except Exception:
                    pass

                # Try to read the page image from S3 (png/jpg). If not present, render from PDF.
                candidate_img_keys = [
                    f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}.png",
                    f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}.jpg",
                    f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}.jpeg",
                ]
                raw = None
                for img_key in candidate_img_keys:
                    try:
                        obj = s3.get_object(Bucket=Config.S3_BUCKET, Key=img_key)
                        raw = obj["Body"].read()
                        break
                    except Exception as e:
                        # If key missing, keep trying other extensions.
                        msg = str(e)
                        if "NoSuchKey" in msg or "Not Found" in msg:
                            continue
                        continue

                if raw is None:
                    # Render the page image from the PDF using PyMuPDF.
                    from models import Document
                    import fitz  # PyMuPDF

                    with get_db() as db:
                        doc = db.query(Document).filter(Document.id == _document_id).first()
                        if not doc:
                            raise HTTPException(status_code=404, detail="Document not found for page")
                        source_url = doc.source_url
                        file_type = doc.file_type or "pdf"

                    # Ensure PDF is available in S3; if missing, download from source_url and upload.
                    candidate_pdf_keys = [
                        f"{Config.S3_FILES_PREFIX.rstrip('/')}/{_document_id}.{file_type}",
                        f"{Config.S3_FILES_PREFIX.rstrip('/')}/{_document_id}.pdf",
                    ]

                    pdf_bytes = None
                    for k in candidate_pdf_keys:
                        try:
                            obj = s3.get_object(Bucket=Config.S3_BUCKET, Key=k)
                            pdf_bytes = obj["Body"].read()
                            break
                        except Exception as e:
                            msg = str(e)
                            if "NoSuchKey" in msg or "Not Found" in msg:
                                continue
                            continue

                    if pdf_bytes is None:
                        import httpx
                        try:
                            r = httpx.get(source_url, timeout=60.0, follow_redirects=True)
                            r.raise_for_status()
                            pdf_bytes = r.content
                            # Upload PDF for future requests (best-effort).
                            try:
                                s3.put_object(
                                    Bucket=Config.S3_BUCKET,
                                    Key=candidate_pdf_keys[-1],
                                    Body=pdf_bytes,
                                    ContentType="application/pdf",
                                    CacheControl="public, max-age=31536000",
                                )
                            except Exception as e2:
                                logger.debug(f"PDF cache upload skipped: {e2}")
                        except Exception as e3:
                            logger.error(f"Failed to download PDF for thumbnail render: {e3}")
                            raise HTTPException(status_code=404, detail="Source PDF not available")

                    # Render page to image bytes.
                    doc_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page_index = max(int(_page_number) - 1, 0)
                    pdf_page = doc_pdf.load_page(page_index)
                    mat = fitz.Matrix(2.0, 2.0)  # good quality
                    pix = pdf_page.get_pixmap(matrix=mat, alpha=False)
                    raw = pix.tobytes("png")

                    # Upload rendered full page image to S3 so /images works too (best-effort).
                    try:
                        full_img_key = f"{Config.S3_IMAGES_PREFIX.rstrip('/')}/{page_id}.png"
                        s3.put_object(
                            Bucket=Config.S3_BUCKET,
                            Key=full_img_key,
                            Body=raw,
                            ContentType="image/png",
                            CacheControl="public, max-age=31536000",
                        )
                    except Exception as e4:
                        logger.debug(f"Rendered page upload skipped: {e4}")

                img = Image.open(BytesIO(raw))
                ratio = width / img.width
                height = int(img.height * ratio)
                img.thumbnail((width, height), Image.Resampling.LANCZOS)

                buffer = BytesIO()
                img.save(buffer, format="PNG")
                buffer.seek(0)

                # Best-effort upload for caching (requires s3:PutObject).
                try:
                    s3.put_object(
                        Bucket=Config.S3_BUCKET,
                        Key=thumb_key,
                        Body=buffer.getvalue(),
                        ContentType="image/png",
                        CacheControl="public, max-age=31536000",
                    )
                except Exception as e:
                    logger.debug(f"Thumbnail cache upload skipped: {e}")

                buffer.seek(0)
                return StreamingResponse(
                    buffer,
                    media_type="image/png",
                    headers={"Content-Disposition": f"inline; filename={page_id}_thumb.png"},
                )
            except Exception as e:
                logger.error(f"Error generating S3 thumbnail: {e}")
                raise HTTPException(status_code=500, detail="Failed to generate thumbnail")

        raise HTTPException(status_code=404, detail=f"Image file not found")
    
    # Generate thumbnail
    try:
        img = Image.open(image_path)
        # Calculate height maintaining aspect ratio
        ratio = width / img.width
        height = int(img.height * ratio)
        img.thumbnail((width, height), Image.Resampling.LANCZOS)
        
        # Save to bytes
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type="image/png",
            headers={"Content-Disposition": f"inline; filename={page_id}_thumb.png"}
        )
    except Exception as e:
        logger.error(f"Error generating thumbnail: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate thumbnail")


@app.get("/files/{document_id}")
async def get_file(document_id: str):
    """
    Serve the original document file (PDF) by document ID.
    
    Example: GET /files/1b433488ca0ef07d
    Returns: Original PDF file
    """
    return _serve_document_file_by_id(document_id)


def _serve_document_file_by_id(document_id: str):
    """
    Shared helper for serving a Document file (local dev or S3, with presigned redirects).
    """
    from database import get_db
    from models import Document

    def _get_or_refresh_presigned_url(doc: Document, key: str, *, file_name: str, file_type: str) -> str:
        """
        Cache presigned URL in DB so repeated opens don't create a new URL each time.
        This stabilizes the URL so the browser can cache the PDF between opens.
        """
        now = datetime.utcnow()
        # Refresh if expiring soon (60s buffer) or key changed.
        if doc.s3_key_files != key:
            doc.s3_key_files = key
            doc.s3_presigned_url = None
            doc.s3_presigned_expires_at = None

        if doc.s3_presigned_url and doc.s3_presigned_expires_at:
            if doc.s3_presigned_expires_at > (now + timedelta(seconds=60)):
                return doc.s3_presigned_url

        from storage.s3_assets import presign_get
        disposition = f'inline; filename="{file_name}"' if file_name else "inline"
        # Ensure PDFs render inline even if the S3 object has a wrong ContentType.
        response_ct = "application/pdf" if file_type == "pdf" else None
        url = presign_get(key, response_content_type=response_ct, response_content_disposition=disposition)
        doc.s3_presigned_url = url
        doc.s3_presigned_expires_at = now + timedelta(seconds=Config.S3_PRESIGN_EXPIRES_SECONDS)
        return url

    with get_db() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

        file_name = doc.file_name
        file_type = (doc.file_type or "pdf").lower()
        source_url = doc.source_url
    
        # Look for the stored file locally (dev). ECS disk is ephemeral, so usually misses.
        file_path = Config.STORAGE_PATH / f"{document_id}.{file_type}"
        if not file_path.exists():
            file_path = Config.STORAGE_PATH / f"{document_id}.pdf"

        if file_path.exists():
            media_types = {
                "pdf": "application/pdf",
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
            }
            media_type = media_types.get(file_type, "application/octet-stream")
            return FileResponse(path=file_path, media_type=media_type, filename=file_name)

        # If running on ECS/Fargate, files should be on S3.
        if Config.S3_BUCKET:
            import boto3

            s3 = boto3.client("s3", region_name=Config.S3_REGION or "us-east-1")

            # Fast path: if we already know the S3 key, skip HEAD checks and reuse cached presigned URL.
            if doc.s3_key_files:
                url = _get_or_refresh_presigned_url(doc, doc.s3_key_files, file_name=file_name, file_type=file_type)
                return RedirectResponse(url=url, status_code=302)

            # Otherwise, do a ONE-TIME existence check to discover the canonical key and store it.
            # Prefer .pdf for PDFs (our upload convention), otherwise use the stored file_type.
            ext = "pdf" if file_type == "pdf" else file_type
            computed_key = f"{Config.S3_FILES_PREFIX.rstrip('/')}/{document_id}.{ext}"

            try:
                s3.head_object(Bucket=Config.S3_BUCKET, Key=computed_key)
                url = _get_or_refresh_presigned_url(doc, computed_key, file_name=file_name, file_type=file_type)
                return RedirectResponse(url=url, status_code=302)
            except Exception:
                pass

            # Not in S3 under computed key: fetch from source_url, upload, store key, and redirect.
            try:
                import httpx
                resp = httpx.get(source_url, timeout=60.0, follow_redirects=True)
                resp.raise_for_status()
                content_type = "application/pdf" if ext == "pdf" else "application/octet-stream"
                s3.put_object(
                    Bucket=Config.S3_BUCKET,
                    Key=computed_key,
                    Body=resp.content,
                    ContentType=content_type,
                    CacheControl="public, max-age=31536000",
                )
                url = _get_or_refresh_presigned_url(doc, computed_key, file_name=file_name, file_type=file_type)
                return RedirectResponse(url=url, status_code=302)
            except Exception as e:
                logger.error(f"Failed to fetch+upload file for {document_id}: {e}")
                raise HTTPException(status_code=404, detail="Document file not found in S3 and fetch failed")
    
    raise HTTPException(status_code=404, detail="Document file not found")
    
    # (handled above)


@app.get("/documents/{document_id}/pages")
async def get_document_pages(document_id: str):
    """
    Get all page IDs for a document.
    
    Returns list of page IDs that can be used with /images/{page_id}
    """
    from database import get_db
    from models import ImagePage
    
    with get_db() as db:
        pages = db.query(ImagePage).filter(
            ImagePage.document_id == document_id
        ).order_by(ImagePage.page_number).all()
        
        if not pages:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        
        return {
            "document_id": document_id,
            "page_count": len(pages),
            "share_url": _share_path("d", document_id),
            "pages": [
                {
                    "page_id": page.id,
                    "page_number": page.page_number,
                    "width": page.width,
                    "height": page.height,
                    "image_url": f"/images/{page.id}",
                }
                for page in pages
            ]
        }


@app.get("/documents/{document_id}/summary")
async def get_document_summary(document_id: str):
    """
    Read-only summary endpoint.
    IMPORTANT: Never invokes Bedrock; only returns cached DB results.
    """
    from database import get_db
    from models import DocumentSummary

    with get_db() as db:
        row = db.query(DocumentSummary).filter(DocumentSummary.document_id == document_id).first()
        if not row:
            return {
                "document_id": document_id,
                "status": "missing",
                "summary_markdown": None,
                "model_id": None,
                "prompt_version": None,
                "updated_at": None,
            }

        return {
            "document_id": document_id,
            "status": row.status,
            "summary_markdown": row.summary_markdown,
            "model_id": row.model_id,
            "prompt_version": row.prompt_version,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


@app.get("/documents/{document_id}/tags")
async def get_document_tags(document_id: str):
    """
    Read-only tags endpoint.
    IMPORTANT: Never invokes Bedrock; only returns cached DB results.
    """
    from database import get_db
    from models import DocumentTag

    with get_db() as db:
        tags = (
            db.query(DocumentTag.tag_id, DocumentTag.confidence, DocumentTag.source)
            .filter(DocumentTag.document_id == document_id)
            .all()
        )
    labels = _get_tag_categories_cached()["labels"]

    return {
        "document_id": document_id,
        "tags": [
            {
                "id": t[0],
                "label": labels.get(t[0], t[0]),
                "confidence": t[1],
                "source": t[2],
            }
            for t in tags
        ],
    }


@app.get("/tag-categories")
async def list_tag_categories(request: Request):
    """
    Return the approved tag taxonomy (stable, small).
    Designed to be prefetched at boot/login and cached aggressively client-side.
    """
    cached = _get_tag_categories_cached()
    etag = cached["etag"]
    inm = request.headers.get("if-none-match")
    headers = {
        "ETag": etag,
        # Safe because this endpoint is keyed by API key (if enabled) and data is non-sensitive.
        # Keep it "private" to avoid any shared cache mixing auth contexts.
        "Cache-Control": "private, max-age=3600",
    }
    if inm and inm == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content={"tag_categories": cached["items"]}, headers=headers)


# ============================================
# File/Document Search Endpoints
# ============================================

class FileSearchResult(BaseModel):
    """Result from file search."""
    document_id: str
    file_name: str
    file_type: Optional[str]
    source_url: Optional[str]
    page_count: int
    ingested_at: Optional[str]
    has_text: bool
    preview_text: Optional[str] = None
    file_url: str
    thumbnail_url: Optional[str] = None
    pages: List[Dict[str, Any]]


class FileSearchResponse(BaseModel):
    """Response for file search."""
    results: List[FileSearchResult]
    count: int
    total: int
    query: Optional[str]


@app.get("/search/files")
async def search_files(
    q: Optional[str] = Query(None, description="Search query (filename or content)"),
    has_text: Optional[bool] = Query(None, description="Filter by whether file has extracted text"),
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0)
):
    """
    Search for files/documents in the system.
    
    - If `q` is provided, searches filenames and OCR text content
    - If `has_text=true`, only returns documents with extracted text
    - If `has_text=false`, returns documents without text
    - Returns document info with page previews
    
    Example: GET /search/files?q=EFTA00000001
    """
    from database import get_db
    from models import Document, ImagePage, OCRText
    from sqlalchemy import func, or_
    
    try:
        with get_db() as db:
            # Base query for documents
            query = db.query(Document)
            
            # If search query provided, search filename and OCR content
            if q:
                q_lower = q.lower()
                
                # Match documents by:
                # - document_id (Document.id)
                # - filename (Document.file_name)
                # - source URL (Document.source_url)
                # - OCR content (OCRText.raw_text)
                base_matches = db.query(Document.id).filter(
                    or_(
                        func.lower(Document.id).contains(q_lower),
                        func.lower(Document.file_name).contains(q_lower),
                        func.lower(Document.source_url).contains(q_lower),
                    )
                ).subquery()
                
                # Find documents with matching OCR text
                content_matches = db.query(OCRText.document_id).filter(
                    func.lower(OCRText.raw_text).contains(q_lower)
                ).distinct().subquery()
                
                # Combine both
                query = query.filter(
                    or_(
                        Document.id.in_(base_matches),
                        Document.id.in_(content_matches)
                    )
                )
            
            # Filter by has_text if specified
            if has_text is not None:
                docs_with_text = db.query(OCRText.document_id).distinct().subquery()
                if has_text:
                    query = query.filter(Document.id.in_(docs_with_text))
                else:
                    query = query.filter(~Document.id.in_(docs_with_text))
            
            # Get total count before pagination
            total = query.count()
            
            # Apply pagination
            documents = query.order_by(Document.ingested_at.desc()).offset(offset).limit(limit).all()
            
            results = []
            for doc in documents:
                # Get pages for this document
                pages = db.query(ImagePage).filter(
                    ImagePage.document_id == doc.id
                ).order_by(ImagePage.page_number).all()
                
                # Get preview text from first OCR result
                ocr = db.query(OCRText).filter(
                    OCRText.document_id == doc.id
                ).first()
                
                preview_text = None
                if ocr and ocr.raw_text:
                    preview_text = ocr.raw_text[:200] + "..." if len(ocr.raw_text) > 200 else ocr.raw_text
                
                results.append({
                    "document_id": doc.id,
                    "file_name": doc.file_name,
                    "file_type": doc.file_type,
                    "source_url": doc.source_url,
                    "page_count": len(pages),
                    "ingested_at": doc.ingested_at.isoformat() if doc.ingested_at else None,
                    "has_text": ocr is not None,
                    "preview_text": preview_text,
                    "file_url": f"/files/{doc.id}",
                    "share_url": _share_path("d", doc.id),
                    # Always provide a doc-level thumbnail for list views (works even when pages/images aren't generated yet)
                    "thumbnail_url": f"/file-thumbnails/{doc.id}",
                    "pages": [
                        {
                            "page_id": page.id,
                            "page_number": page.page_number,
                            "image_url": f"/images/{page.id}",
                            "thumbnail_url": f"/thumbnails/{page.id}",
                        }
                        for page in pages
                    ]
                })
            
            return {
                "results": results,
                "count": len(results),
                "total": total,
                "query": q
            }
            
    except Exception as e:
        logger.error(f"File search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files")
async def list_files(
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0)
):
    """
    List all files/documents in the system with pagination.
    
    Example: GET /files?limit=20&offset=0
    """
    return await search_files(q=None, has_text=None, limit=limit, offset=offset)


@app.get("/avatars/{username}")
async def get_avatar(username: str):
    """
    Serve avatar image for a username.
    
    Example: GET /avatars/BlueOtter
    Returns: PNG avatar image
    """
    from comments.avatars import get_avatar_url, _generate_avatar_image
    from pathlib import Path
    
    # If S3 is configured, redirect to presigned URL
    if Config.S3_BUCKET:
        url = get_avatar_url(username)
        return RedirectResponse(url=url, status_code=302)
    
    # Local fallback: generate on-the-fly or serve from disk
    local_path = Path(f"data/avatars/{username.lower()}.png")
    if local_path.exists():
        return FileResponse(
            path=local_path,
            media_type="image/png",
            filename=f"{username}.png"
        )
    
    # Generate on-the-fly if missing
    try:
        avatar_buffer = _generate_avatar_image(username)
        return StreamingResponse(
            avatar_buffer,
            media_type="image/png",
            headers={"Content-Disposition": f"inline; filename={username}.png"}
        )
    except Exception as e:
        logger.error(f"Error generating avatar for {username}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate avatar")


@app.get("/file-thumbnails/{document_id}")
async def get_file_thumbnail(
    document_id: str,
    width: int = Query(300, ge=50, le=800, description="Thumbnail width")
):
    """
    Document-level thumbnail (renders page 1 from the PDF using PyMuPDF).
    This makes file list thumbnails work even when ImagePage rows / page PNGs are missing.
    """
    from database import get_db
    from models import Document
    from PIL import Image

    # Cache location in S3
    thumb_key = f"thumbnails/docs/{document_id}_w{width}.png"

    if Config.S3_BUCKET:
        try:
            import boto3
            import fitz  # PyMuPDF
            from storage.s3_assets import presign_get

            s3 = boto3.client("s3", region_name=Config.S3_REGION or "us-east-1")

            # Serve from cache if present
            try:
                s3.head_object(Bucket=Config.S3_BUCKET, Key=thumb_key)
                url = _presign_cached(key=thumb_key)
                return RedirectResponse(
                    url=url,
                    status_code=302,
                    headers={"Cache-Control": "private, max-age=300"},
                )
            except Exception:
                pass

            # Load doc metadata inside session
            with get_db() as db:
                doc = db.query(Document).filter(Document.id == document_id).first()
                if not doc:
                    raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
                source_url = doc.source_url
                file_type = (doc.file_type or "pdf").lower()

            if file_type != "pdf":
                raise HTTPException(status_code=404, detail="Thumbnail not available for non-PDF files")

            # Fetch PDF bytes from S3 if possible
            pdf_key = f"{Config.S3_FILES_PREFIX.rstrip('/')}/{document_id}.pdf"
            pdf_bytes = None
            try:
                obj = s3.get_object(Bucket=Config.S3_BUCKET, Key=pdf_key)
                pdf_bytes = obj["Body"].read()
            except Exception:
                pdf_bytes = None

            # If missing in S3, download from source_url and upload (best-effort)
            if pdf_bytes is None:
                if not source_url:
                    raise HTTPException(status_code=404, detail="Source PDF not available")
                import httpx
                r = httpx.get(source_url, timeout=60.0, follow_redirects=True)
                r.raise_for_status()
                pdf_bytes = r.content
                try:
                    s3.put_object(
                        Bucket=Config.S3_BUCKET,
                        Key=pdf_key,
                        Body=pdf_bytes,
                        ContentType="application/pdf",
                        CacheControl="public, max-age=31536000",
                    )
                except Exception as e:
                    logger.debug(f"PDF upload skipped (file thumbnail): {e}")

            # Render first page
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pdf_page = pdf_doc.load_page(0)
            mat = fitz.Matrix(2.0, 2.0)
            pix = pdf_page.get_pixmap(matrix=mat, alpha=False)
            raw_png = pix.tobytes("png")

            # Resize to requested width
            img = Image.open(BytesIO(raw_png))
            ratio = width / img.width
            height = int(img.height * ratio)
            img.thumbnail((width, height), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            # Cache to S3 (best-effort)
            try:
                s3.put_object(
                    Bucket=Config.S3_BUCKET,
                    Key=thumb_key,
                    Body=buffer.getvalue(),
                    ContentType="image/png",
                    CacheControl="public, max-age=31536000",
                )
            except Exception as e:
                logger.debug(f"Thumbnail cache upload skipped (file thumbnail): {e}")

            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="image/png",
                headers={"Content-Disposition": f"inline; filename={document_id}_thumb.png"},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error generating file thumbnail for {document_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to generate file thumbnail")

    # Local fallback (dev)
    with get_db() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
        file_type = (doc.file_type or "pdf").lower()
    if file_type != "pdf":
        raise HTTPException(status_code=404, detail="Thumbnail not available for non-PDF files")
    pdf_path = Config.STORAGE_PATH / f"{document_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Local PDF not found")
    try:
        import fitz  # PyMuPDF
        pdf_doc = fitz.open(str(pdf_path))
        pdf_page = pdf_doc.load_page(0)
        mat = fitz.Matrix(2.0, 2.0)
        pix = pdf_page.get_pixmap(matrix=mat, alpha=False)
        raw_png = pix.tobytes("png")

        img = Image.open(BytesIO(raw_png))
        ratio = width / img.width
        height = int(img.height * ratio)
        img.thumbnail((width, height), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="image/png")
    except Exception as e:
        logger.error(f"Error generating local file thumbnail for {document_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate file thumbnail")


# ============================================
# Celebrity Detection Endpoints
# ============================================

class CelebrityResult(BaseModel):
    """Result from celebrity search."""
    celebrity_id: str
    name: str
    confidence: float
    document_id: str
    page_number: int
    image_page_id: str
    image_url: str
    thumbnail_url: str
    file_url: str
    urls: Optional[List[str]] = None
    bbox: Optional[Dict[str, float]] = None


class CelebritySearchResponse(BaseModel):
    """Response for celebrity search."""
    results: List[CelebrityResult]
    count: int
    query: str


@app.get("/search/celebrities")
async def search_celebrities(
    q: str = Query(..., description="Celebrity name to search for"),
    min_confidence: float = Query(90.0, ge=0, le=100, description="Minimum confidence"),
    limit: int = Query(50, ge=1, le=200)
):
    """
    Search for documents containing a specific celebrity.
    
    Uses AWS Rekognition celebrity detection results.
    
    Example: GET /search/celebrities?q=Bill Clinton
    """
    from database import get_db
    from models import Celebrity
    
    try:
        with get_db() as db:
            query_lower = q.lower()
            
            celebrities = db.query(Celebrity).filter(
                Celebrity.name_lower.contains(query_lower),
                Celebrity.confidence >= min_confidence
            ).order_by(
                Celebrity.confidence.desc()
            ).limit(limit).all()
            
            results = []
            for celeb in celebrities:
                results.append({
                    "celebrity_id": celeb.id,
                    "name": celeb.name,
                    "confidence": celeb.confidence,
                    "document_id": celeb.document_id,
                    "page_number": celeb.page_number,
                    "image_page_id": celeb.image_page_id,
                    "image_url": f"/images/{celeb.image_page_id}",
                    "thumbnail_url": f"/thumbnails/{celeb.image_page_id}",
                    "file_url": f"/files/{celeb.document_id}",
                    "urls": celeb.urls,
                    "bbox": {
                        "left": celeb.bbox_left,
                        "top": celeb.bbox_top,
                        "width": celeb.bbox_width,
                        "height": celeb.bbox_height
                    }
                })
            
            return {
                "results": results,
                "count": len(results),
                "query": q
            }
            
    except Exception as e:
        logger.error(f"Celebrity search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/celebrities")
async def list_celebrities(
    min_confidence: float = Query(90.0, ge=0, le=100),
    limit: int = Query(100, ge=1, le=500)
):
    """
    List all detected celebrities with appearance counts.
    
    Returns celebrities sorted by number of appearances.
    """
    from database import get_db
    from models import Celebrity
    from sqlalchemy import func
    
    try:
        with get_db() as db:
            # Get celebrities grouped by name with count
            rows = (
                db.query(
                    Celebrity.name,
                    func.count(Celebrity.id).label("appearances"),
                    func.avg(Celebrity.confidence).label("avg_confidence"),
                    func.max(Celebrity.confidence).label("max_confidence")
                )
                .filter(Celebrity.confidence >= min_confidence)
                .group_by(Celebrity.name)
                .order_by(func.count(Celebrity.id).desc())
                .limit(limit)
                .all()
            )
            
            return {
                "celebrities": [
                    {
                        "name": name,
                        "appearances": int(count),
                        "avg_confidence": round(float(avg_conf), 1),
                        "max_confidence": round(float(max_conf), 1)
                    }
                    for name, count, avg_conf, max_conf in rows
                ],
                "count": len(rows),
                "min_confidence": min_confidence
            }
            
    except Exception as e:
        logger.error(f"List celebrities error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/celebrities/{name}/appearances")
async def get_celebrity_appearances(
    name: str,
    min_confidence: float = Query(90.0, ge=0, le=100),
    limit: int = Query(100, ge=1, le=500)
):
    """
    Get all appearances of a specific celebrity.
    
    Example: GET /celebrities/Bill%20Clinton/appearances
    """
    from database import get_db
    from models import Celebrity, Document
    
    try:
        with get_db() as db:
            name_lower = name.lower()
            
            celebrities = db.query(Celebrity, Document).join(
                Document, Celebrity.document_id == Document.id
            ).filter(
                Celebrity.name_lower == name_lower,
                Celebrity.confidence >= min_confidence
            ).order_by(
                Celebrity.confidence.desc()
            ).limit(limit).all()
            
            appearances = []
            for celeb, doc in celebrities:
                appearances.append({
                    "document_id": celeb.document_id,
                    "file_name": doc.file_name,
                    "page_number": celeb.page_number,
                    "confidence": celeb.confidence,
                    "image_url": f"/images/{celeb.image_page_id}",
                    "thumbnail_url": f"/thumbnails/{celeb.image_page_id}",
                    "file_url": f"/files/{celeb.document_id}",
                    "bbox": {
                        "left": celeb.bbox_left,
                        "top": celeb.bbox_top,
                        "width": celeb.bbox_width,
                        "height": celeb.bbox_height
                    }
                })
            
            return {
                "celebrity": name,
                "appearances": appearances,
                "count": len(appearances)
            }
            
    except Exception as e:
        logger.error(f"Celebrity appearances error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process/celebrities")
async def process_celebrities(
    limit: int = Query(100, ge=1, le=1000, description="Max pages to process"),
    min_confidence: float = Query(90.0, ge=0, le=100, description="Min confidence to store")
):
    """
    Process images through AWS Rekognition to detect celebrities.
    
    This runs celebrity recognition on unprocessed image pages.
    Requires AWS credentials to be configured.
    
    Note: AWS charges apply (~$0.001 per image).
    """
    from ocr.rekognition import RekognitionProcessor
    
    processor = RekognitionProcessor()
    
    if not processor.enabled:
        raise HTTPException(
            status_code=400,
            detail="AWS Rekognition not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )
    
    # Process pages for celebrities
    from database import get_db
    from models import ImagePage, Celebrity
    
    with get_db() as db:
        # Find pages not yet processed for celebrities
        processed_page_ids = db.query(Celebrity.image_page_id).distinct().subquery()
        
        unprocessed = db.query(ImagePage.id).filter(
            ~ImagePage.id.in_(processed_page_ids)
        ).limit(limit).all()
        
        page_ids = [row[0] for row in unprocessed]
    
    total_celebrities = 0
    processed = 0
    
    for page_id in page_ids:
        celebrities_found = processor.process_celebrities(page_id, min_confidence)
        total_celebrities += celebrities_found
        processed += 1
    
    return {
        "pages_processed": processed,
        "celebrities_found": total_celebrities,
        "remaining": len(page_ids) - processed if len(page_ids) > processed else 0
    }


### Note: Removed experimental email-focused endpoints per user request.


# ============================================
# DOJ File Ingestion Endpoints
# ============================================

_doj_ingestion_task = None
_doj_pause_event = None

class DOJIngestionResponse(BaseModel):
    """Response for DOJ file ingestion."""
    status: str
    files_discovered: int
    files_downloaded: int
    files_processed: int
    message: str
    errors: Optional[List[str]] = None


@app.post("/ingest/doj")
async def ingest_doj_files(
    background: bool = Query(False, description="Run in background (returns immediately)"),
    skip_existing: bool = Query(True, description="Skip files that are already in the database"),
    limit: Optional[int] = Query(None, ge=1, description="Optional limit on number of files to download/process (for testing)")
):
    """
    Ingest files from Department of Justice Epstein page (justice.gov/epstein).
    
    This endpoint:
    1. Crawls justice.gov/epstein for documents
    2. Excludes "Epstein Files Transparency Act" files (already in images)
    3. Downloads all other files
    4. Processes them through AWS Textract for OCR
    5. Indexes the extracted text for search
    
    Note: This can take a while depending on how many files are available.
    Set background=true to run asynchronously.
    """
    import asyncio
    from pathlib import Path
    from ingestion.doj_crawler import DOJEpsteinCrawler
    from ingestion.storage import DocumentStorage
    from ingestion.pdf_converter import pdf_to_images, is_pdf
    from ocr.processor import OCRProcessor
    from ocr.textract import TextractEngine
    from processing.text_processor import TextProcessor
    from search.indexer import SearchIndexer
    from PIL import Image
    from database import get_db
    import asyncio as _asyncio
    import hashlib
    import re
    global _doj_ingestion_task
    global _doj_pause_event

    if _doj_pause_event is None:
        _doj_pause_event = _asyncio.Event()
        _doj_pause_event.set()  # not paused by default
    
    async def do_ingestion():
        storage = DocumentStorage()
        # Force AWS Textract for DOJ ingestion (no silent fallback to Paddle/EasyOCR).
        textract = TextractEngine()
        if not getattr(textract, "enabled", False):
            raise RuntimeError(
                "AWS Textract is not configured/enabled. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and AWS_DEFAULT_REGION if needed), "
                "and ensure OCR_ENGINE=textract."
            )
        ocr_processor = OCRProcessor(ocr_engine=textract)
        text_processor = TextProcessor()
        indexer = SearchIndexer()
        
        temp_dir = Config.STORAGE_PATH / "doj_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        errors = []
        files_processed = 0
        files_downloaded = 0
        
        try:
            # Step 1: Discover files (do NOT download everything up-front; allows pause/stop)
            logger.info("Starting DOJ file ingestion...")
            
            async with DOJEpsteinCrawler() as crawler:
                files = await crawler.discover_files()

                if limit is not None:
                    files = files[: max(int(limit), 0)]
            
            if not files:
                return {
                    "status": "completed",
                    "files_discovered": 0,
                    "files_downloaded": 0,
                    "files_processed": 0,
                    "message": "No files discovered from DOJ website",
                    "errors": ["No files found at justice.gov/epstein"]
                }
            
            # Step 2-7: Download + process each file (pauseable)
            async with DOJEpsteinCrawler() as crawler:
                for file_info in files:
                    # Pause support (takes effect between files)
                    await _doj_pause_event.wait()

                    # Download (skip if already on disk)
                    url_hash = hashlib.sha256(file_info["url"].encode("utf-8")).hexdigest()[:16]
                    safe_basename = re.sub(r"[^\w\-_\.]", "_", file_info["filename"])
                    download_path = temp_dir / f"{url_hash}_{safe_basename}"
                    if not download_path.exists() or download_path.stat().st_size == 0:
                        ok = await crawler.fetch_file(file_info["url"], download_path)
                        if not ok:
                            errors.append(f"Download failed: {file_info.get('filename', 'unknown')}")
                            continue
                        files_downloaded += 1

                    # Ensure downstream storage sees local path & size
                    file_info["local_path"] = str(download_path)
                    file_info["file_size"] = download_path.stat().st_size

                    try:
                        # Store document
                        doc_id, is_new = storage.store_document(file_info)
                    
                        if not is_new and skip_existing:
                            logger.info(f"Document {doc_id} already exists, skipping")
                            continue
                    
                        # Convert PDFs to images if needed
                        file_path = Path(file_info['local_path'])
                        image_paths = []
                    
                        if is_pdf(file_path):
                            logger.info(f"Converting PDF: {file_path.name}")
                            images_dir = temp_dir / f"{doc_id}_images"
                            image_paths = pdf_to_images(file_path, images_dir)
                        else:
                            # Single image file
                            image_paths = [file_path]
                    
                        # Store image pages
                        for page_num, image_path in enumerate(image_paths, start=1):
                            img = Image.open(image_path)
                            width, height = img.size
                            
                            page_id = storage.store_image_page(
                                doc_id, page_num, image_path, width, height
                            )
                    
                        # Process OCR using AWS Textract
                        logger.info(f"Processing OCR for document {doc_id} with AWS Textract")
                        pages_processed = ocr_processor.process_document(doc_id)
                    
                        # Process text (normalize, detect entities)
                        from models import OCRText
                        # IMPORTANT: query IDs only to avoid DetachedInstanceError (instances expire on commit)
                        with get_db() as db:
                            ocr_text_ids = [
                                row[0]
                                for row in db.query(OCRText.id)
                                .filter(OCRText.document_id == doc_id)
                                .all()
                            ]

                        for ocr_text_id in ocr_text_ids:
                            text_processor.process_ocr_text(ocr_text_id)
                    
                        # Index for search
                        indexed = indexer.index_document(doc_id)
                        logger.info(f"Indexed {indexed} OCR texts for document {doc_id}")
                        
                        files_processed += 1
                    
                    except Exception as e:
                        error_msg = f"Error processing {file_info.get('filename', 'unknown')}: {str(e)}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        continue
            
            logger.info(f"DOJ ingestion complete: {files_processed}/{len(files)} files processed")
            
            return {
                "status": "completed",
                "files_discovered": len(files),
                "files_downloaded": files_downloaded,
                "files_processed": files_processed,
                "message": f"Successfully processed {files_processed}/{len(files)} files",
                "errors": errors if errors else None
            }
            
        except _asyncio.CancelledError:
            logger.warning("DOJ ingestion cancelled.")
            return {
                "status": "cancelled",
                "files_discovered": 0,
                "files_downloaded": files_downloaded,
                "files_processed": files_processed,
                "message": "DOJ ingestion cancelled.",
                "errors": errors if errors else None
            }
        except Exception as e:
            logger.exception(f"DOJ ingestion failed: {e}")
            return {
                "status": "failed",
                "files_discovered": 0,
                "files_downloaded": 0,
                "files_processed": 0,
                "message": f"Ingestion failed: {str(e)}",
                "errors": [str(e)]
            }
    
    if background:
        # Start ingestion in background (single-flight: don't start multiple concurrent jobs)
        if _doj_ingestion_task is not None and not _doj_ingestion_task.done():
            return {
                "status": "already_running",
                "files_discovered": 0,
                "files_downloaded": 0,
                "files_processed": 0,
                "message": "DOJ file ingestion is already running in background. Check logs for progress.",
                "errors": None
            }

        _doj_ingestion_task = _asyncio.create_task(do_ingestion())
        return {
            "status": "started",
            "files_discovered": 0,
            "files_downloaded": 0,
            "files_processed": 0,
            "message": "DOJ file ingestion started in background. Check logs for progress.",
            "errors": None
        }
    else:
        # Run synchronously (may take a while)
        result = await do_ingestion()
        return result


@app.post("/ingest/doj/stop")
async def stop_doj_ingestion():
    """Cancel an in-progress DOJ background ingestion job (if any)."""
    global _doj_ingestion_task
    if _doj_ingestion_task is None:
        return {"status": "not_running", "message": "No DOJ ingestion task has been started."}
    if _doj_ingestion_task.done():
        return {"status": "not_running", "message": "DOJ ingestion task is not running."}

    _doj_ingestion_task.cancel()
    return {"status": "cancelling", "message": "Requested cancellation of DOJ ingestion task."}


@app.post("/ingest/doj/pause")
async def pause_doj_ingestion():
    """Pause an in-progress DOJ ingestion job (takes effect between files)."""
    import asyncio as _asyncio
    global _doj_pause_event
    if _doj_pause_event is None:
        _doj_pause_event = _asyncio.Event()
        _doj_pause_event.set()
    _doj_pause_event.clear()
    return {"status": "paused", "message": "DOJ ingestion paused (will pause between files)."}


@app.post("/ingest/doj/resume")
async def resume_doj_ingestion():
    """Resume a paused DOJ ingestion job."""
    import asyncio as _asyncio
    global _doj_pause_event
    if _doj_pause_event is None:
        _doj_pause_event = _asyncio.Event()
    _doj_pause_event.set()
    return {"status": "running", "message": "DOJ ingestion resumed."}


@app.get("/ingest/doj/status")
async def status_doj_ingestion():
    """Get status of the DOJ ingestion job (running/paused/not running)."""
    global _doj_ingestion_task, _doj_pause_event
    running = _doj_ingestion_task is not None and not _doj_ingestion_task.done()
    paused = (_doj_pause_event is not None) and (not _doj_pause_event.is_set())
    return {"running": bool(running), "paused": bool(paused)}


@app.get("/ingest/doj/preview")
async def preview_doj_files():
    """
    Preview what files would be downloaded from justice.gov/epstein without actually downloading them.
    
    This is useful to see what files are available and verify the filtering is working correctly.
    """
    from ingestion.doj_crawler import DOJEpsteinCrawler
    
    try:
        async with DOJEpsteinCrawler() as crawler:
            files = await crawler.discover_files()
        
        # Group by section
        sections = {}
        for file_info in files:
            section = file_info.get('section', 'Unknown')
            if section not in sections:
                sections[section] = []
            sections[section].append({
                "filename": file_info['filename'],
                "url": file_info['url'],
                "description": file_info.get('description', ''),
                "file_type": file_info.get('file_type', 'unknown')
            })
        
        return {
            "total_files": len(files),
            "sections": sections,
            "note": "Files from 'Epstein Files Transparency Act' under 'DOJ Disclosures' are excluded (already in images)"
        }
        
    except Exception as e:
        logger.error(f"Error previewing DOJ files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Image Gallery/List Endpoint
# ============================================

@app.get("/images")
async def list_images(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    document_id: Optional[str] = Query(None, description="Filter by document ID")
):
    """
    List all available images in the system.
    
    Useful for viewing all images that are available through the /images/{page_id} endpoint.
    """
    from database import get_db
    from models import ImagePage, Document
    
    try:
        with get_db() as db:
            query = db.query(ImagePage, Document).join(
                Document, ImagePage.document_id == Document.id
            )
            
            if document_id:
                query = query.filter(ImagePage.document_id == document_id)
            
            total = query.count()
            
            pages = query.order_by(
                ImagePage.document_id, ImagePage.page_number
            ).offset(offset).limit(limit).all()
            
            results = []
            for page, doc in pages:
                results.append({
                    "page_id": page.id,
                    "document_id": page.document_id,
                    "page_number": page.page_number,
                    "file_name": doc.file_name,
                    "width": page.width,
                    "height": page.height,
                    "image_url": f"/images/{page.id}",
                    "thumbnail_url": f"/thumbnails/{page.id}",
                    "ocr_processed": page.ocr_processed
                })
            
            return {
                "images": results,
                "count": len(results),
                "total": total,
                "offset": offset,
                "limit": limit
            }
            
    except Exception as e:
        logger.error(f"Error listing images: {e}")
        raise HTTPException(status_code=500, detail=str(e))


