from __future__ import annotations

import asyncio
import json

from jobagent import db, main
from jobagent.config import DEFAULT_PROFILE


def _setup(tmp_path, monkeypatch, provider: str) -> int:
    profile = json.loads(json.dumps(DEFAULT_PROFILE))
    profile["ai"]["provider"] = provider
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(main, "PROFILE_PATH", profile_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobagent.db")
    monkeypatch.setattr(db, "ensure_data_files", lambda: None)
    generated = tmp_path / "generated"
    generated.mkdir()
    monkeypatch.setattr(main, "GENERATED_DIR", generated)
    now = db.utcnow()
    with db.connection() as conn:
        cur = conn.execute(
            """INSERT INTO jobs(
                source,title,employer,location,description,score,score_details,status,discovered_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "manual", "Frontendudvikler", "Eksempel A/S", "København",
                "Angular TypeScript RxJS og brugergrænseflader", 75,
                json.dumps({"recommendation": "Stærkt match"}), "discovered", now, now,
            ),
        )
        return int(cur.lastrowid)


def test_clipboard_generation_returns_handoff_without_storing_draft(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, "clipboard")
    result = asyncio.run(main.create_draft(job_id))
    assert result["mode"] == "handoff"
    assert "Frontendudvikler" in result["prompt"]
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_template_generation_stores_structured_draft(tmp_path, monkeypatch):
    job_id = _setup(tmp_path, monkeypatch, "template")
    result = asyncio.run(main.create_draft(job_id))
    assert result["mode"] == "draft"
    assert result["provider"] == "template"
    assert result["facts_used"]
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE job_id=?", (job_id,)).fetchone()
    assert row is not None
    assert json.loads(row["warnings"])
