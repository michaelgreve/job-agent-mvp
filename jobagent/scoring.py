from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .dedupe import normalize_text


SCORING_CONFIG_PATH = Path(__file__).with_name("scoring.v2.json")
USER_SCORING_CONFIG_PATH = SCORING_CONFIG_PATH.parents[1] / "data" / "scoring.json"
DIMENSIONS = ("qualification", "direction", "curiosity")


@dataclass
class ScoreResult:
    score: int
    details: dict[str, Any]
    hard_reject: bool = False

    def dimension(self, name: str) -> int:
        return int(self.details.get(f"{name}_score", 0))


@lru_cache(maxsize=1)
def scoring_config() -> dict[str, Any]:
    path = USER_SCORING_CONFIG_PATH if USER_SCORING_CONFIG_PATH.exists() else SCORING_CONFIG_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def validate_scoring_config(config: dict[str, Any]) -> None:
    if not isinstance(config.get("version"), int) or config["version"] < 1:
        raise ValueError("Scoring version skal være et positivt heltal.")
    formula = config.get("overall_score", {}).get("formula")
    if not isinstance(formula, dict):
        raise ValueError("overall_score.formula skal være et objekt.")
    weights = [formula.get(name) for name in DIMENSIONS]
    if any(not isinstance(value, (int, float)) or value < 0 for value in weights):
        raise ValueError("Alle tre vægte skal være tal på mindst 0.")
    if abs(sum(weights) - 1.0) > 0.001:
        raise ValueError("Vægtene for Qualification, Direction og Curiosity skal tilsammen være 1.")
    for key in ("career_tracks", "activity_signals", "penalties", "recommendations"):
        if not isinstance(config.get(key), list):
            raise ValueError(f"{key} skal være en liste.")
    rules = config.get("retention_rules", {}).get("store_if_any")
    if not isinstance(rules, list) or not rules:
        raise ValueError("retention_rules.store_if_any skal være en liste med mindst én regel.")
    allowed = {"overall", *DIMENSIONS}
    if any(rule.get("metric") not in allowed or not isinstance(rule.get("gte"), (int, float)) for rule in rules):
        raise ValueError("Hver fastholdelsesregel skal have en kendt metric og en numerisk gte-grænse.")


def _contains(haystack: str, phrase: str) -> bool:
    needle = normalize_text(phrase)
    return bool(needle and f" {needle} " in f" {haystack} ")


def _hits(terms: Iterable[str], haystack: str) -> list[str]:
    return [term for term in sorted(set(terms), key=lambda value: (-len(value), value)) if _contains(haystack, term)]


def _profile_match(value: str, haystack: str) -> bool:
    return any(
        len(normalize_text(part)) >= 2 and _contains(haystack, part)
        for part in [value, *re.split(r"[/,;()]+", value)]
    )


def _evidence_hits(items: Iterable[Any], haystack: str) -> list[str]:
    hits: list[str] = []
    for item in items:
        if isinstance(item, dict):
            terms = [str(term) for term in item.get("terms", [])]
            if any(_profile_match(term, haystack) for term in terms):
                hits.append(str(item.get("label") or ", ".join(terms)))
        elif _profile_match(str(item), haystack):
            hits.append(str(item))
    return hits


def _clamp(value: float, config: dict[str, Any]) -> int:
    bounds = config.get("score_range", {})
    return max(int(bounds.get("min", 0)), min(int(bounds.get("max", 100)), round(value)))


def _recommendation(score: int, config: dict[str, Any]) -> str:
    ordered = sorted(config.get("recommendations", []), key=lambda item: int(item.get("min", 0)), reverse=True)
    return next((str(item["label"]) for item in ordered if score >= int(item.get("min", 0))), "Lav prioritet")


def should_retain(result: ScoreResult, config: dict[str, Any] | None = None) -> bool:
    if result.hard_reject:
        return False
    config = config or scoring_config()
    values = {"overall": result.score, **{name: result.dimension(name) for name in DIMENSIONS}}
    return any(
        values.get(str(rule.get("metric")), 0) >= int(rule.get("gte", 0))
        for rule in config.get("retention_rules", {}).get("store_if_any", [])
    )


def is_prefilter_candidate(result: ScoreResult, minimum_overall: int = 10) -> bool:
    if result.hard_reject:
        return False
    target_title = any(
        track.get("title_matches") and track.get("priority") in {"primary", "secondary"}
        for track in result.details.get("matched_career_tracks", [])
    )
    return target_title or result.score >= minimum_overall or result.dimension("direction") >= 35 or result.dimension("curiosity") >= 40


