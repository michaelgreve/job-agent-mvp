from __future__ import annotations

from jobagent import db


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
