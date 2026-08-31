from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dedupe import normalize_text


@dataclass
class ScoreResult:
    score: int
    details: dict[str, Any]
    hard_reject: bool = False


# These are deliberately conservative. A job should not look attractive merely because
# it is nearby and contains the word “praktisk”. Users can still inspect every result.
PRIMARY_TITLE_TERMS: dict[str, int] = {
    "frontend": 30,
    "front end": 30,
    "webudvikler": 30,
    "web developer": 30,
    "softwareudvikler": 28,
    "software developer": 28,
    "angular": 30,
    "typescript developer": 28,
    "javascript developer": 28,
    "ui developer": 26,
}

ADJACENT_TITLE_TERMS: dict[str, int] = {
    "it konsulent": 26,
    "it consultant": 26,
    "it support": 18,
    "it supporter": 18,
    "teknisk support": 18,
    "av tekniker": 20,
    "automation": 18,
    "automatik": 18,
    "plc": 18,
    "digitalisering": 12,
    "systemudvikler": 22,
    "driftskonsulent": 22,
    "applikationskonsulent": 22,
    "systemadministrator": 20,
    "devops": 22,
    "platform engineer": 22,
    "webmaster": 18,
    "ejendomsfunktionær": 14,
    "vicevært": 14,
    "regissør": 12,
}

# Roles that normally require a different professional qualification or career track.
# They are penalties, not hard rejections, so an unusual advert can still be reviewed.
ROLE_MISMATCH_TERMS: dict[str, int] = {
    "pædagog": 38,
    "pædagoger": 38,
    "paedagog": 38,
    "pædagogmedhjælper": 38,
    "social og sundhedsassistent": 42,
    "social og sundhedsassistenter": 42,
    "sosu": 42,
    "sygeplejerske": 42,
    "sygeplejersker": 42,
    "ergoterapeut": 42,
    "fysioterapeut": 42,
    "nattevagt": 35,
    "nattevagter": 35,
    "socialrådgiver": 38,
    "læge": 45,
    "tandlæge": 45,
    "jurist": 35,
    "advokat": 40,
    "lærer": 35,
    "lærere": 35,
    "elektriker": 28,
    "mekaniker": 24,
}

TECHNICAL_CONTEXT_GROUPS: dict[str, tuple[int, list[str]]] = {
    "Automatisering": (5, [
        "automatisering", "automatisere", "automatiske", "automatisk",
    ]),
    "Scripting og kode": (5, [
        "scripting", "powershell", "python", "perl", "pearl", "sql",
    ]),
    "Windows/Linux og open source": (4, [
        "windows", "linux", "opensource", "open source",
    ]),
    "Applikationsdrift": (4, [
        "applikationer", "releases", "patches", "driftsmiljø", "sikker drift",
        "certifikatløsning", "certifikater",
    ]),
    "Teknisk problemløsning": (4, [
        "teknisk nysgerrighed", "problemløsning", "komplekse driftsmiljøer",
        "stabile genbrugelige løsninger",
    ]),
}


SENIORITY_MISMATCH_TERMS: dict[str, int] = {
    "studentermedhjælper": 20,
    "student": 18,
    "praktikant": 22,
    "elev": 18,
    "direktør": 18,
    "director": 18,
    "kontorchef": 18,
    "afdelingschef": 18,
}


def _contains_phrase(haystack: str, phrase: str) -> bool:
    needle = normalize_text(phrase)
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def _hits(values: list[str], haystack: str) -> list[str]:
    return [value for value in values if _contains_phrase(haystack, value)]


def _technical_context_hits(haystack: str) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for label, (points, aliases) in TECHNICAL_CONTEXT_GROUPS.items():
        if any(_contains_phrase(haystack, alias) for alias in aliases):
            hits.append((label, points))
    return hits


def _best_weighted_hit(title: str, terms: dict[str, int]) -> tuple[str, int] | None:
    # Job titles commonly join words (frontendudvikler, nattevagter, AV-tekniker),
    # so title-family matching intentionally allows normalized substrings.
    hits = [
        (term, points) for term, points in terms.items()
        if normalize_text(term) and normalize_text(term) in title
    ]
    return max(hits, key=lambda item: item[1], default=None)


def _recommendation(score: int, minimum: int) -> tuple[str, str]:
    if score >= 70:
        return "Stærkt match", "Rollen ligger tæt på din dokumenterede erfaring og dine ønskede jobtyper."
    if score >= minimum:
        return "Muligt match", "Der er relevante overlap, men annoncen bør vurderes manuelt."
    return "Svagt match", "Jobbet blev fundet af portalsøgningen, men profilen giver ikke grundlag for at anbefale det."


