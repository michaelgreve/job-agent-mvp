from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rapidfuzz.fuzz import ratio, token_set_ratio

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "trk", "trackingid"
}


def normalize_text(value: str | None) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9æøå]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.casefold() not in TRACKING_KEYS and not k.casefold().startswith("utm_")]
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunparse((parsed.scheme.casefold() or "https", parsed.netloc.casefold(), path, "", urlencode(query), ""))


def content_hash(description: str) -> str:
    cleaned = normalize_text(description)
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest() if cleaned else ""


def fingerprint(title: str, employer: str | None, location: str | None, source_job_id: str | None = None) -> str:
    parts = [normalize_text(employer), normalize_text(title), normalize_text(location), normalize_text(source_job_id)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def compare_jobs(a: dict, b: dict) -> tuple[float, str]:
    if a.get("canonical_url") and a.get("canonical_url") == b.get("canonical_url"):
        return 1.0, "samme kanoniske URL"
    if a.get("source_job_id") and a.get("source_job_id") == b.get("source_job_id") and a.get("source") == b.get("source"):
        return 1.0, "samme annonce-ID"
    if a.get("content_hash") and a.get("content_hash") == b.get("content_hash"):
        return 0.99, "identisk annoncetekst"

    employer = ratio(normalize_text(a.get("employer")), normalize_text(b.get("employer"))) / 100
    title = token_set_ratio(normalize_text(a.get("title")), normalize_text(b.get("title"))) / 100
    location = ratio(normalize_text(a.get("location")), normalize_text(b.get("location"))) / 100
    description = token_set_ratio(
        normalize_text(a.get("description", ""))[:6000],
        normalize_text(b.get("description", ""))[:6000],
    ) / 100

    weighted = employer * 0.30 + title * 0.35 + description * 0.30 + location * 0.05
    reason = json.dumps({
        "arbejdsgiver": round(employer, 2), "titel": round(title, 2),
        "tekst": round(description, 2), "sted": round(location, 2)
    }, ensure_ascii=False)
    return weighted, reason
