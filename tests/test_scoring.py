from jobagent.config import DEFAULT_PROFILE
from jobagent.scoring import score_job


def test_good_frontend_job_scores_above_threshold():
    job = {
        "title": "Frontendudvikler",
        "employer": "Acme",
        "location": "København",
        "description": "Angular TypeScript RxJS og brugergrænseflader",
    }
    result = score_job(job, DEFAULT_PROFILE)
    assert result.score >= 60
    assert result.details["recommendation"] in {"Muligt match", "Stærkt match"}
    assert result.details["positive_reasons"]


def test_agile_ceremony_terms_are_penalized():
    good = {"title":"Frontendudvikler", "employer":"Acme", "location":"København", "description":"Angular TypeScript"}
    bad = {**good, "description":"Angular TypeScript SAFe PI planning daily scrum burndown"}
    assert score_job(bad, DEFAULT_PROFILE).score < score_job(good, DEFAULT_PROFILE).score


def test_unrelated_health_role_is_not_recommended_by_location_alone():
    job = {
        "title": "Social- og sundhedsassistent til fast dagvagt",
        "employer": "Københavns Kommune",
        "location": "København",
        "description": "Et praktisk arbejde i København med borgerkontakt.",
    }
    result = score_job(job, DEFAULT_PROFILE)
    assert result.score < DEFAULT_PROFILE["preferences"]["minimum_score"]
    assert result.details["recommendation"] == "Svagt match"
    assert any("Andet fagområde" in reason["label"] for reason in result.details["negative_reasons"])


def test_adjacent_av_role_is_explained_but_not_overrated():
    job = {
        "title": "Erfaren AV-tekniker",
        "employer": "Københavns Kommune",
        "location": "København",
        "description": "Praktisk fejlfinding og teknisk support af AV-udstyr.",
    }
    result = score_job(job, DEFAULT_PROFILE)
    assert 20 <= result.score < 70
    assert any("Nært beslægtet rolle" in reason["label"] for reason in result.details["positive_reasons"])


def test_statens_it_consultant_survives_prefilter_and_is_a_possible_match():
    job = {
        "title": "Bliv it-konsulent hos Statens It og vær med til at drive og udvikle centrale it-services",
        "employer": "Statens It",
        "location": "Ballerup",
        "description": (
            "Team Kundeapplikationer arbejder med drift, videreudvikling og automatisering. "
            "Du installerer applikationer, releases og patches på Windows og Linux, arbejder "
            "med scripting i PowerShell, Python, Perl og SQL og motiveres af teknisk "
            "nysgerrighed og vedholdende problemløsning i komplekse driftsmiljøer."
        ),
    }
    result = score_job(job, DEFAULT_PROFILE)
    assert result.score >= DEFAULT_PROFILE["preferences"]["minimum_score"]
    assert result.score >= 10  # scannerens kandidatgrænse
    assert result.details["recommendation"] in {"Muligt match", "Stærkt match"}
    assert any("it konsulent" in reason["label"] for reason in result.details["positive_reasons"])
    assert result.details["technical_context"]