def score_job(job: dict, profile: dict) -> ScoreResult:
    prefs = profile.get("preferences", {})
    career = profile.get("career", {})
    title = normalize_text(job.get("title", ""))
    description = normalize_text(job.get("description", ""))
    employer = normalize_text(job.get("employer", ""))
    location_text = normalize_text(f"{job.get('location', '')} {job.get('description', '')}")
    haystack = normalize_text(" ".join([
        job.get("title", ""), job.get("employer", ""), job.get("location", ""), job.get("description", "")
    ]))

    hard_hits = _hits(prefs.get("hard_reject_terms", []), haystack)
    minimum = int(prefs.get("minimum_score", 45))
    if hard_hits:
        label, summary = _recommendation(0, minimum)
        return ScoreResult(0, {
            "hard_reject": hard_hits,
            "recommendation": label,
            "summary": summary,
            "positive_reasons": [],
            "negative_reasons": [
                {"label": f"Direkte fravalg: {', '.join(hard_hits)}", "points": -100}
            ],
            "skills": [],
            "preferred": [],
            "negative": [],
            "location": [],
        }, True)

    skill_hits = _hits(career.get("skills", []), haystack)
    preferred_hits = _hits(prefs.get("preferred_terms", []), haystack)
    negative_hits = _hits(prefs.get("negative_terms", []), haystack)
    location_hits = _hits(prefs.get("locations", []), location_text)

    positive_reasons: list[dict[str, Any]] = []
    negative_reasons: list[dict[str, Any]] = []
    score = 5

    primary_hit = _best_weighted_hit(title, PRIMARY_TITLE_TERMS)
    adjacent_hit = None if primary_hit else _best_weighted_hit(title, ADJACENT_TITLE_TERMS)
    if primary_hit:
        term, points = primary_hit
        score += points
        positive_reasons.append({"label": f"Målrolle i titlen: {term}", "points": points})
    elif adjacent_hit:
        term, points = adjacent_hit
        score += points
        positive_reasons.append({"label": f"Nært beslægtet rolle i titlen: {term}", "points": points})

    if skill_hits:
        points = min(30, len(skill_hits) * 5)
        score += points
        positive_reasons.append({"label": f"Kompetencetræf: {', '.join(skill_hits)}", "points": points})

    if preferred_hits:
        points = min(15, len(preferred_hits) * 3)
        score += points
        positive_reasons.append({"label": f"Foretrukne ord: {', '.join(preferred_hits)}", "points": points})

    technical_context_hits = _technical_context_hits(haystack)
    # Broad infrastructure words should only count when the title is already technical,
    # or when several independent technical themes occur together. This keeps a stray
    # mention of Windows or SQL from rescuing an unrelated role.
    if technical_context_hits and (primary_hit or adjacent_hit or len(technical_context_hits) >= 2):
        points = min(20, sum(points for _, points in technical_context_hits))
        score += points
        positive_reasons.append({
            "label": f"Teknisk overlap: {', '.join(label for label, _ in technical_context_hits)}",
            "points": points,
        })

    if location_hits:
        points = min(6, len(location_hits) * 3)
        score += points
        positive_reasons.append({"label": f"Geografi: {', '.join(location_hits)}", "points": points})

    mismatch_hit = _best_weighted_hit(title, ROLE_MISMATCH_TERMS)
    if mismatch_hit:
        term, points = mismatch_hit
        score -= points
        negative_reasons.append({"label": f"Andet fagområde i titlen: {term}", "points": -points})

    seniority_hit = _best_weighted_hit(title, SENIORITY_MISMATCH_TERMS)
    if seniority_hit:
        term, points = seniority_hit
        score -= points
        negative_reasons.append({"label": f"Rolleniveau passer dårligt: {term}", "points": -points})

    if negative_hits:
        points = min(32, len(negative_hits) * 8)
        score -= points
        negative_reasons.append({"label": f"Advarselsord: {', '.join(negative_hits)}", "points": -points})

    # A generic hit such as location or “praktisk” must not make an unrelated role look
    # recommended. If no target/adjacent title and no technical skill appear, cap it low.
    if not primary_hit and not adjacent_hit and not skill_hits:
        score = min(score, 24)
        negative_reasons.append({
            "label": "Ingen tydelig målrolle eller dokumenteret kompetence i annoncen",
            "points": 0,
        })

    score = max(0, min(100, score))
    recommendation, summary = _recommendation(score, minimum)
    return ScoreResult(score, {
        "recommendation": recommendation,
        "summary": summary,
        "positive_reasons": positive_reasons,
        "negative_reasons": negative_reasons,
        "skills": skill_hits,
        "preferred": preferred_hits,
        "technical_context": [label for label, _ in technical_context_hits],
        "negative": negative_hits,
        "location": location_hits,
        "base": 5,
        "minimum_score": minimum,
        "title": title,
        "employer": employer,
        "description_length": len(description),
    })
