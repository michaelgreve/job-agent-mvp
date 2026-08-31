from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai import (
    AIError, GeneratedApplication, generate_application, test_provider_connection,
)
from .config import (
    CV_DIR, GENERATED_DIR, PROFILE_PATH, ROOT, SEARCHES_PATH, ensure_data_files,
    load_json, save_json,
)
from .db import connection, init_db, log_event, reset_application_data, rows_to_dicts, utcnow
from .dedupe import compare_jobs, content_hash, fingerprint, normalize_url
from .drafting import build_prompt, template_draft
from .extractors import (
    ExtractedJob, SearchResultCard, discover_search_results, extract_candidate_job,
    job_validation_error,
)
from .scoring import score_job

VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
app = FastAPI(title="Michael's Jobagent", version=VERSION)
app.mount("/static", StaticFiles(directory=ROOT / "jobagent" / "static"), name="static")

class ImportJobRequest(BaseModel):
    url: str = ""
    title: str = ""
    employer: str = ""
    location: str = ""
    description: str = ""
    application_url: str = ""
    source: str = ""

class StatusRequest(BaseModel):
    status: str
    confirmation: str = ""
    notes: str = ""

class ArchiveRequest(BaseModel):
    archived: bool

class ConfigRequest(BaseModel):
    profile: dict[str, Any]
    searches: dict[str, Any]

class ResetDatabaseRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=40)


class AIConnectionRequest(BaseModel):
    provider: str
    config: dict[str, Any] = Field(default_factory=dict)


class DraftSaveRequest(BaseModel):
    body: str = Field(min_length=20, max_length=30000)
    provider: str = "manual"
    model: str = ""
    facts_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

ALLOWED_STATUSES = {
    "new", "discovered", "shortlisted", "dismissed", "drafted", "awaiting_review",
    "submitted", "confirmation_received", "interview", "rejected", "withdrawn", "duplicate"
}


def _new_scan_state() -> dict[str, Any]:
    return {
        "running": False,
        "phase": "idle",
        "message": "Klar til at scanne",
        "searches": 0,
        "searches_done": 0,
        "listings_seen": 0,
        "load_more_clicks": 0,
        "api_batches": 0,
        "total_available": 0,
        "links": 0,
        "processed": 0,
        "prefiltered": 0,
        "filtered": 0,
        "new": 0,
        "duplicates": 0,
        "updated": 0,
        "detail_fallbacks": 0,
        "errors": [],
        "current_source": "",
        "current_title": "",
        "started_at": "",
        "finished_at": "",
        "limit_reached": False,
        "checkpoint_cards": 0,
        "checkpoints_committed": 0,
        "checkpoints_held": 0,
        "checkpoint_message": "",
    }


SCAN_STATE: dict[str, Any] = _new_scan_state()
SCAN_TASK: asyncio.Task | None = None


def _scan_snapshot() -> dict[str, Any]:
    snapshot = dict(SCAN_STATE)
    snapshot["errors"] = list(SCAN_STATE.get("errors", []))
    return snapshot


def _clear_scan_checkpoints() -> int:
    searches_config = load_json(SEARCHES_PATH)
    checkpoints_cleared = 0
    for search in searches_config.get("searches", []):
        incremental = search.get("incremental") if isinstance(search.get("incremental"), dict) else {}
        checkpoint = incremental.get("checkpoint") if isinstance(incremental.get("checkpoint"), dict) else {}
        if checkpoint.get("value") or checkpoint.get("committed_at"):
            checkpoints_cleared += 1
        checkpoint["value"] = ""
        checkpoint["committed_at"] = ""
        incremental["checkpoint"] = checkpoint
        search["incremental"] = incremental
    save_json(SEARCHES_PATH, searches_config)
    return checkpoints_cleared


def _rescore_all_jobs() -> None:
    profile = load_json(PROFILE_PATH)
    with connection() as conn:
        rows = rows_to_dicts(conn.execute("SELECT * FROM jobs WHERE duplicate_of IS NULL").fetchall())
        for job in rows:
            result = score_job(job, profile)
            conn.execute(
                "UPDATE jobs SET score=?,score_details=?,updated_at=? WHERE id=?",
                (result.score, json.dumps(result.details, ensure_ascii=False), utcnow(), job["id"]),
            )

