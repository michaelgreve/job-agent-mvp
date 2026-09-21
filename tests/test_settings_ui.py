"""Browser regressions; install Chromium with `python -m playwright install chromium`."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
import pytest

from jobagent import config, main


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Install Playwright Chromium to run settings browser tests")
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def settings(browser, monkeypatch, tmp_path):
    profile_path = tmp_path / "profile.json"
    searches_path = tmp_path / "searches.json"
    scoring_path = tmp_path / "scoring.json"
    profile = deepcopy(config.DEFAULT_PROFILE)
    profile["person"]["full_name"] = "Original name"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    searches_path.write_text(json.dumps(config.DEFAULT_SEARCHES), encoding="utf-8")
    scoring_path.write_text(json.dumps(main.scoring_config()), encoding="utf-8")
    monkeypatch.setattr(main, "PROFILE_PATH", profile_path)
    monkeypatch.setattr(main, "SEARCHES_PATH", searches_path)
    monkeypatch.setattr(main, "SCORING_PATH", scoring_path)
    monkeypatch.setattr(main, "CV_DIR", tmp_path / "cv")
    (tmp_path / "cv").mkdir()
    monkeypatch.setattr(config, "ensure_data_files", lambda: None)
    monkeypatch.setattr(main, "ensure_data_files", lambda: None)
    monkeypatch.setattr(main, "_rescore_all_jobs", lambda *args, **kwargs: None)
    requests = []
    client = TestClient(main.app)

    def respond(route):
        path = route.request.url.removeprefix("http://jobagent.test")
        if path == "/api/config":
            if route.request.method == "PUT":
                payload = route.request.post_data_json
                requests.append(payload)
                response = client.put(path, json=payload)
            else:
                response = client.get(path)
            route.fulfill(status=response.status_code, json=response.json())
        elif path == "/api/jobs":
            route.fulfill(json={"jobs": [], "counts": {}})
        elif path == "/api/jobs/1":
            details = {
                "qualification_score": 91, "direction_score": 42, "curiosity_score": 67,
                "recommendation": "Interessant",
                "summary": "Dimensionstest",
                "matched_career_tracks": [{"id": "frontend_software", "label": "Frontend / software"}],
                "reasons_by_dimension": {
                    "qualification": [{"label": "Angular", "points": 20}],
                    "direction": [{"label": "Ren software", "points": -10}],
                    "curiosity": [{"label": "IoT", "points": 15}],
                },
            }
            route.fulfill(json={"job": {
                "id": 1, "title": "A", "employer": "Test", "location": "København",
                "description": "Beskrivelse", "source": "manual", "status": "new", "score": 62,
                "qualification_score": 91, "direction_score": 42, "curiosity_score": 67,
                "score_details": json.dumps(details), "archived": 0,
            }, "drafts": [], "sources": []})
        elif path == "/api/search/scan/status":
            route.fulfill(json={"phase": "idle", "running": False})
        elif path.startswith("/static/"):
            route.fulfill(path=main.ROOT / "jobagent" / path[1:].split("?")[0])
        elif path == "/":
            route.fulfill(path=main.ROOT / "jobagent/templates/index.html", content_type="text/html")
        else:
            route.fulfill(status=404)

    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("http://jobagent.test/**", respond)
    page.goto("http://jobagent.test")
    page.locator("#settingsBtn").click()
    yield page, requests, profile_path, searches_path
    page.close()
    client.close()
    assert not errors


def open_json(page, selector):
    page.locator(selector).locator("..").locator("..").locator("summary").click()
    return page.locator(selector)


def save(page):
    page.locator('#settingsForm button[value="default"]').click()
    expect(page.locator("#settingsDialog")).not_to_be_visible()


def test_profile_json_persists_and_updates_fields(settings):
    page, requests, profile_path, _ = settings
    editor = open_json(page, "#profileJson")
    profile = json.loads(editor.input_value())
    profile["person"]["full_name"] = "New pasted name"
    profile["career"]["skills"] = ["Python", "TypeScript"]
    profile["ai"] = {"provider": "clipboard", "custom_option": "keep"}
    profile["files"] = {"default_cv": "new-cv.pdf"}
    profile["custom"] = {"notes": ["Preserve advanced values"]}
    editor.fill(json.dumps(profile))
    expect(page.locator("#profileName")).to_have_value("New pasted name")
    expect(page.locator("#profileSkills")).to_have_value("Python, TypeScript")
    save(page)
    assert requests[-1]["profile"] == profile
    assert json.loads(profile_path.read_text(encoding="utf-8")) == profile
    page.locator("#settingsBtn").click()
    assert json.loads(editor.input_value()) == profile
    page.locator("#profileName").fill("Edited after paste")
    assert json.loads(editor.input_value())["person"]["full_name"] == "Edited after paste"
    save(page)
    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["person"]["full_name"] == "Edited after paste"
    assert saved["custom"] == profile["custom"]
    assert saved["ai"]["custom_option"] == "keep"
    assert saved["files"]["default_cv"] == "new-cv.pdf"


def test_search_json_reorders_adds_and_removes_entries(settings):
    page, requests, _, searches_path = settings
    page.locator('[data-tab="searches"]').click()
    editor = open_json(page, "#searchesJson")
    searches = json.loads(editor.input_value())
    searches["searches"].reverse()
    searches["searches"].append({"source": "custom", "name": "Added", "url": "https://example.com/jobs", "enabled": True})
    searches["searches"][0].update(name="Pasted search", url="https://example.com/new", enabled=True)
    searches["scan"]["max_results_per_search"] = 123
    editor.fill(json.dumps(searches))
    expect(page.locator(".search-entry")).to_have_count(3)
    expect(page.locator('[data-search-name="0"]')).to_have_value("Pasted search")
    save(page)
    assert requests[-1]["searches"] == searches
    assert json.loads(searches_path.read_text(encoding="utf-8")) == searches
    page.locator("#settingsBtn").click()
    page.locator('[data-search-name="0"]').fill("")
    assert json.loads(editor.input_value())["searches"][0]["name"] == ""
    searches = json.loads(editor.input_value())
    searches["searches"] = searches["searches"][1:]
    editor.fill(json.dumps(searches))
    expect(page.locator(".search-entry")).to_have_count(2)
    save(page)
    assert json.loads(searches_path.read_text(encoding="utf-8")) == searches


@pytest.mark.parametrize("invalid", ["{", "null", "[]", '{"career":{"skills":"Python"}}'])
def test_invalid_json_stays_open_and_cancel_does_not_save(settings, invalid):
    page, requests, profile_path, _ = settings
    original = profile_path.read_text(encoding="utf-8")
    open_json(page, "#profileJson").fill(invalid)
    page.locator('#settingsForm button[value="default"]').click()
    expect(page.locator("#settingsDialog")).to_be_visible()
    expect(page.locator("#settingsError")).to_be_visible()
    assert not requests
    page.locator('#settingsForm .dialog-actions button[value="cancel"]').click()
    expect(page.locator("#settingsDialog")).not_to_be_visible()
    assert not requests
    assert profile_path.read_text(encoding="utf-8") == original


def test_checkpoint_reset_preserves_pasted_searches(settings):
    page, _, _, searches_path = settings
    page.locator('[data-tab="searches"]').click()
    editor = open_json(page, "#searchesJson")
    searches = json.loads(editor.input_value())
    searches["searches"] = [searches["searches"][0]]
    search = searches["searches"][0]
    search["name"] = "Imported"
    search["incremental"]["checkpoint"].update(value="2026-09-01T12:00:00Z", committed_at="2026-09-02")
    editor.fill(json.dumps(searches))
    page.locator('[data-reset-checkpoint="0"]').click()
    search["incremental"]["checkpoint"].update(value="", committed_at="")
    save(page)
    assert json.loads(searches_path.read_text(encoding="utf-8")) == searches


def test_form_edits_save_and_cancel_discards_changes(settings):
    page, requests, profile_path, searches_path = settings
    page.locator("#profileName").fill("Form edit")
    page.locator('[data-tab="searches"]').click()
    page.locator('[data-search-url="0"]').fill("https://example.com/search")
    page.locator('[data-search-enabled="0"]').check()
    save(page)
    assert json.loads(profile_path.read_text(encoding="utf-8"))["person"]["full_name"] == "Form edit"
    search = json.loads(searches_path.read_text(encoding="utf-8"))["searches"][0]
    assert search["url"] == "https://example.com/search"
    assert search["enabled"] is True
    page.locator("#settingsBtn").click()
    page.locator('[data-tab="profile"]').click()
    page.locator("#profileName").fill("Discard this")
    page.locator('#settingsForm .dialog-actions button[value="cancel"]').click()
    assert len(requests) == 1
    page.locator("#settingsBtn").click()
    expect(page.locator("#profileName")).to_have_value("Form edit")


def test_save_failure_is_visible_inside_dialog(settings):
    page, requests, _, _ = settings
    page.route("http://jobagent.test/api/config", lambda route: route.fulfill(status=500, json={"detail": "Could not save"}))
    page.locator("#profileName").fill("Unsaved edit")
    page.locator('#settingsForm button[value="default"]').click()
    expect(page.locator("#settingsError")).to_contain_text("Could not save")
    expect(page.locator("#settingsError")).to_be_visible()
    expect(page.locator("#settingsDialog")).to_be_visible()
    expect(page.locator("#profileName")).to_have_value("Unsaved edit")
    assert not requests


def test_score_sort_filters_and_dimension_details(settings):
    page, _, _, _ = settings
    page.locator('#settingsForm .dialog-actions button[value="cancel"]').click()
    page.evaluate("""state.jobs=[
      {id:1,title:'A',employer:'Test',location:'',source:'manual',status:'new',archived:0,score:62,qualification_score:91,direction_score:42,curiosity_score:67},
      {id:2,title:'B',employer:'Test',location:'',source:'manual',status:'new',archived:0,score:70,qualification_score:40,direction_score:88,curiosity_score:50}
    ]; renderRows();""")
    expect(page.locator("#jobRows tr").first).to_contain_text("B")
    page.locator("#scoreSort").select_option("qualification_score")
    expect(page.locator("#jobRows tr").first).to_contain_text("A")
    page.locator("#directionFilter").fill("50")
    expect(page.locator("#jobRows tr")).to_have_count(1)
    expect(page.locator("#jobRows tr").first).to_contain_text("B")
    page.locator("#directionFilter").fill("")
    page.locator('#jobRows tr[data-id="1"]').click()
    expect(page.locator(".dimension-scores")).to_contain_text("91")
    expect(page.locator(".dimension-scores")).to_contain_text("42")
    expect(page.locator(".dimension-scores")).to_contain_text("67")
    expect(page.locator(".dimension-reasons")).to_have_count(3)
    expect(page.locator(".track-list")).to_contain_text("Frontend / software")


def test_scoring_json_is_validated_saved_and_returned(settings):
    page, requests, _, _ = settings
    page.locator('[data-tab="scoring"]').click()
    editor = page.locator("#scoringJson")
    scoring = json.loads(editor.input_value())
    scoring["overall_score"]["formula"] = {
        "qualification": 0.2, "direction": 0.6, "curiosity": 0.2,
    }
    scoring["retention_rules"]["store_if_any"][0]["gte"] = 35
    editor.fill(json.dumps(scoring))
    save(page)
    assert requests[-1]["scoring"] == scoring
    page.locator("#settingsBtn").click()
    page.locator('[data-tab="scoring"]').click()
    assert json.loads(editor.input_value()) == scoring
