#!/usr/bin/env python3
"""
One-shot migration: SQLite -> PostgreSQL (AWS RDS or any Postgres).

This copies all ORM tables defined in models.py:
  documents, image_pages, ocr_text, entities, search_index, image_labels, celebrities

Usage:
  python scripts/migrate_sqlite_to_postgres.py \
    --sqlite "sqlite:///./data/ocr.db" \
    --postgres "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME"

Notes:
  - This script only migrates the DATABASE contents, not the actual files on disk
    under data/storage/ and data/images/.
  - Run it when ingestion is stopped to avoid a moving target.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Type

import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError

sys.path.append(str(Path(__file__).resolve().parent.parent))

from models import Base, Document, ImagePage, OCRText, Entity, SearchIndex, ImageLabel, Celebrity  # noqa: E402


TABLES: list[Type[Base]] = [
    Document,
    ImagePage,
    OCRText,
    Entity,
    SearchIndex,
    ImageLabel,
    Celebrity,
]


def _iter_rows(db: Session, model: Type[Base], batch_size: int) -> Iterable[list[Base]]:
    q = db.query(model).order_by(model.id.asc())
    offset = 0
    while True:
        batch = q.offset(offset).limit(batch_size).all()
        if not batch:
            break
        yield batch
        offset += len(batch)


def _copy_table(
    src: Session,
    DstSession: sessionmaker,
    model: Type[Base],
    batch_size: int,
    truncate_first: bool,
) -> int:
    if truncate_first:
        with DstSession() as dst:
            dst.query(model).delete()
            dst.commit()

    inserted = 0
    for batch in _iter_rows(src, model, batch_size=batch_size):
        # Use merge so this is idempotent (re-runs update/replace by PK).
        # We open a fresh destination session per batch so any broken connection can be discarded.
        attempt = 0
        while True:
            attempt += 1
            try:
                with DstSession() as dst:
                    for row in batch:
                        dst.merge(row)
                    dst.commit()
                break
            except OperationalError as e:
                msg = str(e).lower()
                print(f"{model.__tablename__}: OperationalError on batch (attempt {attempt}): {e}")
                if attempt >= 3:
                    raise
                # backoff and retry
                time.sleep(2 * attempt)
                # If SSL/connection errors persist, fall back to row-by-row inserts with fresh sessions.
                if "ssl" in msg or "connection" in msg:
                    print(f"{model.__tablename__}: falling back to row-by-row for this batch")
                    for row in batch:
                        row_attempt = 0
                        while True:
                            row_attempt += 1
                            try:
                                with DstSession() as dst:
                                    dst.merge(row)
                                    dst.commit()
                                break
                            except OperationalError as row_e:
                                row_msg = str(row_e).lower()
                                print(f"{model.__tablename__}: OperationalError on row (attempt {row_attempt}): {row_e}")
                                if row_attempt >= 5 or ("ssl" not in row_msg and "connection" not in row_msg):
                                    raise
                                time.sleep(1 * row_attempt)
                    break
        inserted += len(batch)
        print(f"{model.__tablename__}: migrated {inserted} rows")
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite DB to Postgres DB")
    parser.add_argument("--sqlite", required=True, help='SQLite URL, e.g. "sqlite:///./data/ocr.db"')
    parser.add_argument(
        "--postgres",
        required=True,
        help='Postgres URL, e.g. "postgresql+psycopg2://user:pass@host:5432/dbname"',
    )
    parser.add_argument("--batch-size", type=int, default=2000, help="Rows per batch per table")
    parser.add_argument(
        "--truncate-dest",
        action="store_true",
        help="Delete destination tables before copy (DANGEROUS if dest has data you need).",
    )
    args = parser.parse_args()

    src_engine = create_engine(args.sqlite, connect_args={"check_same_thread": False})
    dst_engine = create_engine(args.postgres, pool_pre_ping=True)

    # Create tables in Postgres if they don't exist yet
    Base.metadata.create_all(bind=dst_engine)
    # Ensure Postgres-friendly indexes (avoid btree index on huge TEXT)
    with dst_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_ocr_text_tsv "
                "ON ocr_text USING gin (to_tsvector('english', normalized_text))"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_search_index_tsv "
                "ON search_index USING gin (to_tsvector('english', searchable_text))"
            )
        )

    SrcSession = sessionmaker(bind=src_engine, autocommit=False, autoflush=False)
    DstSession = sessionmaker(bind=dst_engine, autocommit=False, autoflush=False)

    with SrcSession() as src:
        for model in TABLES:
            print(f"\n=== Migrating {model.__tablename__} ===")
            per_table_batch = args.batch_size
            if model.__tablename__ in {"ocr_text", "entities", "search_index"}:
                per_table_batch = min(per_table_batch, 200)
            if model.__tablename__ == "ocr_text":
                per_table_batch = min(per_table_batch, 50)
            _copy_table(
                src=src,
                DstSession=DstSession,
                model=model,
                batch_size=per_table_batch,
                truncate_first=args.truncate_dest,
            )

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