@app.on_event("startup")
def startup() -> None:
    ensure_data_files()
    init_db()
    # Remove only clearly broken scanner imports from 0.1.1. Real applications,
    # manual entries and anything with a draft/status change are untouched.
    with connection() as conn:
        conn.execute("""
            DELETE FROM jobs
            WHERE status IN ('new','discovered')
              AND lower(trim(title)) IN ('ukendt stilling', 'find job', 'søg', 'jobnet', 'jobindex')
              AND lower(trim(COALESCE(employer,''))) IN ('', 'ukendt')
              AND length(trim(COALESCE(description,''))) < 80
              AND NOT EXISTS (SELECT 1 FROM drafts WHERE drafts.job_id=jobs.id)
        """)
    _rescore_all_jobs()

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((ROOT / "jobagent" / "templates" / "index.html").read_text(encoding="utf-8"))

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": VERSION}

@app.get("/api/config")
def get_config() -> dict:
    return {"profile": load_json(PROFILE_PATH), "searches": load_json(SEARCHES_PATH), "cv_files": sorted([p.name for p in CV_DIR.iterdir() if p.is_file() and p.name != "README.md"])}

@app.put("/api/config")
def put_config(payload: ConfigRequest) -> dict:
    save_json(PROFILE_PATH, payload.profile)
    save_json(SEARCHES_PATH, payload.searches)
    _rescore_all_jobs()
    return {"ok": True}

@app.post("/api/admin/reset-database")
def reset_database(payload: ResetDatabaseRequest) -> dict:
    global SCAN_STATE
    if SCAN_STATE.get("running"):
        raise HTTPException(409, "Vent til den aktuelle scanning er færdig, før databasen nulstilles.")
    if payload.confirmation != "NULSTIL":
        raise HTTPException(422, "Skriv NULSTIL præcist for at bekræfte.")
    counts = reset_application_data()
    counts["checkpoints"] = _clear_scan_checkpoints()
    SCAN_STATE = _new_scan_state()
    return {"ok": True, "deleted": counts}

@app.get("/api/jobs")
def list_jobs(status: str | None = None, include_duplicates: bool = False) -> dict:
    sql = "SELECT * FROM jobs"
    clauses: list[str] = []
    args: list[Any] = []
    if status:
        clauses.append("status=?")
        args.append(status)
    if not include_duplicates:
        clauses.append("duplicate_of IS NULL")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY COALESCE(score,-1) DESC, discovered_at DESC"
    with connection() as conn:
        jobs = rows_to_dicts(conn.execute(sql, args).fetchall())
        counts = dict(conn.execute(
            "SELECT status,COUNT(*) c FROM jobs WHERE duplicate_of IS NULL AND COALESCE(archived,0)=0 GROUP BY status"
        ).fetchall())
        active_count = int(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NULL AND COALESCE(archived,0)=0"
        ).fetchone()[0])
        archived_count = int(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NULL AND COALESCE(archived,0)=1"
        ).fetchone()[0])
    for job in jobs:
        try:
            job["score_details"] = json.loads(job.get("score_details") or "{}")
        except Exception:
            pass
    return {"jobs": jobs, "counts": counts, "active_count": active_count, "archived_count": archived_count}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Jobbet findes ikke")
        sources = rows_to_dicts(conn.execute("SELECT * FROM job_sources WHERE job_id=?", (job_id,)).fetchall())
        drafts = rows_to_dicts(conn.execute("SELECT * FROM drafts WHERE job_id=? ORDER BY id DESC", (job_id,)).fetchall())
    for draft in drafts:
        for field in ("facts_used", "warnings"):
            try:
                draft[field] = json.loads(draft.get(field) or "[]")
            except (json.JSONDecodeError, TypeError):
                draft[field] = []
    return {"job": dict(row), "sources": sources, "drafts": drafts}


def _missing(value: Any) -> bool:
    return not str(value or "").strip() or str(value).strip().casefold() in {"ukendt", "ukendt virksomhed"}


