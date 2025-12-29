"""Long-running ECS worker for generating missing summaries/tags."""

from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timedelta

from database import init_db, get_db
from models import Document, DocumentSummary
from summaries.worker import summarize_and_tag_document

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def get_next_document_ids(limit: int) -> list[str]:
    """
    Pick documents that need summaries.
    Designed for a single worker instance (desired_count=1).
    """
    with get_db() as db:
        # Documents with no summary row
        missing = (
            db.query(Document.id)
            .outerjoin(DocumentSummary, DocumentSummary.document_id == Document.id)
            .filter(DocumentSummary.document_id.is_(None))
            .order_by(Document.ingested_at.desc())
            .limit(limit)
            .all()
        )
        if missing:
            return [r[0] for r in missing]

        # Retry failed/pending older than a cooldown window
        cooldown_minutes = _env_int("SUMMARIES_WORKER_RETRY_COOLDOWN_MINUTES", 30)
        cutoff = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
        retry = (
            db.query(DocumentSummary.document_id)
            .filter(DocumentSummary.status.in_(["failed", "pending"]))
            .filter((DocumentSummary.updated_at.is_(None)) | (DocumentSummary.updated_at < cutoff))
            .order_by(DocumentSummary.updated_at.asc().nullsfirst())
            .limit(limit)
            .all()
        )
        return [r[0] for r in retry]


def run_loop():
    init_db()

    batch_size = _env_int("SUMMARIES_WORKER_BATCH_SIZE", 1)
    poll_seconds = _env_int("SUMMARIES_WORKER_POLL_SECONDS", 10)

    logger.info(
        f"Summaries worker starting (batch_size={batch_size}, poll_seconds={poll_seconds})"
    )

    while True:
        try:
            doc_ids = get_next_document_ids(batch_size)
            if not doc_ids:
                time.sleep(poll_seconds)
                continue

            for doc_id in doc_ids:
                logger.info(f"Summarizing+tagging document_id={doc_id}")
                try:
                    status, tags = summarize_and_tag_document(doc_id)
                    logger.info(
                        f"Done document_id={doc_id} status={status} tags={len(tags)}"
                    )
                except Exception as e:
                    logger.exception(f"Worker failed on document_id={doc_id}: {e}")
        except Exception as e:
            logger.exception(f"Worker loop error: {e}")
            time.sleep(max(poll_seconds, 5))


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_loop()


if __name__ == "__main__":
    main()


