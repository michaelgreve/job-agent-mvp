from __future__ import annotations

import sqlite3

from jobagent import main


def _profile():
    return {
        "preferences": {"preferred_terms": ["software", "frontend"]},
        "career": {"skills": ["Angular", "TypeScript", "JavaScript"]},
    }


def _job(title="Frontend Developer"):
    return {
        "source": "jobnet",
        "source_job_id": "new-status-1",
        "source_url": "https://jobnet.dk/find-job/new-status-1",
        "application_url": "https://example.com/apply/new-status-1",
        "title": title,
        "employer": "Example A/S",
        "location": "København",
        "description": "Frontend software development with Angular TypeScript JavaScript and user interfaces. " * 3,
    }


def test_new_status_is_allowed():
    assert "new" in main.ALLOWED_STATUSES


def test_scanned_job_can_be_stored_as_new(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.sqlite3"

    def fake_connection():
        class Ctx:
            def __enter__(self):
                self.conn = sqlite3.connect(db_path)
                self.conn.row_factory = sqlite3.Row
                self.conn.executescript(main.connection.__globals__["SCHEMA"] if "SCHEMA" in main.connection.__globals__ else "")
                return self.conn
            def __exit__(self, exc_type, exc, tb):
                if exc_type is None:
                    self.conn.commit()
                self.conn.close()
        return Ctx()

    # Reuse the real DB module schema without touching the user's data path.
    from jobagent import db
    def isolated_connection():
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

    monkeypatch.setattr(main, "connection", isolated_connection)
    monkeypatch.setattr(main, "load_json", lambda path: _profile())
    result = main._store_job(_job(), initial_status="new")
    assert result["duplicate"] is False
    with isolated_connection() as conn:
        status = conn.execute("SELECT status FROM jobs WHERE id=?", (result["id"],)).fetchone()[0]
    assert status == "new"


def test_manual_default_remains_discovered(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    from jobagent import db

    def isolated_connection():
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

    monkeypatch.setattr(main, "connection", isolated_connection)
    monkeypatch.setattr(main, "load_json", lambda path: _profile())
    data = _job()
    data["source"] = "manual"
    data["source_job_id"] = "manual-status-1"
    data["source_url"] = "https://example.com/jobs/manual-status-1"
    data["application_url"] = "https://example.com/apply/manual-status-1"
    result = main._store_job(data)
    with isolated_connection() as conn:
        status = conn.execute("SELECT status FROM jobs WHERE id=?", (result["id"],)).fetchone()[0]
    assert status == "discovered"