def _enrich_duplicate(conn: Any, job_id: int, incoming: dict[str, Any], profile: dict, now: str) -> bool:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return False
    merged = dict(row)
    changed = False
    simple_fields = [
        "employer", "location", "application_url", "source_job_id",
        "date_posted", "deadline", "employment_type",
    ]
    for field in simple_fields:
        value = str(incoming.get(field) or "").strip()
        if value and _missing(merged.get(field)):
            merged[field] = value
            changed = True

    incoming_description = str(incoming.get("description") or "").strip()
    existing_description = str(merged.get("description") or "").strip()
    if incoming_description and (len(existing_description) < 80 or len(incoming_description) > len(existing_description) * 1.20):
        merged["description"] = incoming_description
        changed = True

    if not changed:
        return False

    merged["canonical_url"] = normalize_url(merged.get("application_url") or merged.get("source_url"))
    merged["content_hash"] = content_hash(merged.get("description", ""))
    merged["fingerprint"] = fingerprint(
        merged.get("title", ""), merged.get("employer"), merged.get("location"), merged.get("source_job_id")
    )
    result = score_job(merged, profile)
    conn.execute(
        """UPDATE jobs SET employer=?,location=?,application_url=?,source_job_id=?,description=?,
           date_posted=?,deadline=?,employment_type=?,canonical_url=?,content_hash=?,fingerprint=?,
           score=?,score_details=?,updated_at=? WHERE id=?""",
        (
            merged.get("employer", ""), merged.get("location", ""), merged.get("application_url", ""),
            merged.get("source_job_id", ""), merged.get("description", ""), merged.get("date_posted", ""),
            merged.get("deadline", ""), merged.get("employment_type", ""), merged["canonical_url"],
            merged["content_hash"], merged["fingerprint"], result.score,
            json.dumps(result.details, ensure_ascii=False), now, job_id,
        ),
    )
    log_event(conn, "job_enriched", "Manglende annonceoplysninger blev opdateret", job_id)
    return True


