from __future__ import annotations

import asyncio

import pytest

from jobagent.ai import (
    AIError,
    ClipboardProvider,
    GeneratedApplication,
    TemplateProvider,
    _parse_generated,
    get_provider,
)
from jobagent.config import DEFAULT_PROFILE
from jobagent.drafting import build_prompt, template_draft


JOB = {
    "title": "Frontendudvikler",
    "employer": "Eksempel A/S",
    "location": "København",
    "description": "Vi søger en Angular-udvikler til TypeScript, RxJS og brugbare grænseflader.",
    "score": 78,
    "qualification_score": 91,
    "direction_score": 65,
    "curiosity_score": 72,
    "score_details": {
        "recommendation": "Stærkt match",
        "matched_career_tracks": [{"id": "frontend_software", "label": "Frontend / software"}],
        "positive_reasons": [{"label": "Angular", "points": 20}],
    },
}


def test_structured_result_is_parsed_from_json_fence():
    result = _parse_generated(
        '```json\n{"application":"Hej verden","facts_used":["Angular"],"warnings":[]}\n```',
        "ollama",
        "test-model",
    )
    assert result.application == "Hej verden"
    assert result.facts_used == ["Angular"]
    assert result.provider == "ollama"


def test_plain_text_falls_back_with_visible_warning():
    result = _parse_generated("En helt almindelig ansøgningstekst.", "ollama", "small")
    assert result.application.startswith("En helt")
    assert result.warnings


def test_clipboard_provider_returns_complete_handoff():
    prompt = build_prompt(JOB, DEFAULT_PROFILE)
    result = asyncio.run(ClipboardProvider({}).generate(prompt))
    assert result.requires_external_response is True
    assert "Frontendudvikler" in result.handoff_prompt
    assert "Returnér kun den færdige ansøgningstekst" in result.handoff_prompt


def test_template_provider_returns_prepared_result():
    prepared = template_draft(JOB, DEFAULT_PROFILE)
    result = asyncio.run(TemplateProvider({}).generate("ignored", template_result=prepared))
    assert result.provider == "template"
    assert "Frontendudvikler" in result.application
    assert result.facts_used


def test_unknown_provider_is_rejected():
    with pytest.raises(AIError):
        get_provider("sacrificial-chicken", {})


def test_prompt_contains_job_profile_and_factual_boundaries():
    prompt = build_prompt(JOB, DEFAULT_PROFILE)
    assert "Eksempel A/S" in prompt
    assert "Angular" in prompt
    assert "Currently learning" in prompt
    assert "Opfind ikke" in prompt
    assert '"overall_score": 78' in prompt
    assert '"qualification_score": 91' in prompt
    assert '"direction_score": 65' in prompt
    assert '"curiosity_score": 72' in prompt
    assert '"matched_career_tracks"' in prompt
    assert "Cykelværksted og reparation gennem Nytteindsats" in prompt
    assert "Nævn ikke jobscore" in prompt


def test_generated_application_defaults_lists_safely():
    a = GeneratedApplication(application="A", provider="x")
    b = GeneratedApplication(application="B", provider="y")
    a.warnings.append("x")
    assert b.warnings == []
