from __future__ import annotations

import pytest
from fastapi import HTTPException

from jobagent import main


def test_reset_endpoint_requires_exact_confirmation(monkeypatch):
    main.SCAN_STATE = main._new_scan_state()
    monkeypatch.setattr(main, "reset_application_data", lambda: {"jobs": 0, "sources": 0, "drafts": 0, "events": 0})

    with pytest.raises(HTTPException) as exc:
        main.reset_database(main.ResetDatabaseRequest(confirmation="nulstil"))
    assert exc.value.status_code == 422


def test_reset_endpoint_resets_ledger_and_scan_status(monkeypatch):
    main.SCAN_STATE = {**main._new_scan_state(), "phase": "complete", "message": "Gammel scanning"}
    deleted = {"jobs": 7, "sources": 7, "drafts": 2, "events": 12}
    monkeypatch.setattr(main, "reset_application_data", lambda: deleted.copy())
    monkeypatch.setattr(main, "_clear_scan_checkpoints", lambda: 2)

    result = main.reset_database(main.ResetDatabaseRequest(confirmation="NULSTIL"))

    assert result == {"ok": True, "deleted": {**deleted, "checkpoints": 2}}
    assert main.SCAN_STATE["phase"] == "idle"
    assert main.SCAN_STATE["running"] is False


def test_reset_endpoint_is_blocked_during_scan(monkeypatch):
    main.SCAN_STATE = {**main._new_scan_state(), "running": True}
    monkeypatch.setattr(main, "reset_application_data", lambda: pytest.fail("reset should not run"))

    with pytest.raises(HTTPException) as exc:
        main.reset_database(main.ResetDatabaseRequest(confirmation="NULSTIL"))
    assert exc.value.status_code == 409