def _store_job(data: dict[str, Any], initial_status: str | None = None) -> dict[str, Any]:
    validation = job_validation_error(ExtractedJob(
        source=str(data.get("source") or "manual"),
        source_job_id=str(data.get("source_job_id") or ""),
        source_url=str(data.get("source_url") or ""),
        application_url=str(data.get("application_url") or ""),
        title=str(data.get("title") or ""),
        employer=str(data.get("employer") or ""),
        location=str(data.get("location") or ""),
        description=str(data.get("description") or ""),
        date_posted=str(data.get("date_posted") or ""),
        deadline=str(data.get("deadline") or ""),
        employment_type=str(data.get("employment_type") or ""),
    ))
    if validation and data.get("source") not in {"historik", "manual"}:
        raise ValueError(validation)

    profile = load_json(PROFILE_PATH)
    data["canonical_url"] = normalize_url(data.get("application_url") or data.get("source_url"))
    data["content_hash"] = content_hash(data.get("description", ""))
    data["fingerprint"] = fingerprint(data.get("title", ""), data.get("employer"), data.get("location"), data.get("source_job_id"))
    score = score_job(data, profile)
    data["score"] = score.score
    data["score_details"] = json.dumps(score.details, ensure_ascii=False)
    now = utcnow()

    with connection() as conn:
        existing = rows_to_dicts(conn.execute(
            "SELECT * FROM jobs WHERE duplicate_of IS NULL ORDER BY discovered_at DESC LIMIT 1500"
        ).fetchall())
        duplicate_id = None
        duplicate_reason = ""
        best_similarity = 0.0
        for candidate in existing:
            similarity, reason = compare_jobs(data, candidate)
            if similarity > best_similarity:
                best_similarity = similarity
                duplicate_reason = reason
                duplicate_id = candidate["id"]
        if best_similarity < 0.88:
            duplicate_id = None
            duplicate_reason = ""

        if duplicate_id:
            conn.execute(
                "INSERT OR IGNORE INTO job_sources(job_id,source,source_url,first_seen) VALUES(?,?,?,?)",
                (duplicate_id, data.get("source", "manual"), data.get("source_url", ""), now),
            )
            enriched = _enrich_duplicate(conn, duplicate_id, data, profile, now)
            log_event(conn, "duplicate_seen", f"{data.get('source_url')} ({best_similarity:.2f})", duplicate_id)
            return {
                "id": duplicate_id, "duplicate": True, "enriched": enriched,
                "similarity": round(best_similarity, 3), "reason": duplicate_reason
            }

        status = "dismissed" if score.hard_reject else (initial_status or "discovered")
        cur = conn.execute("""
            INSERT INTO jobs(
                source,source_job_id,source_url,application_url,canonical_url,title,employer,location,
                description,date_posted,deadline,employment_type,content_hash,fingerprint,score,score_details,
                status,discovered_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("source", "manual"), data.get("source_job_id", ""), data.get("source_url", ""),
            data.get("application_url", ""), data["canonical_url"], data.get("title") or "Ukendt stilling",
            data.get("employer", ""), data.get("location", ""), data.get("description", ""),
            data.get("date_posted", ""), data.get("deadline", ""), data.get("employment_type", ""),
            data["content_hash"], data["fingerprint"], data["score"], data["score_details"],
            status, now, now,
        ))
        job_id = int(cur.lastrowid)
        if data.get("source_url"):
            conn.execute(
                "INSERT OR IGNORE INTO job_sources(job_id,source,source_url,first_seen) VALUES(?,?,?,?)",
                (job_id, data.get("source", "manual"), data.get("source_url", ""), now),
            )
        log_event(conn, "job_discovered", data.get("source_url", ""), job_id)
        return {"id": job_id, "duplicate": False, "score": data["score"]}

@app.post("/api/jobs/import")
async def import_job(payload: ImportJobRequest) -> dict:
    if payload.url:
        try:
            extracted = await extract_job(payload.url)
            data = extracted.dict()
        except Exception as exc:
            if not payload.title and not payload.description:
                raise HTTPException(422, f"Kunne ikke hente annoncen: {exc}") from exc
            data = payload.model_dump()
            data["source_url"] = payload.url
    else:
        data = payload.model_dump()
        data["source_url"] = ""
    for key in ["title", "employer", "location", "description", "application_url", "source"]:
        if getattr(payload, key, ""):
            data[key] = getattr(payload, key)
    if not data.get("source"):
        data["source"] = "manual"
    if not data.get("title"):
        raise HTTPException(422, "Titel mangler")
    return _store_job(data)

@app.post("/api/search/scan")
async def start_scan() -> dict:
    global SCAN_STATE, SCAN_TASK
    if SCAN_STATE.get("running"):
        return {"started": False, **_scan_snapshot()}
    SCAN_STATE = _new_scan_state()
    SCAN_STATE.update({
        "running": True, "phase": "starting", "message": "Starter scanning…",
        "started_at": utcnow(),
    })
    SCAN_TASK = asyncio.create_task(_run_scan())
    return {"started": True, **_scan_snapshot()}


@app.get("/api/search/scan/status")
def scan_status() -> dict:
    return _scan_snapshot()


async def _run_scan() -> None:
    global SCAN_STATE
    config = load_json(SEARCHES_PATH)
    scan_cfg = config.get("scan", {})
    profile = load_json(PROFILE_PATH)
    configured_searches = config.get("searches", [])
    searches = [
        (index, search) for index, search in enumerate(configured_searches)
        if search.get("enabled") and search.get("url")
    ]
    SCAN_STATE["searches"] = len(searches)
    if not searches:
        SCAN_STATE.update({
            "running": False, "phase": "complete",
            "message": "Ingen aktive søgninger. Tilføj en URL under Indstillinger.",
            "finished_at": utcnow(),
        })
        return

    max_results = max(10, int(scan_cfg.get("max_results_per_search", 500)))
    max_candidates = max(1, int(scan_cfg.get("max_links_per_search", 80)))
    max_load_more_clicks = max(1, int(scan_cfg.get("max_load_more_clicks", 70)))
    candidate_min_score = max(0, int(scan_cfg.get("candidate_min_score", 10)))
    store_min_score = max(0, int(scan_cfg.get("minimum_score_to_store", 10)))
    run_started_at = str(SCAN_STATE.get("started_at") or utcnow())
    pending_checkpoints: list[tuple[int, str]] = []
    checkpoint_blockers: list[str] = []

    try:
        for search_number, (config_index, search) in enumerate(searches, start=1):
            source_name = str(search.get("source") or search.get("name") or "søgning")
            incremental = search.get("incremental") if isinstance(search.get("incremental"), dict) else {}
            incremental_enabled = bool(incremental.get("enabled"))
            checkpoint = incremental.get("checkpoint") if isinstance(incremental.get("checkpoint"), dict) else {}
            checkpoint_value = str(checkpoint.get("value") or "")
            checkpoint_label = " siden sidste kontrolpunkt" if incremental_enabled and checkpoint_value else ""
            search_error_start = len(SCAN_STATE["errors"])
            search_partial_reasons: list[str] = []

            SCAN_STATE.update({
                "phase": "discovering",
                "current_source": source_name,
                "current_title": "",
                "message": f"Åbner {source_name} og indlæser søgeresultater{checkpoint_label}…",
            })

            baseline_seen = int(SCAN_STATE.get("listings_seen", 0))
            baseline_checkpoint_cards = int(SCAN_STATE.get("checkpoint_cards", 0))
            baseline_clicks = int(SCAN_STATE.get("load_more_clicks", 0))
            baseline_batches = int(SCAN_STATE.get("api_batches", 0))

            def discovery_progress(progress: dict[str, Any]) -> None:
                seen = int(progress.get("listings_seen", 0))
                eligible = int(progress.get("cards_after_checkpoint", seen))
                clicks = int(progress.get("load_more_clicks", 0))
                batches = int(progress.get("api_batches", 0))
                total = int(progress.get("total_available", 0))
                total_text = f" af ca. {total}" if total else ""
                if incremental_enabled and checkpoint_value:
                    result_text = f"{eligible} nye; {seen}{total_text} kontrolleret"
                else:
                    result_text = f"{seen}{total_text} jobkort fundet"
                SCAN_STATE.update({
                    "listings_seen": baseline_seen + seen,
                    "checkpoint_cards": baseline_checkpoint_cards + eligible,
                    "load_more_clicks": baseline_clicks + clicks,
                    "api_batches": baseline_batches + batches,
                    "total_available": max(int(SCAN_STATE.get("total_available", 0)), total),
                    "message": (
                        f"Indlæser resultater fra {source_name}… {result_text} "
                        f"efter {clicks} ekstra indlæsninger"
                    ),
                })

            try:
                discovery = await discover_search_results(
                    search["url"],
                    search.get("link_patterns", []),
                    max_results=max_results,
                    max_load_more_clicks=max_load_more_clicks,
                    headless=bool(scan_cfg.get("headless", True)),
                    on_progress=discovery_progress,
                    checkpoint=checkpoint if incremental_enabled else None,
                )
            except Exception as exc:
                SCAN_STATE["errors"].append(f"{search.get('name')}: {exc}")
                checkpoint_blockers.append(f"{search.get('name')}: søgningen fejlede")
                SCAN_STATE["searches_done"] = search_number
                continue

            SCAN_STATE["listings_seen"] = baseline_seen + discovery.listings_seen
            SCAN_STATE["checkpoint_cards"] = baseline_checkpoint_cards + discovery.cards_after_checkpoint
            SCAN_STATE["load_more_clicks"] = baseline_clicks + discovery.load_more_clicks
            SCAN_STATE["api_batches"] = baseline_batches + discovery.api_batches
            SCAN_STATE["total_available"] = max(
                int(SCAN_STATE.get("total_available", 0)), discovery.total_available
            )
            if (
                source_name.casefold() == "jobnet"
                and discovery.total_available > discovery.listings_seen
                and discovery.load_more_clicks == 0
                and not discovery.checkpoint_reached
            ):
                SCAN_STATE["errors"].append(
                    f"Jobnet oplyste {discovery.total_available} resultater, men knappen "
                    f"‘Indlæs flere job’ kunne ikke aktiveres. Kun {discovery.listings_seen} blev læst."
                )
            SCAN_STATE["limit_reached"] = bool(SCAN_STATE.get("limit_reached")) or discovery.limit_reached

            if incremental_enabled and not discovery.checkpoint_supported:
                search_partial_reasons.append(
                    f"{source_name} understøtter endnu ikke et sikkert scanningskontrolpunkt"
                )
            if discovery.limit_reached and not discovery.checkpoint_reached:
                search_partial_reasons.append("søgeresultatgrænsen blev nået før alle nye opslag var læst")
            if incremental_enabled and discovery.cards and not discovery.oldest_date_posted:
                search_partial_reasons.append("opslagenes publiceringsdato kunne ikke bekræftes")

            ranked: list[tuple[int, SearchResultCard]] = []
            for card in discovery.cards:
                result = score_job({
                    "title": card.title,
                    "description": card.snippet,
                    "source_url": card.url,
                }, profile)
                if result.score >= candidate_min_score:
                    ranked.append((result.score, card))
                else:
                    SCAN_STATE["prefiltered"] += 1

            ranked.sort(key=lambda item: item[0], reverse=True)
            candidates = [card for _, card in ranked[:max_candidates]]
            if len(ranked) > max_candidates:
                SCAN_STATE["prefiltered"] += len(ranked) - max_candidates
                search_partial_reasons.append(
                    f"kandidatgrænsen på {max_candidates} blev nået ({len(ranked)} kandidater fandtes)"
                )
            SCAN_STATE["links"] += len(candidates)
            if incremental_enabled and checkpoint_value:
                SCAN_STATE["message"] = (
                    f"{discovery.cards_after_checkpoint} nye jobkort siden kontrolpunktet; "
                    f"{len(candidates)} ser relevante nok ud til nærmere kontrol."
                )
            else:
                SCAN_STATE["message"] = (
                    f"{discovery.listings_seen} jobkort set; "
                    f"{len(candidates)} ser relevante nok ud til nærmere kontrol."
                )

            for link_index, card in enumerate(candidates, start=1):
                SCAN_STATE.update({
                    "phase": "importing",
                    "current_source": source_name,
                    "current_title": card.title or card.url,
                    "message": f"Kontrollerer kandidat {link_index} af {len(candidates)} fra {source_name}…",
                })
                fetched_page = False
                try:
                    extracted, fetched_page, used_fallback = await extract_candidate_job(card)
                    if used_fallback:
                        SCAN_STATE["detail_fallbacks"] += 1
                    SCAN_STATE["current_title"] = extracted.title
                    full_score = score_job(extracted.dict(), profile)
                    if full_score.score < store_min_score:
                        SCAN_STATE["filtered"] += 1
                    else:
                        stored = _store_job(extracted.dict(), initial_status="new")
                        if stored.get("duplicate"):
                            SCAN_STATE["duplicates"] += 1
                            if stored.get("enriched"):
                                SCAN_STATE["updated"] += 1
                        else:
                            SCAN_STATE["new"] += 1
                except Exception as exc:
                    SCAN_STATE["errors"].append(f"{card.url}: {exc}")
                finally:
                    SCAN_STATE["processed"] += 1
                if fetched_page:
                    await asyncio.sleep(float(scan_cfg.get("delay_seconds", 1.5)))

            search_had_errors = len(SCAN_STATE["errors"]) > search_error_start
            if search_partial_reasons:
                checkpoint_blockers.extend(
                    f"{search.get('name')}: {reason}" for reason in search_partial_reasons
                )
            if (
                incremental_enabled
                and discovery.checkpoint_supported
                and not search_had_errors
                and not search_partial_reasons
            ):
                pending_checkpoints.append((config_index, "published_at"))
            elif incremental_enabled:
                SCAN_STATE["checkpoints_held"] += 1
            SCAN_STATE["searches_done"] = search_number

        error_count = len(SCAN_STATE["errors"])
        checkpoints_committed = 0
        # Atomic high-watermark commit: no search checkpoint moves unless the entire
        # run was complete. Re-reading duplicates is cheap; skipping unseen jobs is not.
        if not error_count and not checkpoint_blockers and pending_checkpoints:
            latest_config = load_json(SEARCHES_PATH)
            latest_searches = latest_config.get("searches", [])
            committed_at = utcnow()
            for index, kind in pending_checkpoints:
                if index >= len(latest_searches):
                    continue
                incremental = latest_searches[index].setdefault("incremental", {})
                checkpoint = incremental.setdefault("checkpoint", {})
                checkpoint.update({
                    "kind": kind,
                    "value": run_started_at,
                    "committed_at": committed_at,
                })
                checkpoints_committed += 1
            if checkpoints_committed:
                save_json(SEARCHES_PATH, latest_config)
        elif pending_checkpoints:
            SCAN_STATE["checkpoints_held"] += len(pending_checkpoints)

        SCAN_STATE["checkpoints_committed"] = checkpoints_committed
        if checkpoints_committed:
            SCAN_STATE["checkpoint_message"] = (
                f"{checkpoints_committed} scanningskontrolpunkt blev opdateret."
            )
        elif pending_checkpoints or checkpoint_blockers:
            reason = checkpoint_blockers[0] if checkpoint_blockers else "scanningen var ikke komplet"
            SCAN_STATE["checkpoint_message"] = (
                f"Kontrolpunktet blev ikke flyttet: {reason}."
            )

        limit_note = " Søgeresultatgrænsen blev nået." if SCAN_STATE.get("limit_reached") else ""
        checkpoint_note = f" {SCAN_STATE['checkpoint_message']}" if SCAN_STATE.get("checkpoint_message") else ""
        fallback_note = (
            f" {SCAN_STATE['detail_fallbacks']} Jobindex-opslag blev reddet fra resultatkortet."
            if SCAN_STATE.get("detail_fallbacks") else ""
        )
        SCAN_STATE.update({
            "running": False,
            "phase": "complete" if not error_count else "complete_with_errors",
            "message": (
                f"Færdig: {SCAN_STATE['listings_seen']} jobkort set, "
                f"{SCAN_STATE['new']} nye, {SCAN_STATE['duplicates']} dubletter, "
                f"{SCAN_STATE['filtered'] + SCAN_STATE['prefiltered']} frasorteret og {error_count} fejl."
                f"{limit_note}{fallback_note}{checkpoint_note}"
            ),
            "current_source": "",
            "current_title": "",
            "finished_at": utcnow(),
        })
    except Exception as exc:
        SCAN_STATE.update({
            "running": False, "phase": "failed",
            "message": f"Scanningen stoppede: {exc}. Intet kontrolpunkt blev flyttet.",
            "finished_at": utcnow(),
            "checkpoint_message": "Kontrolpunktet blev ikke flyttet, fordi scanningen fejlede.",
        })
        SCAN_STATE["errors"].append(str(exc))

@app.post("/api/jobs/{job_id}/archive")
def archive_job(job_id: int, payload: ArchiveRequest) -> dict:
    now = utcnow()
    archived = 1 if payload.archived else 0
    archived_at = now if payload.archived else None
    with connection() as conn:
        row = conn.execute("SELECT id,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Jobbet findes ikke")
        conn.execute(
            "UPDATE jobs SET archived=?, archived_at=?, updated_at=? WHERE id=?",
            (archived, archived_at, now, job_id),
        )
        log_event(conn, "job_archived" if payload.archived else "job_restored", row["status"], job_id)
    return {"ok": True, "archived": bool(archived)}

@app.post("/api/jobs/{job_id}/status")
def update_status(job_id: int, payload: StatusRequest) -> dict:
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(422, "Ukendt status")
    now = utcnow()
    applied_at = now if payload.status in {"submitted", "confirmation_received"} else None
    with connection() as conn:
        row = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Jobbet findes ikke")
        conn.execute("""
            UPDATE jobs SET status=?, confirmation=COALESCE(NULLIF(?,''),confirmation),
            notes=COALESCE(NULLIF(?,''),notes), applied_at=COALESCE(?,applied_at), updated_at=? WHERE id=?
        """, (payload.status, payload.confirmation, payload.notes, applied_at, now, job_id))
        log_event(conn, "status_changed", payload.status, job_id)
    return {"ok": True}

@app.post("/api/ai/test")
async def test_ai_connection(payload: AIConnectionRequest) -> dict:
    profile = load_json(PROFILE_PATH)
    config = {**profile.get("ai", {}), **payload.config}
    try:
        return await test_provider_connection(payload.provider, config)
    except AIError as exc:
        raise HTTPException(422, str(exc)) from exc


def _save_generated_draft(
    job_id: int,
    job: dict[str, Any],
    result: GeneratedApplication,
    prompt: str = "",
) -> dict:
    body = result.application.strip()
    if not body:
        raise HTTPException(422, "Ansøgningsteksten er tom")
    now = utcnow()
    with connection() as conn:
        cur = conn.execute(
            """INSERT INTO drafts(
                   job_id,provider,model,body,facts_used,warnings,prompt_snapshot,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                job_id, result.provider, result.model, body,
                json.dumps(result.facts_used, ensure_ascii=False),
                json.dumps(result.warnings, ensure_ascii=False),
                prompt, now,
            ),
        )
        conn.execute("UPDATE jobs SET status='drafted',updated_at=? WHERE id=?", (now, job_id))
        log_event(conn, "draft_created", f"{result.provider}:{result.model}", job_id)
        draft_id = int(cur.lastrowid)
    out = GENERATED_DIR / f"{job_id:04d}_{_safe(job.get('employer'))}_{_safe(job.get('title'))}.txt"
    out.write_text(body, encoding="utf-8")
    return {
        "id": draft_id, "body": body, "provider": result.provider,
        "model": result.model, "facts_used": result.facts_used,
        "warnings": result.warnings, "file": out.name, "mode": "draft",
    }


