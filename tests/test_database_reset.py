from __future__ import annotations

import json
import sqlite3

import pytest

from jobagent import config, db, main
from jobagent.config import DEFAULT_PROFILE


def test_reset_application_data_removes_ledger_and_restarts_ids(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)

    db.init_db()
    now = db.utcnow()
    with db.connection() as conn:
        job_id = conn.execute(
            """
            INSERT INTO jobs(title,description,status,discovered_at,updated_at)
            VALUES(?,?,?,?,?)
            """,
            ("Testjob", "Testbeskrivelse", "discovered", now, now),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_sources(job_id,source,source_url,first_seen) VALUES(?,?,?,?)",
            (job_id, "jobnet", "https://example.test/job", now),
        )
        conn.execute(
            "INSERT INTO drafts(job_id,provider,body,created_at) VALUES(?,?,?,?)",
            (job_id, "template", "Ansøgning", now),
        )
        conn.execute(
            "INSERT INTO events(job_id,event_type,detail,created_at) VALUES(?,?,?,?)",
            (job_id, "job_discovered", "Test", now),
        )

    counts = db.reset_application_data()
    assert counts == {"jobs": 1, "sources": 1, "drafts": 1, "events": 1}

    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        new_id = conn.execute(
            """
            INSERT INTO jobs(title,description,status,discovered_at,updated_at)
            VALUES(?,?,?,?,?)
            """,
            ("Første rigtige job", "Beskrivelse", "discovered", now, now),
        ).lastrowid
        assert new_id == 1


def test_init_records_migrations_and_configures_sqlite(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)

    db.init_db()

    with db.connection() as conn:
        migrations = conn.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall()
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert [tuple(row) for row in migrations] == [
        (1, "draft_metadata"),
        (2, "archive_flag"),
        (3, "common_query_indexes"),
        (4, "multidimensional_scoring"),
    ]
    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)

    def broken_migration(conn):
        conn.execute("CREATE TABLE should_be_rolled_back(value TEXT)")
        raise RuntimeError("migration failed")

    monkeypatch.setattr(db, "MIGRATIONS", ((99, "broken", broken_migration),))

    with pytest.raises(RuntimeError, match="migration failed"):
        db.init_db()

    with sqlite3.connect(database) as conn:
        recorded = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=99"
        ).fetchone()[0]
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='should_be_rolled_back'"
        ).fetchone()

    assert recorded == 0
    assert table is None


def test_legacy_database_migration_preserves_jobs_status_archive_drafts_and_sources(tmp_path, monkeypatch):
    database = tmp_path / "legacy-0.2.db"
    old_schema = db.SCHEMA
    for declaration in (
        "    qualification_score INTEGER,\n",
        "    direction_score INTEGER,\n",
        "    curiosity_score INTEGER,\n",
        "    scoring_version INTEGER NOT NULL DEFAULT 0,\n",
    ):
        old_schema = old_schema.replace(declaration, "")
    now = db.utcnow()
    with sqlite3.connect(database) as conn:
        conn.executescript(old_schema)
        job_id = conn.execute(
            """INSERT INTO jobs(
                source,source_url,title,description,score,score_details,status,archived,archived_at,
                discovered_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "jobnet", "https://example.test/job/42", "Gemt job", "Angular TypeScript",
                72, '{"legacy":true}', "shortlisted", 1, now, now, now,
            ),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_sources(job_id,source,source_url,first_seen) VALUES(?,?,?,?)",
            (job_id, "jobnet", "https://example.test/job/42", now),
        )
        conn.execute(
            "INSERT INTO drafts(job_id,provider,body,created_at) VALUES(?,?,?,?)",
            (job_id, "template", "Bevar dette udkast", now),
        )
        conn.executemany(
            "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
            [(1, "draft_metadata", now), (2, "archive_flag", now), (3, "common_query_indexes", now)],
        )

    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)
    db._INITIALIZED_PATHS.discard(database.resolve())
    db.init_db()

    with db.connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        draft = conn.execute("SELECT body FROM drafts WHERE job_id=?", (job_id,)).fetchone()
        source = conn.execute("SELECT source_url FROM job_sources WHERE job_id=?", (job_id,)).fetchone()
        migration = conn.execute("SELECT name FROM schema_migrations WHERE version=4").fetchone()
    assert job["status"] == "shortlisted"
    assert job["archived"] == 1
    assert job["discovered_at"] == now
    assert job["score"] == 72
    assert job["qualification_score"] is None
    assert draft["body"] == "Bevar dette udkast"
    assert source["source_url"] == "https://example.test/job/42"
    assert migration["name"] == "multidimensional_scoring"

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(DEFAULT_PROFILE), encoding="utf-8")
    monkeypatch.setattr(main, "PROFILE_PATH", profile_path)
    monkeypatch.setattr(config, "ensure_data_files", lambda: None)
    assert main._rescore_all_jobs(force=False) == 1
    with db.connection() as conn:
        rescored = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert rescored["scoring_version"] == 2
        assert rescored["qualification_score"] is not None
        assert rescored["direction_score"] is not None
        assert rescored["curiosity_score"] is not None
        assert rescored["status"] == "shortlisted"
        assert rescored["archived"] == 1
        assert rescored["discovered_at"] == now
        assert conn.execute("SELECT body FROM drafts WHERE job_id=?", (job_id,)).fetchone()["body"] == "Bevar dette udkast"
