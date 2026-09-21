from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobagent.db"
PROFILE_PATH = DATA_DIR / "profile.json"
SEARCHES_PATH = DATA_DIR / "searches.json"
SCORING_PATH = DATA_DIR / "scoring.json"
PROFILE_EXAMPLE_PATH = DATA_DIR / "profile.example.json"
SEARCHES_EXAMPLE_PATH = DATA_DIR / "searches.example.json"
SCORING_DEFAULT_PATH = ROOT / "jobagent" / "scoring.v2.json"
GENERATED_DIR = DATA_DIR / "generated"
CV_DIR = DATA_DIR / "cv"
BROWSER_PROFILE_DIR = DATA_DIR / "browser-profile"

DEFAULT_PROFILE: dict[str, Any] = {
    "person": {
        "full_name": "",
        "preferred_name": "",
        "email": "",
        "phone": "",
        "address": "",
        "postal_code": "",
        "city": "",
        "country": "Denmark",
    },
    "career": {
        "headline": "Frontendudvikler og webkonsulent",
        "summary": "Erfaren frontendudvikler med fokus på enkle og brugbare løsninger.",
        "years_professional": 0,
        "skills": [
            "Angular", "TypeScript", "JavaScript", "HTML", "SCSS", "RxJS",
            "NgRx", "Angular Material", "frontendarkitektur", "webudvikling",
            "brugergrænseflader", "fejlfinding", "systemudvikling"
        ],
        "learning": ["Vue", "React", "Next.js", "Nuxt", "Docker", "PLC/automation"],
        "experience_highlights": [],
        "education": [],
        "languages": [],
        "drivers_license": "",
        "evidence": {
            "practical": [
                {
                    "label": "Cykelværksted og reparation gennem Nytteindsats",
                    "terms": ["cykelværksted", "cykelreparation", "cykelmekanik", "værksted", "reparation"],
                    "professional_employment": False
                },
                {
                    "label": "Praktisk fejlfinding, reparation og gør-det-selv i hjemmet",
                    "terms": ["praktisk fejlfinding", "reparation", "gør-det-selv", "værktøj"],
                    "professional_employment": False
                }
            ],
            "creative_people": [
                {
                    "label": "D&D-gamemaster og historiefortælling",
                    "terms": ["d&d", "gamemaster", "game master", "historiefortælling", "storytelling", "rollespil"],
                    "professional_employment": False
                },
                {
                    "label": "Amatørteater",
                    "terms": ["amatørteater", "teater", "scene", "publikum"],
                    "professional_employment": False
                }
            ]
        },
        "direction": {
            "interests": ["CTS", "BMS", "automation", "PLC"]
        },
    },
    "preferences": {
        "locations": ["København", "Storkøbenhavn", "Frederiksberg", "Lyngby", "Glostrup", "Roskilde"],
        "remote_ok": True,
        "preferred_terms": [
            "frontend", "webudvikler", "softwareudvikler", "Angular", "TypeScript",
            "JavaScript", "web", "UI", "praktisk", "teknisk support", "automation"
        ],
        "negative_terms": [
            "SAFe", "PI planning", "program increment", "burndown", "burn-up",
            "daily scrum", "certificeret Scrum Master", "rockstar", "ninja"
        ],
        "hard_reject_terms": ["ulønnet praktik", "commission only"],
        "minimum_score": 45,
        "application_style": "Kort, varm og konkret dansk ansøgning på cirka to afsnit. Ingen overdreven begejstring og ingen opfundne fakta.",
    },
    "ai": {
        "provider": "template",
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        "openai_url": os.getenv("OPENAI_URL", "https://api.openai.com/v1"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
        "temperature": 0.2,
        "max_output_tokens": 1600
    },
    "files": {
        "default_cv": "",
        "application_output": "data/generated"
    }
}

DEFAULT_SEARCHES = {
    "searches": [
        {
            "name": "Jobnet – indsæt din gemte søgning",
            "source": "jobnet",
            "url": "",
            "enabled": False,
            "link_patterns": ["/find-job/", "/CV/FindWork/", "jobannonce"],
            "incremental": {
                "enabled": True,
                "checkpoint": {"kind": "published_at", "value": "", "committed_at": ""}
            }
        },
        {
            "name": "Jobindex – indsæt din gemte søgning",
            "source": "jobindex",
            "url": "",
            "enabled": False,
            "link_patterns": ["/vis-job/", "/jobannonce/", "/job/", "/c?t="],
            "incremental": {
                "enabled": False,
                "checkpoint": {"kind": "", "value": "", "committed_at": ""}
            }
        }
    ],
    "scan": {
        "max_results_per_search": 500,
        "max_links_per_search": 80,
        "max_load_more_clicks": 70,
        "candidate_min_score": 10,
        "minimum_score_to_store": 10,
        "delay_seconds": 1.5,
        "headless": True
    }
}


def normalize_searches_config(data: dict[str, Any]) -> dict[str, Any]:
    """Add source-neutral incremental scan checkpoints to old configurations.

    A checkpoint is deliberately generic. Source adapters decide whether the value
    represents a publication timestamp, opaque cursor, advert id, or something else.
    Jobnet currently supports ``published_at``; Jobindex remains disabled until its
    pagination/date semantics are implemented safely.
    """
    changed = False
    searches = data.setdefault("searches", [])
    for search in searches:
        source = str(search.get("source") or "").casefold()
        incremental = search.get("incremental")
        if not isinstance(incremental, dict):
            incremental = {}
            search["incremental"] = incremental
            changed = True
        if "enabled" not in incremental:
            incremental["enabled"] = source == "jobnet"
            changed = True
        checkpoint = incremental.get("checkpoint")
        if not isinstance(checkpoint, dict):
            checkpoint = {}
            incremental["checkpoint"] = checkpoint
            changed = True
        if source == "jobindex":
            patterns = search.get("link_patterns")
            if not isinstance(patterns, list):
                patterns = []
            required_patterns = ["/vis-job/", "/jobannonce/", "/job/", "/c?t="]
            merged_patterns = list(dict.fromkeys([*patterns, *required_patterns]))
            if merged_patterns != patterns:
                search["link_patterns"] = merged_patterns
                changed = True
        default_kind = "published_at" if source == "jobnet" else ""
        for key, value in (("kind", default_kind), ("value", ""), ("committed_at", "")):
            if key not in checkpoint:
                checkpoint[key] = value
                changed = True
    if "scan" not in data or not isinstance(data.get("scan"), dict):
        data["scan"] = dict(DEFAULT_SEARCHES["scan"])
        changed = True
    data["_normalized"] = changed
    return data


def ensure_data_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    GENERATED_DIR.mkdir(exist_ok=True)
    CV_DIR.mkdir(exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(exist_ok=True)
    if not PROFILE_PATH.exists():
        initial_profile = (
            json.loads(PROFILE_EXAMPLE_PATH.read_text(encoding="utf-8"))
            if PROFILE_EXAMPLE_PATH.exists()
            else DEFAULT_PROFILE
        )
        PROFILE_PATH.write_text(
            json.dumps(initial_profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if not SEARCHES_PATH.exists():
        initial_searches = (
            json.loads(SEARCHES_EXAMPLE_PATH.read_text(encoding="utf-8"))
            if SEARCHES_EXAMPLE_PATH.exists()
            else DEFAULT_SEARCHES
        )
        SEARCHES_PATH.write_text(
            json.dumps(initial_searches, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if not SCORING_PATH.exists():
        SCORING_PATH.write_text(SCORING_DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    ensure_data_files()
    data = json.loads(path.read_text(encoding="utf-8"))
    if path == SEARCHES_PATH:
        data = normalize_searches_config(data)
        changed = bool(data.pop("_normalized", False))
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