@app.post("/api/jobs/{job_id}/draft")
async def create_draft(job_id: int) -> dict:
    profile = load_json(PROFILE_PATH)
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Jobbet findes ikke")
    job = dict(row)
    ai_cfg = profile.get("ai", {})
    provider = str(ai_cfg.get("provider") or "template")
    prompt = build_prompt(job, profile)
    try:
        result = await generate_application(
            provider, prompt, ai_cfg, template_result=template_draft(job, profile)
        )
    except AIError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result.requires_external_response:
        return {
            "mode": "handoff", "provider": result.provider,
            "prompt": result.handoff_prompt, "job_id": job_id,
        }
    return _save_generated_draft(job_id, job, result, prompt)


@app.post("/api/jobs/{job_id}/draft/save")
def save_draft(job_id: int, payload: DraftSaveRequest) -> dict:
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Jobbet findes ikke")
    result = GeneratedApplication(
        application=payload.body, provider=payload.provider or "manual",
        model=payload.model, facts_used=payload.facts_used, warnings=payload.warnings,
    )
    return _save_generated_draft(job_id, dict(row), result)


def _safe(value: str | None) -> str:
    import re
    value = re.sub(r"[^A-Za-z0-9ÆØÅæøå_-]+", "_", value or "job")
    return value.strip("_")[:45] or "job"

