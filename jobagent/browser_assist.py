from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright, Page

from .config import BROWSER_PROFILE_DIR, CV_DIR, PROFILE_PATH, load_json
from .db import connection

FIELD_HINTS = {
    "first_name": ["fornavn", "first name", "firstname"],
    "last_name": ["efternavn", "last name", "lastname", "surname"],
    "full_name": ["fulde navn", "full name", "navn", "name"],
    "email": ["e-mail", "email", "mail"],
    "phone": ["telefon", "phone", "mobile", "mobil"],
    "address": ["adresse", "address", "street"],
    "postal_code": ["postnummer", "postal", "zip"],
    "city": ["by", "city", "town"],
}

async def _fill_by_labels(page: Page, values: dict[str, str]) -> list[str]:
    filled: list[str] = []
    for key, hints in FIELD_HINTS.items():
        value = values.get(key, "")
        if not value:
            continue
        for hint in hints:
            try:
                locator = page.get_by_label(re.compile(re.escape(hint), re.I)).first
                if await locator.count() and await locator.is_visible():
                    await locator.fill(value)
                    filled.append(key)
                    break
            except Exception:
                continue
        if key in filled:
            continue
        for hint in hints:
            try:
                locator = page.locator(f'input[name*="{hint}" i], input[id*="{hint}" i]').first
                if await locator.count() and await locator.is_visible():
                    await locator.fill(value)
                    filled.append(key)
                    break
            except Exception:
                continue
    return filled

async def assist(job_id: int) -> None:
    profile = load_json(PROFILE_PATH)
    person = profile.get("person", {})
    full = person.get("full_name", "").split()
    values = dict(person)
    values["first_name"] = full[0] if full else ""
    values["last_name"] = " ".join(full[1:]) if len(full) > 1 else ""

    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        draft = conn.execute("SELECT * FROM drafts WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    if not row:
        raise SystemExit(f"Job {job_id} findes ikke")
    job = dict(row)
    url = job.get("application_url") or job.get("source_url")
    if not url:
        raise SystemExit("Jobbet har ingen ansøgnings-URL")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR), headless=False, viewport={"width": 1350, "height": 900}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)
        filled = await _fill_by_labels(page, values)

        if draft:
            body = draft["body"]
            for hint in ["ansøgning", "application", "cover letter", "motivation"]:
                try:
                    loc = page.get_by_label(re.compile(hint, re.I)).first
                    if await loc.count() and await loc.is_visible():
                        await loc.fill(body)
                        break
                except Exception:
                    pass

        cv_name = profile.get("files", {}).get("default_cv", "")
        cv_path = CV_DIR / cv_name if cv_name else None
        if cv_path and cv_path.exists():
            for hint in ["cv", "resume"]:
                try:
                    loc = page.get_by_label(re.compile(hint, re.I)).first
                    if await loc.count() and await loc.is_visible():
                        await loc.set_input_files(str(cv_path))
                        break
                except Exception:
                    pass

        print("\nBrowseren er åbnet. Felter forsøgt udfyldt:", ", ".join(filled) or "ingen")
        print("Kontrollér alt selv. Programmet trykker ALDRIG på Send/Submit.")
        print("Luk browseren, når du er færdig.")
        await context.wait_for_event("close", timeout=0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id", type=int)
    args = parser.parse_args()
    asyncio.run(assist(args.job_id))

if __name__ == "__main__":
    main()