def _add(
    scores: dict[str, float],
    reasons: dict[str, list[dict[str, Any]]],
    points: dict[str, Any],
    label: str,
    source: str,
    multiplier: float = 1.0,
) -> None:
    for dimension in DIMENSIONS:
        amount = float(points.get(dimension, 0)) * multiplier
        scores[dimension] += amount
        if amount:
            reasons[dimension].append({"label": label, "points": round(amount), "source": source})


def _requirement_applies(rule: dict[str, Any], haystack: str, profile_text: str) -> tuple[bool, list[str]]:
    hits = _hits(rule.get("requirement_terms", []), haystack)
    if not hits or _hits(rule.get("ignore_terms", []), haystack):
        return False, []
    return not any(_contains(profile_text, hit) for hit in hits), hits


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> ScoreResult:
    config = scoring_config()
    title = normalize_text(str(job.get("title") or ""))
    haystack = normalize_text(" ".join(str(job.get(key) or "") for key in ("title", "employer", "location", "description")))
    profile_text = normalize_text(json.dumps(profile, ensure_ascii=False))
    baselines = config.get("dimension_baselines", {})
    scores = {name: float(baselines.get(name, 0)) for name in DIMENSIONS}
    reasons: dict[str, list[dict[str, Any]]] = {name: [] for name in DIMENSIONS}
    tracks: list[dict[str, Any]] = []
    track_ids: set[str] = set()
    signal_ids: set[str] = set()

    hard_hits = _hits(profile.get("preferences", {}).get("hard_reject_terms", []), haystack)
    if hard_hits:
        details = {
            "scoring_version": int(config.get("version", 2)),
            "qualification_score": 0, "direction_score": 0, "curiosity_score": 0,
            "matched_career_tracks": [], "positive_reasons": [],
            "negative_reasons": [{"dimension": "overall", "label": f"Direkte fravalg: {', '.join(hard_hits)}", "points": -100}],
            "reasons_by_dimension": reasons, "recommendation": _recommendation(0, config),
            "summary": "Jobbet rammer et direkte fravalg i profilens indstillinger.",
            "retained": False, "hard_reject": hard_hits,
        }
        return ScoreResult(0, details, True)

    title_multiplier = float(config.get("matching", {}).get("title_multiplier", 1.0))
    for track in config.get("career_tracks", []):
        title_hits = _hits(track.get("title_terms", []), title)
        keyword_hits = _hits(track.get("keyword_terms", []), haystack)
        if not title_hits and not keyword_hits:
            continue
        track_id = str(track.get("id") or "")
        track_ids.add(track_id)
        tracks.append({
            "id": track_id, "label": track.get("label", track_id), "priority": track.get("priority", ""),
            "title_matches": title_hits, "keyword_matches": keyword_hits,
        })
        if title_hits:
            _add(scores, reasons, track.get("title_points", {}), f"Målretning i titlen: {track.get('label')}", track_id, title_multiplier)
        for dimension in DIMENSIONS:
            per_hit = float(track.get("keyword_points_per_match", {}).get(dimension, 0))
            amount = min(float(track.get("keyword_caps", {}).get(dimension, 0)), len(keyword_hits) * per_hit)
            _add(scores, reasons, {dimension: amount}, f"{track.get('label')}: {', '.join(keyword_hits)}", track_id)

    career = profile.get("career", {})
    overlap = config.get("profile_overlap", {})
    professional = [str(skill) for skill in career.get("skills", []) if _profile_match(str(skill), haystack)]
    rule = overlap.get("professional_skill", {})
    amount = min(float(rule.get("qualification_cap", 0)), len(professional) * float(rule.get("qualification_per_distinct_match", 0)))
    _add(scores, reasons, {"qualification": amount}, f"Dokumenterede kompetencer: {', '.join(professional)}", "professional_skill")

    learning = [str(item) for item in career.get("learning", []) if _profile_match(str(item), haystack)]
    rule = overlap.get("currently_learning", {})
    learning_points = {
        dimension: min(float(rule.get(f"{dimension}_cap", 0)), len(learning) * float(rule.get(f"{dimension}_per_distinct_match", 0)))
        for dimension in ("direction", "curiosity")
    }
    _add(scores, reasons, learning_points, f"Matcher noget du er ved at lære: {', '.join(learning)}", "currently_learning")

    interests = [
        str(item) for item in career.get("direction", {}).get("interests", [])
        if _profile_match(str(item), haystack)
    ]
    rule = overlap.get("career_interest", {})
    interest_points = {
        dimension: min(float(rule.get(f"{dimension}_cap", 0)), len(interests) * float(rule.get(f"{dimension}_per_distinct_match", 0)))
        for dimension in ("direction", "curiosity")
    }
    _add(scores, reasons, interest_points, f"Matcher din ønskede karriereretning: {', '.join(interests)}", "career_interest")

    evidence = career.get("evidence", {})
    evidence_rules = (
        ("practical", {"workshop_mechanical", "technical_operations_support", "facilities_property"}, 4, 12),
        ("creative_people", {"event_experience_stage"}, 5, 15),
    )
    for key, relevant_tracks, points_each, cap in evidence_rules:
        if not track_ids.intersection(relevant_tracks):
            continue
        matches = _evidence_hits(evidence.get(key, []), haystack)
        _add(scores, reasons, {"qualification": min(cap, len(matches) * points_each)}, f"Relevant faktuel erfaring: {', '.join(matches)}", f"profile_evidence_{key}")

    for group in ("activity_signals", "environment_signals"):
        for signal in config.get(group, []):
            hits = _hits(signal.get("terms", []), haystack)
            if hits:
                signal_id = str(signal.get("id") or "")
                signal_ids.add(signal_id)
                _add(scores, reasons, signal.get("points", {}), f"{signal.get('label')}: {', '.join(hits)}", signal_id)

    for rule in config.get("interaction_rules", []):
        groups_ok = not rule.get("requires_any_from_groups") or bool(track_ids.intersection(rule["requires_any_from_groups"]))
        signal_ok = not rule.get("requires_signal") or rule["requires_signal"] in signal_ids
        signals_ok = all(signal in signal_ids for signal in rule.get("requires_signals", []))
        if groups_ok and signal_ok and signals_ok:
            _add(scores, reasons, rule.get("points", {}), str(rule.get("label") or rule.get("id")), str(rule.get("id")))

    for signal in config.get("curiosity_signals", []):
        hits = _hits(signal.get("terms", []), haystack)
        if hits:
            _add(scores, reasons, {"curiosity": signal.get("points", 0)}, f"{signal.get('label')}: {', '.join(hits)}", str(signal.get("id")))

    for rule in config.get("penalties", []):
        conditional = rule.get("conditional")
        hits: list[str] = []
        if conditional:
            applies = conditional.get("matched_group") in track_ids and not signal_ids.intersection(conditional.get("none_of_signals", []))
        elif rule.get("requirement_terms"):
            applies, hits = _requirement_applies(rule, haystack, profile_text)
        else:
            hits = _hits(rule.get("terms", []), haystack)
            applies = bool(hits)
        if applies:
            suffix = f": {', '.join(hits)}" if hits else ""
            _add(scores, reasons, rule.get("points", {}), f"{rule.get('label')}{suffix}", str(rule.get("id")))

    unrelated = config.get("unrelated_professions", {})
    unrelated_hits = _hits(unrelated.get("terms", []), title)
    target_title = any(track["title_matches"] and track["priority"] in {"primary", "secondary"} for track in tracks)
    hidden = bool(unrelated.get("enabled") and unrelated_hits and unrelated.get("hide_when_no_target_track_matches") and not target_title)
    if unrelated_hits:
        _add(scores, reasons, unrelated.get("points", {}), f"Andet fagområde i titlen: {', '.join(unrelated_hits)}", "unrelated_profession")

    final = {name: _clamp(value, config) for name, value in scores.items()}
    formula = config.get("overall_score", {}).get("formula", {})
    overall = _clamp(sum(final[name] * float(formula.get(name, 0)) for name in DIMENSIONS), config)
    details = {
        "scoring_version": int(config.get("version", 2)),
        **{f"{name}_score": final[name] for name in DIMENSIONS},
        "matched_career_tracks": tracks, "matched_signals": sorted(signal_ids),
        "professional_skill_matches": professional, "currently_learning_matches": learning,
        "positive_reasons": [{"dimension": name, **item} for name in DIMENSIONS for item in reasons[name] if item["points"] > 0],
        "negative_reasons": [{"dimension": name, **item} for name in DIMENSIONS for item in reasons[name] if item["points"] < 0],
        "reasons_by_dimension": reasons, "recommendation": _recommendation(overall, config),
        "summary": "Samlet score vægter kvalifikation 30 %, retning 50 % og nysgerrighed 20 %.",
        "unrelated_profession": unrelated_hits,
    }
    result = ScoreResult(overall, details, hidden)
    details["retained"] = should_retain(result, config)
    if hidden:
        details["summary"] = "Jobbet tilhører et andet fagområde uden et tydeligt målspor."
    return result