@app.get("/api/jobs/{job_id}/application.txt")
def download_application(job_id: int):
    with connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        draft = conn.execute("SELECT * FROM drafts WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    if not job or not draft:
        raise HTTPException(404, "Ingen ansøgning fundet")
    filename = f"Ansøgning_{_safe(job['employer'])}_{_safe(job['title'])}.txt"
    return PlainTextResponse(draft["body"], headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@app.post("/api/jobs/{job_id}/launch-assist")
def launch_assist(job_id: int) -> dict:
    with connection() as conn:
        if not conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
            raise HTTPException(404, "Jobbet findes ikke")
    subprocess.Popen([sys.executable, "-m", "jobagent.browser_assist", str(job_id)], cwd=ROOT)
    return {"ok": True, "message": "Browserassistenten åbner lokalt"}

@app.get("/api/export/joblog.csv")
def export_joblog_csv() -> StreamingResponse:
    with connection() as conn:
        rows = rows_to_dicts(conn.execute("""
            SELECT applied_at,employer,title,source_url,application_url,status,confirmation,notes
            FROM jobs WHERE status IN ('submitted','confirmation_received','interview','rejected')
            ORDER BY applied_at DESC
        """).fetchall())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Ansøgningsdato", "Arbejdsgiver", "Stilling", "Annonce", "Ansøgningslink", "Status", "Bekræftelse", "Noter"])
    for r in rows:
        writer.writerow([r[k] or "" for k in ["applied_at","employer","title","source_url","application_url","status","confirmation","notes"]])
    data = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(iter([data]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=joblog.csv"})

@app.post("/api/history/import")
async def import_history(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    imported = 0
    duplicates = 0
    for row in reader:
        lower = {str(k).casefold().strip(): (v or "").strip() for k, v in row.items() if k}
        data = {
            "source": lower.get("source", "historik"),
            "source_url": lower.get("url", lower.get("annonce", lower.get("link", ""))),
            "application_url": lower.get("ansøgningslink", lower.get("application_url", "")),
            "title": lower.get("stilling", lower.get("titel", lower.get("title", "Ukendt stilling"))),
            "employer": lower.get("arbejdsgiver", lower.get("virksomhed", lower.get("company", ""))),
            "location": lower.get("sted", lower.get("location", "")),
            "description": lower.get("beskrivelse", "Historisk ansøgning importeret fra CSV"),
        }
        result = _store_job(data)
        if result.get("duplicate"):
            duplicates += 1
            target_id = result["id"]
        else:
            imported += 1
            target_id = result["id"]
        applied = lower.get("ansøgningsdato", lower.get("dato", lower.get("date", "")))
        with connection() as conn:
            conn.execute("UPDATE jobs SET status='submitted', applied_at=COALESCE(NULLIF(?,''),?) WHERE id=?", (applied, utcnow(), target_id))
    return {"imported": imported, "duplicates": duplicates}
