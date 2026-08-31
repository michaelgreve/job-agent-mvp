from __future__ import annotations

import json
import sqlite3

from jobagent import db, main
from jobagent.ai import GeneratedApplication


def _insert_job():
    now = db.utcnow()
    with db.connection() as conn:
        cur = conn.execute(
            """INSERT INTO jobs(
                source,title,employer,location,description,status,discovered_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            ("manual", "Frontendudvikler", "Eksempel A/S", "København", "Angular", "discovered", now, now),
        )
        return int(cur.lastrowid)


def test_database_migrates_draft_metadata_columns(tmp_path, monkeypatch):
    path = tmp_path / "jobagent.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)
    db.init_db()
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
    assert {"facts_used", "warnings", "prompt_snapshot"}.issubset(columns)


def test_generated_draft_stores_metadata_and_text_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobagent.db")
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)
    monkeypatch.setattr(main, "GENERATED_DIR", tmp_path / "generated")
    main.GENERATED_DIR.mkdir()
    job_id = _insert_job()
    with db.connection() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    result = GeneratedApplication(
        application="Dette er en målrettet ansøgning med tilstrækkelig længde.",
        facts_used=["Angular"], warnings=["Kontrollér kontaktperson"],
        provider="ollama", model="qwen-test",
    )
    saved = main._save_generated_draft(job_id, job, result, "prompt snapshot")

    assert saved["provider"] == "ollama"
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id=?", (saved["id"],)).fetchone()
        status = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    assert json.loads(row["facts_used"]) == ["Angular"]
    assert json.loads(row["warnings"]) == ["Kontrollér kontaktperson"]
    assert row["prompt_snapshot"] == "prompt snapshot"
    assert status == "drafted"
    assert (main.GENERATED_DIR / saved["file"]).exists()
