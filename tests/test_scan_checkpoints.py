from __future__ import annotations

import asyncio
from copy import deepcopy

from jobagent import main
from jobagent.config import normalize_searches_config
from jobagent.extractors import (
    SearchDiscovery,
    SearchResultCard,
    _apply_publication_checkpoint,
    _jobnet_publication_order_url,
)


def test_old_search_config_gets_source_neutral_checkpoint():
    data = {
        "searches": [
            {"source": "jobnet", "name": "A", "url": "https://jobnet.dk/find-job"},
            {"source": "jobindex", "name": "B", "url": "https://jobindex.dk/jobsoegning"},
        ],
        "scan": {},
    }
    normalized = normalize_searches_config(data)
    assert normalized["searches"][0]["incremental"]["enabled"] is True
    assert normalized["searches"][0]["incremental"]["checkpoint"]["kind"] == "published_at"
    assert normalized["searches"][1]["incremental"]["enabled"] is False
    assert normalized["searches"][1]["incremental"]["checkpoint"]["kind"] == ""


def test_jobnet_runtime_url_forces_publication_order_without_duplicate_parameter():
    url = "https://jobnet.dk/find-job?kmRadius=10&OrderType=Relevance&postalCode=2720"
    runtime = _jobnet_publication_order_url(url)
    assert "orderType=PublicationDate" in runtime
    assert "Relevance" not in runtime
    assert "postalCode=2720" in runtime


def test_publication_checkpoint_keeps_new_equal_and_undated_cards():
    def card(job_id: str, date: str | None) -> SearchResultCard:
        data = {
            "source": "jobnet",
            "source_job_id": job_id,
            "source_url": f"https://jobnet.dk/find-job/{job_id}",
            "application_url": f"https://jobnet.dk/find-job/{job_id}",
            "title": job_id,
            "description": "En tilstrækkeligt lang beskrivelse af et relevant softwarejob.",
        }
        if date is not None:
            data["date_posted"] = date
        return SearchResultCard(url=data["source_url"], title=job_id, snippet="software", job_data=data)

    cards = [
        card("new", "2026-07-21T12:00:00+02:00"),
        card("equal", "2026-07-20T10:00:00Z"),
        card("old", "2026-07-19T09:00:00Z"),
        card("undated", None),
    ]
    kept, reached, newest, oldest = _apply_publication_checkpoint(
        cards, "2026-07-20T10:00:00Z"
    )
    assert [item.title for item in kept] == ["new", "equal", "undated"]
    assert reached is True
    assert newest.startswith("2026-07-21T10:00:00")
    assert oldest.startswith("2026-07-19T09:00:00")


def _search_config() -> dict:
    return {
        "searches": [{
            "name": "Jobnet test",
            "source": "jobnet",
            "url": "https://jobnet.dk/find-job?postalCode=2720",
            "enabled": True,
            "link_patterns": ["/find-job/"],
            "incremental": {
                "enabled": True,
                "checkpoint": {"kind": "published_at", "value": "", "committed_at": ""},
            },
        }],
        "scan": {
            "max_results_per_search": 500,
            "max_links_per_search": 80,
            "max_load_more_clicks": 70,
            "candidate_min_score": 10,
            "minimum_score_to_store": 10,
            "delay_seconds": 0,
            "headless": True,
        },
    }


def test_successful_scan_commits_start_time_atomically(monkeypatch):
    config = _search_config()
    saved: list[dict] = []

    def fake_load(path):
        if path == main.SEARCHES_PATH:
            return deepcopy(config if not saved else saved[-1])
        return {"preferences": {}, "career": {}}

    async def fake_discover(*args, **kwargs):
        return SearchDiscovery(
            cards=[], listings_seen=0, checkpoint_supported=True,
            checkpoint_reached=True, cards_after_checkpoint=0,
        )

    monkeypatch.setattr(main, "load_json", fake_load)
    monkeypatch.setattr(main, "save_json", lambda path, data: saved.append(deepcopy(data)))
    monkeypatch.setattr(main, "discover_search_results", fake_discover)
    main.SCAN_STATE = {
        **main._new_scan_state(),
        "running": True,
        "started_at": "2026-07-21T08:15:00+00:00",
    }

    asyncio.run(main._run_scan())

    assert len(saved) == 1
    checkpoint = saved[0]["searches"][0]["incremental"]["checkpoint"]
    assert checkpoint["value"] == "2026-07-21T08:15:00+00:00"
    assert checkpoint["kind"] == "published_at"
    assert main.SCAN_STATE["checkpoints_committed"] == 1


def test_failed_scan_never_moves_checkpoint(monkeypatch):
    config = _search_config()
    saved: list[dict] = []

    def fake_load(path):
        return deepcopy(config) if path == main.SEARCHES_PATH else {"preferences": {}, "career": {}}

    async def fake_discover(*args, **kwargs):
        raise RuntimeError("Jobnet gik i stykker")

    monkeypatch.setattr(main, "load_json", fake_load)
    monkeypatch.setattr(main, "save_json", lambda path, data: saved.append(deepcopy(data)))
    monkeypatch.setattr(main, "discover_search_results", fake_discover)
    main.SCAN_STATE = {
        **main._new_scan_state(),
        "running": True,
        "started_at": "2026-07-21T08:15:00+00:00",
    }

    asyncio.run(main._run_scan())

    assert saved == []
    assert main.SCAN_STATE["errors"]
    assert main.SCAN_STATE["checkpoints_committed"] == 0


def test_jobindex_search_patterns_are_migrated_without_enabling_checkpoint():
    from jobagent.config import normalize_searches_config

    data = {
        "searches": [{
            "source": "jobindex",
            "name": "Jobindex",
            "url": "https://www.jobindex.dk/jobsoegning/it/systemudvikling",
            "enabled": True,
            "link_patterns": ["/jobannonce/"],
        }],
        "scan": {},
    }
    normalized = normalize_searches_config(data)
    search = normalized["searches"][0]
    assert "/vis-job/" in search["link_patterns"]
    assert "/c?t=" in search["link_patterns"]
    assert search["incremental"]["enabled"] is False
    assert search["incremental"]["checkpoint"]["kind"] == ""
