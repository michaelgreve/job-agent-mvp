from __future__ import annotations

import json
from typing import Any

from .ai import GeneratedApplication
from .dedupe import normalize_text


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def build_generation_context(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    career = profile.get("career", {})
    prefs = profile.get("preferences", {})
    person = profile.get("person", {})
    score_details = _json_value(job.get("score_details") or {})
    if not isinstance(score_details, dict):
        score_details = {}

    return {
        "candidate": {
            "name": person.get("full_name", ""),
            "preferred_name": person.get("preferred_name", ""),
            "headline": career.get("headline", ""),
            "summary": career.get("summary", ""),
            "years_professional": career.get("years_professional"),
            "skills": career.get("skills", []),
            "currently_learning": career.get("learning", []),
            "experience_highlights": career.get("experience_highlights", []),
            "education": career.get("education", []),
            "languages": career.get("languages", []),
            "drivers_license": career.get("drivers_license", ""),
            "selected_cv": profile.get("files", {}).get("default_cv", ""),
        },
        "writing_preferences": {
            "style": prefs.get("application_style", ""),
            "length": "Omkring to afsnit plus en kort afslutning.",
            "language": "Dansk",
            "avoid": [
                "Opdigtede fakta eller kompetencer",
                "Overdreven begejstring",
                "Floskler som drømmejob, passioneret eller brænder for",
                "At omtale mangler eller score",
                "Overskrift, adresseblok eller emnelinje",
            ],
        },
        "job": {
            "title": job.get("title", ""),
            "employer": job.get("employer", ""),
            "location": job.get("location", ""),
            "source": job.get("source", ""),
            "description": str(job.get("description") or "")[:18000],
        },
        "matching": {
            "score": job.get("score"),
            "recommendation": score_details.get("recommendation", ""),
            "summary": score_details.get("summary", ""),
            "positive_reasons": score_details.get("positive_reasons", []),
            "negative_reasons": score_details.get("negative_reasons", []),
        },
    }


def build_prompt(job: dict[str, Any], profile: dict[str, Any]) -> str:
    context = build_generation_context(job, profile)
    return f"""Skriv en færdig, målrettet dansk jobansøgning ud fra oplysningerne nedenfor.

KRAV TIL RESULTATET:
- Ansøgningen skal være omkring to afsnit plus en kort afslutning og underskrift.
- Tonen skal være varm, voksen, direkte og konkret.
- Udvælg højst 2-3 faktiske erfaringer eller kompetencer, som er mest relevante for netop denne annonce.
- Knyt dem tydeligt til arbejdsgiverens konkrete opgaver og behov; genfortæl ikke hele CV'et.
- Brug kun fakta fra kandidatprofilen. “Currently learning” må ikke beskrives som erhvervserfaring.
- Opfind ikke motivation, resultater, teknologi, uddannelse eller personlige egenskaber.
- Nævn ikke jobscore, mangler, arbejdsløshed eller hvordan jobbet blev fundet.
- Undgå ordene “brænder”, “drømmejob”, “passioneret” og generiske rekrutteringsfloskler.
- Ingen overskrift, adresseblok eller emnelinje.
- Returnér et JSON-objekt med præcis disse felter:
  application: den færdige ansøgningstekst
  facts_used: en kort liste over de konkrete profilfakta, teksten bygger på
  warnings: en liste over eventuelle forhold, der bør kontrolleres; ellers en tom liste

ANSØGNINGSPAKKE:
{json.dumps(context, ensure_ascii=False, indent=2)}""".strip()


def template_draft(job: dict[str, Any], profile: dict[str, Any]) -> GeneratedApplication:
    person = profile.get("person", {})
    name = person.get("preferred_name") or person.get("full_name") or ""
    career = profile.get("career", {})
    title = job.get("title") or "stillingen"
    employer = job.get("employer") or "jer"
    text_words = set(normalize_text(f"{job.get('title', '')} {job.get('description', '')}").split())

    ranked: list[tuple[int, str]] = []
    for highlight in career.get("experience_highlights", []):
        words = set(normalize_text(highlight).split())
        ranked.append((len(words.intersection(text_words)), highlight))
    chosen = [h for points, h in sorted(ranked, key=lambda item: item[0], reverse=True) if points > 0][:2]
    if not chosen:
        chosen = list(career.get("experience_highlights", []))[:1]
    matched_skills = [
        skill for skill in career.get("skills", [])
        if normalize_text(str(skill)) in normalize_text(f"{job.get('title', '')} {job.get('description', '')}")
    ][:4]
    facts_used = chosen or ([f"Kompetencer: {', '.join(matched_skills)}"] if matched_skills else [])
    experience = " ".join(chosen)
    years = career.get("years_professional")
    years_text = f"Jeg har {years} års professionel erfaring" if isinstance(years, int) and years > 0 else "Jeg har relevant teknisk erfaring"
    skills_text = f" med blandt andet {', '.join(matched_skills)}" if matched_skills else ""
    signature = f"\n{name}" if name else ""

    body = (
        f"Jeg søger stillingen som {title} hos {employer}, fordi opgaverne ligger tæt på det, "
        f"jeg arbejder med som frontend- og webudvikler. {years_text}{skills_text}, "
        f"og lægger vægt på at gøre komplekse løsninger enkle, stabile og brugbare.\n\n"
        f"{experience} Jeg vil derfor kunne bidrage med solid teknisk erfaring, et roligt blik for helheden "
        f"og en praktisk tilgang til at få opgaverne helt i mål.\n\n"
        f"Jeg ser frem til muligheden for at uddybe, hvordan jeg kan bidrage hos jer.\n\n"
        f"Venlig hilsen{signature}"
    )
    return GeneratedApplication(
        application=body,
        facts_used=facts_used,
        warnings=["Skabelontilstand bruger kun simpel nøgleordsmatching og er ikke en egentlig AI-vurdering af annoncen."],
        provider="template",
        model="",
    )
