from __future__ import annotations

import sqlite3

from jobagent import db, main


def _isolated_connection(db_path):
    class Ctx:
        def __enter__(self):
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(db.SCHEMA)
            return self.conn
        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self.conn.commit()
            self.conn.close()
    return Ctx()


def test_schema_has_archive_columns(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)
    db.init_db()
    with db.connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "archived" in columns
    assert "archived_at" in columns


def test_archiving_preserves_workflow_status(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(main, "connection", lambda: _isolated_connection(database))
    now = db.utcnow()
    with _isolated_connection(database) as conn:
        job_id = conn.execute(
            "INSERT INTO jobs(title,description,status,discovered_at,updated_at) VALUES(?,?,?,?,?)",
            ("Testjob", "Beskrivelse", "shortlisted", now, now),
        ).lastrowid

    result = main.archive_job(int(job_id), main.ArchiveRequest(archived=True))
    assert result == {"ok": True, "archived": True}
    with _isolated_connection(database) as conn:
        row = conn.execute("SELECT status,archived,archived_at FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "shortlisted"
    assert row["archived"] == 1
    assert row["archived_at"]

    main.archive_job(int(job_id), main.ArchiveRequest(archived=False))
    with _isolated_connection(database) as conn:
        row = conn.execute("SELECT status,archived,archived_at FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "shortlisted"
    assert row["archived"] == 0
    assert row["archived_at"] is None


def test_list_counts_only_active_jobs_but_reports_archive_total(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(main, "connection", lambda: _isolated_connection(database))
    now = db.utcnow()
    with _isolated_connection(database) as conn:
        conn.execute(
            "INSERT INTO jobs(title,description,status,archived,discovered_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("Aktiv", "Beskrivelse", "new", 0, now, now),
        )
        conn.execute(
            "INSERT INTO jobs(title,description,status,archived,discovered_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("Arkiveret", "Beskrivelse", "new", 1, now, now),
        )
    result = main.list_jobs()
    assert result["active_count"] == 1
    assert result["archived_count"] == 1
    assert result["counts"]["new"] == 1
    assert len(result["jobs"]) == 2


def test_migrates_024_database_before_creating_archive_index(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
        CREATE TABLE jobs (
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
            duplicate_of INTEGER,
            duplicate_reason TEXT,
            notes TEXT NOT NULL DEFAULT '',
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            applied_at TEXT,
            confirmation TEXT
        );
        CREATE TABLE job_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            UNIQUE(job_id, source_url)
        );
        CREATE TABLE drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            body TEXT NOT NULL,
            facts_used TEXT NOT NULL DEFAULT '[]',
            warnings TEXT NOT NULL DEFAULT '[]',
            prompt_snapshot TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            event_type TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        );
        """)
        now = db.utcnow()
        conn.execute(
            "INSERT INTO jobs(title,description,status,discovered_at,updated_at) VALUES(?,?,?,?,?)",
            ("Bevar mig", "Eksisterende job", "shortlisted", now, now),
        )
        conn.commit()

    monkeypatch.setattr(db, "DB_PATH", database)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)
    db.init_db()

    with sqlite3.connect(database) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(jobs)").fetchall()}
        row = conn.execute("SELECT title,status,archived,archived_at FROM jobs").fetchone()

    assert "archived" in columns
    assert "archived_at" in columns
    assert "idx_jobs_archived" in indexes
    assert row == ("Bevar mig", "shortlisted", 0, None)


def test_archived_jobs_still_participate_in_duplicate_detection(tmp_path, monkeypatch):
    database = tmp_path / "jobagent.db"
    monkeypatch.setattr(main, "connection", lambda: _isolated_connection(database))
    monkeypatch.setattr(main, "load_json", lambda path: {
        "preferences": {"preferred_terms": ["frontend", "software"]},
        "career": {"skills": ["Angular", "TypeScript"]},
    })
    now = db.utcnow()
    description = "Frontend softwareudvikling med Angular og TypeScript til en stabil kundeportal. " * 3
    with _isolated_connection(database) as conn:
        job_id = conn.execute(
            """INSERT INTO jobs(
                   source,source_job_id,source_url,title,employer,location,description,
                   status,archived,discovered_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "jobnet", "archived-duplicate-1", "https://jobnet.dk/find-job/archived-duplicate-1",
                "Frontendudvikler", "Eksempel A/S", "København", description,
                "shortlisted", 1, now, now,
            ),
        ).lastrowid

    result = main._store_job({
        "source": "jobnet",
        "source_job_id": "archived-duplicate-1",
        "source_url": "https://jobnet.dk/find-job/archived-duplicate-1",
        "application_url": "https://example.com/apply/archived-duplicate-1",
        "title": "Frontendudvikler",
        "employer": "Eksempel A/S",
        "location": "København",
        "description": description,
    }, initial_status="new")

    assert result["duplicate"] is True
    assert result["id"] == job_id
    with _isolated_connection(database) as conn:
        row = conn.execute(
            "SELECT status,archived FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert dict(row) == {"status": "shortlisted", "archived": 1}
    assert count == 1
