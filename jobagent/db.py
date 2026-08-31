from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterator

from .config import DB_PATH, ensure_data_files

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_job_id TEXT,
    source_url TEXT,
    application_url TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    employer TEXT,
    location TEXT,
    description TEXT NOT NULL DEFAULT '',
    date_posted TEXT,
    deadline TEXT,
    employment_type TEXT,
    content_hash TEXT,
    fingerprint TEXT,
    score INTEGER,
    score_details TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    duplicate_of INTEGER,
    duplicate_reason TEXT,
    notes TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    applied_at TEXT,
    confirmation TEXT,
    FOREIGN KEY(duplicate_of) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_canonical_url ON jobs(canonical_url);
CREATE INDEX IF NOT EXISTS idx_jobs_source_job_id ON jobs(source, source_job_id);

CREATE TABLE IF NOT EXISTS job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    UNIQUE(job_id, source_url),
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    body TEXT NOT NULL,
    facts_used TEXT NOT NULL DEFAULT '[]',
    warnings TEXT NOT NULL DEFAULT '[]',
    prompt_snapshot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    event_type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]
_INITIALIZED_PATHS: set[Path] = set()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _migration_draft_metadata(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "drafts", "facts_used", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "drafts", "warnings", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "drafts", "prompt_snapshot", "TEXT NOT NULL DEFAULT ''")


def _migration_archive(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "jobs", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "jobs", "archived_at", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_archived ON jobs(archived)")


def _migration_list_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_active_status_score "
        "ON jobs(archived, status, score)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_job_id ON drafts(job_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_sources_job_id ON job_sources(job_id)")


MIGRATIONS: tuple[Migration, ...] = (
    (1, "draft_metadata", _migration_draft_metadata),
    (2, "archive_flag", _migration_archive),
    (3, "common_query_indexes", _migration_list_indexes),
)


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")


def init_db() -> None:
    ensure_data_files()
    database_path = Path(DB_PATH).resolve()
    if database_path in _INITIALIZED_PATHS and database_path.exists():
        return
    with sqlite3.connect(database_path, timeout=5.0) as conn:
        _configure_connection(conn)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        applied = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")
        }
        for version, name, migration in MIGRATIONS:
            if version in applied:
                continue
            try:
                conn.execute("BEGIN IMMEDIATE")
                migration(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                    (version, name, utcnow()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    _INITIALIZED_PATHS.add(database_path)


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def log_event(conn: sqlite3.Connection, event_type: str, detail: str = "", job_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO events(job_id,event_type,detail,created_at) VALUES(?,?,?,?)",
        (job_id, event_type, detail, utcnow()),
    )


def reset_application_data() -> dict[str, int]:
    """Delete all job-ledger records while preserving configuration files and browser data."""
    with connection() as conn:
        counts = {
            "jobs": int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            "sources": int(conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0]),
            "drafts": int(conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]),
            "events": int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
        }
        conn.execute("DELETE FROM drafts")
        conn.execute("DELETE FROM job_sources")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM jobs")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('jobs','job_sources','drafts','events')"
        )
    return counts
