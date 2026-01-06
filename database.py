"""Database connection and session management."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from config import Config
from models import Base
import logging

logger = logging.getLogger(__name__)

# Create engine
engine = create_engine(
    Config.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in Config.DATABASE_URL else {},
    echo=False
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def ensure_db_indexes():
    """
    Create DB indexes that must differ by dialect.

    - SQLite: simple btree indexes on TEXT columns are OK.
    - Postgres: btree indexes on large TEXT can fail (index row size limit).
      Use GIN full-text indexes instead.
    """
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
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
        else:
            # These are helpful for SQLite, and harmless if the DB already has them.
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_normalized_text ON ocr_text(normalized_text)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_searchable_text ON search_index(searchable_text)"))


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
    # Lightweight schema migrations (safe on both SQLite and Postgres).
    # We avoid relying on Alembic in production and keep additive migrations here.
    try:
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if dialect == "sqlite":
                cols = [r[1] for r in conn.execute(text("PRAGMA table_info(documents)")).fetchall()]
                if "s3_key_files" not in cols:
                    conn.execute(text("ALTER TABLE documents ADD COLUMN s3_key_files TEXT"))
                if "s3_presigned_url" not in cols:
                    conn.execute(text("ALTER TABLE documents ADD COLUMN s3_presigned_url TEXT"))
                if "s3_presigned_expires_at" not in cols:
                    conn.execute(text("ALTER TABLE documents ADD COLUMN s3_presigned_expires_at TIMESTAMP"))
            elif dialect == "postgresql":
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_key_files TEXT"))
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_presigned_url TEXT"))
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_presigned_expires_at TIMESTAMP"))
            else:
                # Best-effort on other dialects
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_key_files TEXT"))
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_presigned_url TEXT"))
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_presigned_expires_at TIMESTAMP"))
    except Exception as e:
        logger.warning(f"Schema migration skipped/failed: {e}")
    
    # Add collection column to documents table
    try:
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if dialect == "sqlite":
                cols = [r[1] for r in conn.execute(text("PRAGMA table_info(documents)")).fetchall()]
                if "collection" not in cols:
                    conn.execute(text("ALTER TABLE documents ADD COLUMN collection TEXT"))
            elif dialect == "postgresql":
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS collection VARCHAR"))
            else:
                # Best-effort on other dialects
                conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS collection TEXT"))
            # Create index on collection if it doesn't exist
            if dialect == "sqlite":
                indexes = [
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type='index' AND name='idx_collection'"
                        )
                    ).fetchall()
                ]
                if not indexes:
                    conn.execute(text("CREATE INDEX idx_collection ON documents(collection)"))
            elif dialect == "postgresql":
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_collection ON documents(collection)"))
    except Exception as e:
        logger.warning(f"Collection column migration skipped/failed: {e}")
    
    # Add likes/dislikes columns to comments table
    try:
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if dialect == "sqlite":
                cols = [r[1] for r in conn.execute(text("PRAGMA table_info(comments)")).fetchall()]
                if "likes_count" not in cols:
                    conn.execute(text("ALTER TABLE comments ADD COLUMN likes_count INTEGER DEFAULT 0 NOT NULL"))
                if "dislikes_count" not in cols:
                    conn.execute(text("ALTER TABLE comments ADD COLUMN dislikes_count INTEGER DEFAULT 0 NOT NULL"))
            elif dialect == "postgresql":
                conn.execute(text("ALTER TABLE comments ADD COLUMN IF NOT EXISTS likes_count INTEGER DEFAULT 0 NOT NULL"))
                conn.execute(text("ALTER TABLE comments ADD COLUMN IF NOT EXISTS dislikes_count INTEGER DEFAULT 0 NOT NULL"))
            else:
                conn.execute(text("ALTER TABLE comments ADD COLUMN IF NOT EXISTS likes_count INTEGER DEFAULT 0 NOT NULL"))
                conn.execute(text("ALTER TABLE comments ADD COLUMN IF NOT EXISTS dislikes_count INTEGER DEFAULT 0 NOT NULL"))
    except Exception as e:
        logger.warning(f"Comments reaction columns migration skipped/failed: {e}")
    
    ensure_db_indexes()
    # Seed tag taxonomy if needed
    try:
        dialect = engine.dialect.name
        approved = [
            ("financial", "Financial"),
            ("legal", "Legal"),
            ("government", "Government"),
            ("email", "Email/Correspondence"),
            ("travel", "Travel/Itinerary"),
            ("contacts", "Contacts/Address book"),
            ("medical", "Medical/Health"),
            ("business", "Business/Corporate"),
            ("press", "Media/Press"),
            ("investigation", "Investigation/Intelligence"),
            ("evidence", "Evidence/Exhibits"),
            ("other", "Other/Uncategorized"),
        ]
        with engine.begin() as conn:
            if dialect == "sqlite":
                for tid, label in approved:
                    conn.execute(
                        text("INSERT OR IGNORE INTO tag_categories (id, label) VALUES (:id, :label)"),
                        {"id": tid, "label": label},
                    )
            elif dialect == "postgresql":
                for tid, label in approved:
                    conn.execute(
                        text(
                            "INSERT INTO tag_categories (id, label) VALUES (:id, :label) "
                            "ON CONFLICT (id) DO NOTHING"
                        ),
                        {"id": tid, "label": label},
                    )
            else:
                # best-effort
                for tid, label in approved:
                    conn.execute(
                        text("INSERT INTO tag_categories (id, label) VALUES (:id, :label)"),
                        {"id": tid, "label": label},
                    )
    except Exception as e:
        logger.warning(f"Tag taxonomy seed skipped/failed: {e}")
    logger.info("Database initialized")


@contextmanager
def get_db():
    """Context manager for database sessions."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session() -> Session:
    """Get a database session (use with get_db context manager)."""
    return SessionLocal()

