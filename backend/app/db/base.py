"""SQLite persistence layer (SQLAlchemy 2.0).

One local database under <repo>/data holds everything the "intelligent" studio learns:
datasets and fields across every region, their cross-region similarity, per-user usage,
saved research sessions and prompts, and provenance. `init_db()` creates the schema on
first run; there is no separate migration step for the local single-file DB.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

# check_same_thread=False: FastAPI serves requests from a threadpool; SQLite is fine with
# short-lived sessions per request.
engine = create_engine(
    f"sqlite:///{_settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app.db import models  # noqa: F401  (register mappers)
    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """create_all() never ADDS columns to a table that already exists, so a DB created before a
    new column was introduced would be missing it. This adds any missing nullable columns with a
    plain ALTER TABLE — idempotent and safe for the local single-file SQLite DB."""
    from sqlalchemy import inspect, text
    wanted = {
        "sim_results": [("margin", "FLOAT"), ("drawdown", "FLOAT"),
                        ("test_period", "TEXT DEFAULT 'P0Y0M0D'"),
                        ("inversion_candidate", "BOOLEAN DEFAULT 0"),
                        ("evidence_json", "TEXT DEFAULT '{}'") ,
                        ("metrics_json", "TEXT DEFAULT '{}'") ,
                        ("transfer_study_id", "INTEGER DEFAULT 0")],
        "catalog_field_availability": [("date_coverage", "FLOAT DEFAULT 0")],
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, sqltype in cols:
                if name not in have:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sqltype}'))


def get_db():
    """FastAPI dependency: a request-scoped session that always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
