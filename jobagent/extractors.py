from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any, Callable
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

HTTP_USER_AGENT = "MichaelJobAgent/0.2.6 (personal job-search assistant; polite rate limiting)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

GENERIC_TITLES = {
    "", "ukendt stilling", "søg", "find job", "find-job", "jobnet", "jobindex",
    "ledige stillinger", "job", "stilling",
}


@dataclass
class ExtractedJob:
    source: str = "manual"
    source_job_id: str = ""
    source_url: str = ""
    application_url: str = ""
    title: str = ""
    employer: str = ""
    location: str = ""
    description: str = ""
    date_posted: str = ""
    deadline: str = ""
    employment_type: str = ""

    def dict(self) -> dict[str, Any]:
        return asdict(self)


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.casefold()
    if "jobnet.dk" in host:
        return "jobnet"
    if "jobindex.dk" in host:
        return "jobindex"
    return host.replace("www.", "") or "manual"


def _flatten_location(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(filter(None, (_flatten_location(v) for v in value)))
    if isinstance(value, dict):
        addr = value.get("address", value)
        if isinstance(addr, dict):
            parts = [
                addr.get("streetAddress"), addr.get("postalCode"),
                addr.get("addressLocality"), addr.get("addressRegion"),
            ]
            return " ".join(str(p) for p in parts if p)
    return ""


def _jsonld_objects(soup: BeautifulSoup) -> list[dict]:
    result: list[dict] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        raw = tag.string or tag.get_text(" ", strip=True)
        try:
            value = json.loads(raw)
        except Exception:
            continue
        pending = list(value) if isinstance(value, list) else [value]
        while pending:
            item = pending.pop(0)
            if isinstance(item, dict) and "@graph" in item:
                pending.extend(v for v in item.get("@graph", []) if isinstance(v, dict))
            if isinstance(item, dict):
                result.append(item)
    return result



EMPLOYER_JSON_KEYS = {
    "hiringorganization", "hiringorganisation", "employer", "employername",
    "company", "companyname", "organizationname", "organisationname",
    "workplacename", "advertisername", "customername", "jobadcompanyname",
    "jobadvertisementcompanyname", "workplace",
}

DOMAIN_EMPLOYERS = {
    "kk.dk": "Københavns Kommune",
    "dtu.dk": "Danmarks Tekniske Universitet (DTU)",
    "ku.dk": "Københavns Universitet",
    "regionh.dk": "Region Hovedstaden",
    "frederiksberg.dk": "Frederiksberg Kommune",
}


def _embedded_json_objects(soup: BeautifulSoup) -> list[Any]:
    """Read JSON state embedded by SPA frameworks, not only schema.org JSON-LD."""
    result: list[Any] = []
    for tag in soup.find_all("script"):
        raw = (tag.string or tag.get_text(" ", strip=True) or "").strip()
        script_type = str(tag.get("type") or "").casefold()
        if not raw or ("json" not in script_type and not raw.startswith(("{", "["))):
            continue
        try:
            result.append(json.loads(raw))
        except Exception:
            continue
    return result


def _json_scalar(value: Any) -> str:
    if isinstance(value, str):
        return unescape(value).strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "displayName", "title", "value", "label"):
            if key in value:
                candidate = _json_scalar(value[key])
                if candidate:
                    return candidate
    return ""


def _find_json_value(value: Any, wanted_keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized_key in wanted_keys:
                candidate = _json_scalar(child)
                if (
                    1 < len(candidate) < 180
                    and candidate.casefold() not in {"jobnet", "jobindex", "virksomhed", "arbejdsgiver", "company", "ukendt"}
                ):
                    return candidate
        for child in value.values():
            candidate = _find_json_value(child, wanted_keys)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _find_json_value(child, wanted_keys)
            if candidate:
                return candidate
    return ""


def _labelled_text_value(soup: BeautifulSoup, labels: list[str]) -> str:
    text = _clean_page_text(soup.body or soup)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized_labels = {re.sub(r"\s+", " ", label.casefold()).rstrip(":") for label in labels}
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", " ", line).strip()
        lower = compact.casefold()
        for label in normalized_labels:
            if lower == label or lower == f"{label}:":
                for candidate in lines[index + 1:index + 4]:
                    candidate = re.sub(r"\s+", " ", candidate).strip()
                    if 1 < len(candidate) < 180 and candidate.casefold().rstrip(":") not in normalized_labels:
                        return candidate
            prefix = f"{label}:"
            if lower.startswith(prefix):
                candidate = compact[len(prefix):].strip()
                if 1 < len(candidate) < 180:
                    return candidate
    return ""


def _organisation_from_text(text: str) -> str:
    if not text:
        return ""
    known = [
        "Københavns Kommune", "Frederiksberg Kommune", "Gladsaxe Kommune",
        "Rødovre Kommune", "Herlev Kommune", "Region Hovedstaden",
        "Københavns Universitet", "Danmarks Tekniske Universitet",
    ]
    for name in known:
        if re.search(rf"\b{re.escape(name)}\b", text, re.I):
            return name

    # Prefer repeated, conventional organisation names. This is intentionally
    # conservative to avoid promoting a department or random heading to employer.
    pattern = re.compile(
        r"\b([A-ZÆØÅ][A-Za-zÆØÅæøå0-9&.'’/-]+(?:\s+[A-ZÆØÅ][A-Za-zÆØÅæøå0-9&.'’/-]+){0,5}"
        r"\s+(?:Kommune|Universitet|Styrelse|A/S|ApS|I/S|Fond|Forening))\b"
    )
    matches = [re.sub(r"\s+", " ", m).strip() for m in pattern.findall(text)]
    if not matches:
        return ""
    return Counter(matches).most_common(1)[0][0]


def _employer_from_url(url: str) -> str:
    host = urlparse(url or "").netloc.casefold().removeprefix("www.")
    for domain, employer in DOMAIN_EMPLOYERS.items():
        if host == domain or host.endswith(f".{domain}"):
            return employer
    return ""

def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.select_one(f'meta[property="{name}"], meta[name="{name}"]')
        if tag and tag.get("content"):
            return unescape(str(tag["content"])).strip()
    return ""


def _clean_page_text(node: Any) -> str:
    if not node:
        return ""
    # Work on a detached copy so navigation/chrome removal does not mutate the caller's soup.
    copy = BeautifulSoup(str(node), "html.parser")
    for tag in copy.select("script, style, noscript, svg, nav, header, footer, form"):
        tag.decompose()
    lines = [re.sub(r"\s+", " ", line).strip() for line in copy.get_text("\n", strip=True).splitlines()]
    return "\n".join(line for line in lines if line)


def _longest_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    texts: list[str] = []
    for selector in selectors:
        for node in soup.select(selector):
            text = _clean_page_text(node)
            if text:
                texts.append(text)
    return max(texts, key=len, default="")


def _labelled_value(soup: BeautifulSoup, labels: list[str]) -> str:
    pattern = re.compile(r"^(?:" + "|".join(re.escape(x) for x in labels) + r")\s*:?$", re.I)
    for text_node in soup.find_all(string=pattern):
        parent = text_node.parent
        if not parent:
            continue
        for candidate in [parent.find_next_sibling(), parent.parent.find_next_sibling() if parent.parent else None]:
            if candidate:
                value = candidate.get_text(" ", strip=True)
                if value and not pattern.match(value):
                    return value
    return ""


def parse_job_html(url: str, html: str) -> ExtractedJob:
    soup = BeautifulSoup(html, "html.parser")
    job = ExtractedJob(source=source_from_url(url), source_url=url)

    posting = None
    for obj in _jsonld_objects(soup):
        kind = obj.get("@type", "")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            posting = obj
            break

    if posting:
        org = posting.get("hiringOrganization") or {}
        identifier = posting.get("identifier") or ""
        if isinstance(identifier, dict):
            identifier = identifier.get("value") or identifier.get("name") or ""
        job.source_job_id = str(identifier)
        job.title = unescape(str(posting.get("title") or "")).strip()
        job.employer = unescape(str(org.get("name") if isinstance(org, dict) else org or "")).strip()
        job.location = _flatten_location(posting.get("jobLocation"))
        job.description = BeautifulSoup(str(posting.get("description") or ""), "html.parser").get_text("\n", strip=True)
        job.date_posted = str(posting.get("datePosted") or "")
        job.deadline = str(posting.get("validThrough") or "")
        emp = posting.get("employmentType") or ""
        job.employment_type = ", ".join(emp) if isinstance(emp, list) else str(emp)
        job.application_url = str(posting.get("url") or "")

    if not job.title:
        h1s = [el.get_text(" ", strip=True) for el in soup.find_all("h1")]
        job.title = next((value for value in h1s if value.casefold() not in GENERIC_TITLES), "")
    if not job.title:
        job.title = _meta_content(soup, "og:title", "twitter:title")
        job.title = re.sub(r"\s*[|–-]\s*(?:Jobnet|Jobindex).*?$", "", job.title, flags=re.I).strip()

    if not job.employer:
        for obj in _embedded_json_objects(soup):
            job.employer = _find_json_value(obj, EMPLOYER_JSON_KEYS)
            if job.employer:
                break
    if not job.employer:
        selectors = [
            '[itemprop="hiringOrganization"]', '[data-testid*="company" i]',
            '[data-testid*="employer" i]', '[class*="company" i]',
            '[class*="employer" i]', '[class*="workplace" i]',
            '[class*="organization" i]'
        ]
        for selector in selectors:
            el = soup.select_one(selector)
            if el:
                value = el.get_text(" ", strip=True)
                if value and len(value) < 180:
                    job.employer = value
                    break
    if not job.employer:
        job.employer = _labelled_value(soup, ["Arbejdsgiver", "Virksomhed", "Company"])
    if not job.employer:
        job.employer = _labelled_text_value(soup, ["Arbejdsgiver", "Virksomhed", "Company", "Ansættelsessted"])
    if not job.employer:
        page_text = _clean_page_text(soup.find("main") or soup.find("article") or soup.body)
        job.employer = _organisation_from_text(page_text[:12000])

    if not job.location:
        for selector in [
            '[itemprop="jobLocation"]', '[data-testid*="location" i]',
            '[class*="location" i]', '[class*="address" i]'
        ]:
            el = soup.select_one(selector)
            if el:
                value = el.get_text(" ", strip=True)
                if value and len(value) < 220:
                    job.location = value
                    break
    if not job.location:
        job.location = _labelled_value(soup, ["Arbejdssted", "Sted", "Lokation", "Location"])

    if not job.description:
        job.description = _longest_text(soup, [
            '[itemprop="description"]', '[data-testid*="description" i]',
            '[class*="job-description" i]', '[class*="jobDescription" i]',
            "article", "main"
        ])
    meta_description = _meta_content(soup, "og:description", "description", "twitter:description")
    if len(meta_description) > len(job.description):
        job.description = meta_description

    apply_candidates: list[str] = []
    for link in soup.find_all("a", href=True):
        classes = link.get("class", [])
        if isinstance(classes, str):
            classes = [classes]
        label = (link.get_text(" ", strip=True) + " " + " ".join(classes) + " " + str(link.get("aria-label") or "")).casefold()
        href = urljoin(url, link["href"])
        if any(term in label for term in ["ansøg", "ansog", "apply", "søg jobbet", "send ansøgning"]):
            apply_candidates.append(href)
    if apply_candidates:
        external = [u for u in apply_candidates if urlparse(u).netloc != urlparse(url).netloc]
        job.application_url = (external or apply_candidates)[0]
    if not job.application_url:
        job.application_url = url
    if not job.employer:
        job.employer = _employer_from_url(job.application_url)

    if not job.source_job_id:
        patterns = [
            r"(?:jobannonce|find-job|vis-job|job|position|vacancy)[/-]([a-zA-Z0-9_-]{3,})",
            r"[?&](?:id|jobid|jobId|jobAnnouncementId|t)=([^&]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url, flags=re.I)
            if match:
                job.source_job_id = match.group(1)
                break
    return job


def job_validation_error(job: ExtractedJob) -> str:
    title = re.sub(r"\s+", " ", job.title or "").strip()
    description = re.sub(r"\s+", " ", job.description or "").strip()
    if title.casefold() in GENERIC_TITLES:
        return "annoncens titel blev ikke indlæst"
    if len(description) < 60:
        return "annonceteksten blev ikke indlæst"
    return ""


def is_job_detail_url(search_url: str, candidate_url: str, patterns: list[str] | None = None) -> bool:
    if not candidate_url:
        return False
    parsed = urlparse(candidate_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    search = urlparse(search_url)
    candidate_host = parsed.netloc.casefold().removeprefix("www.")
    search_host = search.netloc.casefold().removeprefix("www.")
    if candidate_host != search_host:
        return False

    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", parsed.query, ""))
    search_clean = urlunparse((search.scheme, search.netloc, search.path.rstrip("/"), "", search.query, ""))
    if clean == search_clean:
        return False

    host = parsed.netloc.casefold()
    path = parsed.path.casefold().rstrip("/")
    query = {k.casefold(): v for k, v in parse_qs(parsed.query).items()}

    if "jobnet.dk" in host:
        if re.search(r"/find-job/[^/]{4,}(?:/.*)?$", path):
            return True
        if "/cv/findwork/" in path and len(path.rsplit("/", 1)[-1]) >= 4:
            return True
        return any(key in query for key in {"jobid", "jobannouncementid"})

    if "jobindex.dk" in host:
        if re.search(r"/(?:jobannonce|vis-job|job)/[^/]{3,}", path):
            return True
        # Jobindex result cards often use a click-tracking URL such as
        # /c?t=h1631460&ctx=w. The ``t`` value is the stable portal advert id.
        return path == "/c" and bool((query.get("t") or [""])[0].strip())

    return any(p.casefold() in candidate_url.casefold() for p in (patterns or []))


def _html_has_job_signal(url: str, html: str) -> bool:
    if not html or len(html) < 1000:
        return False
    job = parse_job_html(url, html)
    return not job_validation_error(job)


async def _dismiss_cookie_banner(page: Any) -> None:
    labels = [
        r"Afvis alle", r"Afvis", r"Kun nødvendige", r"Accepter alle", r"Accept all",
        r"Reject all", r"Tillad alle", r"OK",
    ]
    for label in labels:
        try:
            button = page.get_by_role("button", name=re.compile(label, re.I)).first
            if await button.count() and await button.is_visible():
                await button.click(timeout=1500)
                await page.wait_for_timeout(300)
                return
        except Exception:
            continue


async def _fetch_with_browser(url: str, wait_ms: int = 4500) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright er ikke installeret") from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=BROWSER_USER_AGENT, viewport={"width": 1440, "height": 1100})
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await _dismiss_cookie_banner(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            try:
                await page.wait_for_function(
                    """() => {
                        const text = (document.body?.innerText || '').trim();
                        const h1 = [...document.querySelectorAll('h1')].some(x => (x.innerText || '').trim().length > 3);
                        const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')]
                          .some(x => (x.textContent || '').includes('JobPosting'));
                        return jsonld || h1 || text.length > 1200;
                    }""",
                    timeout=15000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(wait_ms)

            # Jobindex sometimes renders the advert itself inside an iframe while the
            # outer page only contains navigation and a heading. ``page.content()``
            # does not include cross-origin frame DOM, but Playwright can read each
            # loaded frame directly. Combining the documents lets the normal parser
            # find a title in the shell and the description in the embedded advert.
            documents: list[str] = []
            try:
                documents.append(await page.content())
            except Exception:
                pass
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    frame_html = await frame.content()
                except Exception:
                    continue
                if frame_html and len(frame_html) > 200:
                    documents.append(frame_html)
            return _combine_browser_documents(documents)
        finally:
            await browser.close()


def _combine_browser_documents(documents: list[str]) -> str:
    """Combine a top-level page and loaded frame documents into parseable HTML."""
    unique: list[str] = []
    seen: set[str] = set()
    for document in documents:
        raw = str(document or "").strip()
        if not raw:
            continue
        marker = raw[:5000]
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(raw)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]

    head_parts: list[str] = []
    body_parts: list[str] = []
    for index, raw in enumerate(unique):
        soup = BeautifulSoup(raw, "html.parser")
        for node in soup.select('script[type="application/ld+json"], meta[name], meta[property]'):
            head_parts.append(str(node))
        body = soup.body or soup
        body_parts.append(f'<section data-browser-document="{index}">{str(body)}</section>')
    return "<html><head>" + "".join(head_parts) + "</head><body>" + "".join(body_parts) + "</body></html>"


async def fetch_html(url: str, browser_fallback: bool = True) -> str:
    # Jobnet and Jobindex are client-rendered. Their initial HTML can be a large but empty
    # application shell, so browser-first is intentional for these two sources.
    source = source_from_url(url)
    if browser_fallback and source in {"jobnet", "jobindex"}:
        browser_html = await _fetch_with_browser(url)
        if _html_has_job_signal(url, browser_html):
            return browser_html

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=25,
            headers={"User-Agent": HTTP_USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
            if _html_has_job_signal(url, html):
                return html
    except Exception:
        if not browser_fallback:
            raise

    if not browser_fallback:
        raise RuntimeError("Siden kunne ikke hentes eller indeholdt ingen indlæst jobannonce")

    browser_html = await _fetch_with_browser(url, wait_ms=6500)
    if not _html_has_job_signal(url, browser_html):
        raise RuntimeError("Siden åbnede, men jobannoncens titel og tekst blev ikke indlæst")
    return browser_html


async def extract_job(url: str) -> ExtractedJob:
    html = await fetch_html(url)
    job = parse_job_html(url, html)
    error = job_validation_error(job)
    if error:
        raise RuntimeError(error)
    return job


@dataclass
class SearchResultCard:
    url: str
    title: str = ""
    snippet: str = ""
    # Jobnet's search API already supplies nearly the complete advert. Keeping the
    # structured record avoids reopening a synthetic detail URL that may not exist
    # for externally hosted adverts. DOM-discovered cards leave this as None.
    job_data: dict[str, Any] | None = None


@dataclass
class SearchDiscovery:
    cards: list[SearchResultCard]
    listings_seen: int = 0
    load_more_clicks: int = 0
    limit_reached: bool = False
    api_batches: int = 0
    total_available: int = 0
    checkpoint_supported: bool = False
    checkpoint_reached: bool = False
    cards_after_checkpoint: int = 0
    newest_date_posted: str = ""
    oldest_date_posted: str = ""


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # Some feeds expose a date without time. Midnight in UTC is sufficient for
        # ordering and deliberately errs toward re-reading rather than skipping.
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jobnet_publication_order_url(url: str) -> str:
    """Return a Jobnet URL ordered newest-first without changing the saved URL."""
    parsed = urlparse(url)
    if "jobnet.dk" not in parsed.netloc.casefold():
        return url
    pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.casefold() != "ordertype"]
    pairs.append(("orderType", "PublicationDate"))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params,
                       urlencode(pairs, doseq=True), parsed.fragment))



def _jobindex_page_url(url: str, page_number: int) -> str:
    """Build a deterministic Jobindex pagination URL.

    Jobindex uses a normal ``page`` query parameter. Page one is kept without the
    parameter so the saved URL remains visually familiar; later pages replace any
    existing page value rather than appending duplicates.
    """
    parsed = urlparse(url)
    pairs = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() != "page"
    ]
    if page_number > 1:
        pairs.append(("page", str(page_number)))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params,
                       urlencode(pairs, doseq=True), parsed.fragment))


def _jobindex_source_job_id(url: str) -> str:
    parsed = urlparse(url)
    path_match = re.search(r"/(?:vis-job|jobannonce|job)/([^/?#]+)", parsed.path, re.I)
    if path_match:
        return path_match.group(1)
    query = parse_qs(parsed.query)
    return str((query.get("t") or query.get("jobid") or [""])[0]).strip()


def _jobindex_canonical_detail_url(url: str) -> str:
    """Turn Jobindex click-tracking links into one stable detail URL.

    Search cards commonly link through ``/c?t=<portal id>`` while other parts of
    the page expose ``/vis-job/<portal id>``. Canonicalising both forms prevents
    duplicates and avoids carrying position/tracking parameters into the ledger.
    """
    parsed = urlparse(url)
    if "jobindex.dk" not in parsed.netloc.casefold():
        return url
    job_id = _jobindex_source_job_id(url)
    if parsed.path.casefold().rstrip("/") == "/c" and job_id:
        return f"https://www.jobindex.dk/vis-job/{job_id}"
    return urlunparse((parsed.scheme or "https", parsed.netloc or "www.jobindex.dk",
                       parsed.path.rstrip("/"), "", "", ""))


def _jobindex_cards_from_html(search_url: str, html: str) -> list[SearchResultCard]:
    """Extract Jobindex result cards from both current and older result markup.

    Jobindex has used ``PaidJob``/``jix_robotjob`` containers for years, but the
    stable part is the advert URL. We therefore discover valid detail links first
    and use their nearest result-like container for title and preview text.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    by_url: dict[str, SearchResultCard] = {}
    container_class = re.compile(r"(?:paidjob|jix_robotjob|job.*result|result.*job|job-card|jobad)", re.I)

    for link in soup.find_all("a", href=True):
        href = urljoin(search_url, str(link.get("href") or ""))
        if not is_job_detail_url(search_url, href, ["/vis-job/", "/jobannonce/", "/job/"]):
            continue
        canonical = _jobindex_canonical_detail_url(href)
        container = link.find_parent(["article", "li", "div"], class_=container_class)
        if container is None:
            container = link.find_parent(["article", "li"]) or link.parent

        title = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        if not title or title.casefold() in {"se jobbet", "læs mere", "vis job", "ansøg"}:
            heading = container.select_one("h1, h2, h3, h4, [class*='title' i], b, strong") if container else None
            title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True) if heading else "").strip()
        snippet = _clean_page_text(container) if container else title
        snippet = re.sub(r"\s+", " ", snippet).strip()[:2400]
        if not title:
            title = snippet[:180]
        if len(title) > 240:
            title = title[:240].rsplit(" ", 1)[0]

        job_data = _jobindex_job_data_from_card(canonical, title, snippet or title, container)
        candidate = SearchResultCard(
            url=canonical, title=title, snippet=snippet or title, job_data=job_data
        )
        existing = by_url.get(canonical)
        if existing is None:
            by_url[canonical] = candidate
        else:
            if len(candidate.title) > len(existing.title):
                existing.title = candidate.title
            if len(candidate.snippet) > len(existing.snippet):
                existing.snippet = candidate.snippet
            existing.job_data = _richer_job_data(existing.job_data, candidate.job_data)
    return list(by_url.values())


def _richer_job_data(
    current: dict[str, Any] | None, incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not current:
        return incoming
    if not incoming:
        return current
    current_quality = len(str(current.get("description") or "")) + sum(
        50 for field in ("employer", "location", "date_posted", "application_url")
        if str(current.get(field) or "").strip()
    )
    incoming_quality = len(str(incoming.get("description") or "")) + sum(
        50 for field in ("employer", "location", "date_posted", "application_url")
        if str(incoming.get(field) or "").strip()
    )
    use_incoming = incoming_quality > current_quality
    richer = dict(incoming if use_incoming else current)
    other = current if use_incoming else incoming
    for field, value in other.items():
        if not str(richer.get(field) or "").strip() and str(value or "").strip():
            richer[field] = value
    return richer


def _jobindex_job_data_from_card(
    canonical_url: str, title: str, snippet: str, container: Any,
) -> dict[str, Any] | None:
    """Create a safe fallback record from a Jobindex result card.

    Jobindex result pages contain enough text to keep a candidate visible even when
    the corresponding detail page is an iframe, redirects through an employer ATS,
    or refuses to render in a fresh headless browser session. A full detail fetch is
    still attempted later; this record is only used when that fetch fails.
    """
    clean_title = re.sub(r"\s+", " ", str(title or "")).strip()
    clean_description = re.sub(r"\s+", " ", str(snippet or "")).strip()
    if not clean_title or len(clean_description) < 60:
        return None

    employer = ""
    location = ""
    date_posted = ""
    application_url = canonical_url

    if container is not None:
        time_node = container.find("time")
        if time_node:
            date_posted = str(time_node.get("datetime") or time_node.get_text(" ", strip=True) or "").strip()

        for selector in [
            '[data-testid*="company" i]', '[data-testid*="employer" i]',
            '[class*="company" i]', '[class*="employer" i]'
        ]:
            node = container.select_one(selector)
            if node:
                value = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                if value and value.casefold() != clean_title.casefold() and len(value) < 180:
                    employer = value
                    break

        for selector in [
            '[data-testid*="location" i]', '[class*="location" i]',
            '[class*="address" i]'
        ]:
            node = container.select_one(selector)
            if node:
                value = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                if value and len(value) < 220:
                    location = value
                    break

        bold_values: list[str] = []
        for node in container.select("b, strong"):
            value = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            if value and value.casefold() != clean_title.casefold() and value not in bold_values:
                bold_values.append(value)

        # Common Jobindex markup puts the employer in <b> and the location as
        # ordinary text immediately after it, e.g. <p><b>Acme A/S</b>, København</p>.
        for paragraph in container.find_all("p"):
            bold = paragraph.find(["b", "strong"])
            if not bold:
                continue
            bold_value = re.sub(r"\s+", " ", bold.get_text(" ", strip=True)).strip()
            if bold_value and bold_value.casefold() != clean_title.casefold():
                employer = employer or bold_value
            trailing = " ".join(
                str(part).strip(" ,–-\n\t")
                for part in bold.next_siblings
                if str(part).strip(" ,–-\n\t")
            )
            trailing = re.sub(r"\s+", " ", BeautifulSoup(trailing, "html.parser").get_text(" ", strip=True)).strip()
            if trailing and len(trailing) < 220:
                location = location or trailing
            if employer and location:
                break

        classes = " ".join(container.get("class", []) if hasattr(container, "get") else []).casefold()
        if "jix_robotjob" in classes:
            if len(bold_values) >= 2:
                employer = employer or bold_values[0]
                location = location or bold_values[1]
            elif len(bold_values) == 1:
                employer = employer or bold_values[0]
        else:
            if bold_values:
                employer = employer or bold_values[0]
            if len(bold_values) > 1:
                location = location or bold_values[1]

        external_links: list[tuple[int, str]] = []
        for link in container.find_all("a", href=True):
            href = urljoin(canonical_url, str(link.get("href") or ""))
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if "jobindex.dk" in parsed.netloc.casefold():
                continue
            label = " ".join([
                link.get_text(" ", strip=True), str(link.get("aria-label") or ""),
                " ".join(link.get("class", []) if isinstance(link.get("class"), list) else []),
            ]).casefold()
            priority = 0 if any(term in label for term in ("ansøg", "ansog", "apply", "søg jobbet")) else 1
            external_links.append((priority, href))
        if external_links:
            application_url = sorted(external_links, key=lambda item: item[0])[0][1]

    job = ExtractedJob(
        source="jobindex",
        source_job_id=_jobindex_source_job_id(canonical_url),
        source_url=canonical_url,
        application_url=application_url,
        title=clean_title,
        employer=employer,
        location=location,
        description=clean_description,
        date_posted=date_posted,
    )
    return job.dict()


def _jobindex_total_count_from_html(html: str) -> int:
    """Best-effort total count for progress only; never used to skip adverts."""
    soup = BeautifulSoup(html or "", "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    patterns = [
        r"(?:viser|fandt|fundet)\s+([\d.]+)\s+(?:ledige\s+)?job",
        r"([\d.]+)\s+(?:ledige\s+)?job(?:s|opslag|annoncer)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                return int(match.group(1).replace(".", ""))
            except ValueError:
                pass
    return 0


def _jobindex_has_later_page(search_url: str, html: str, current_page: int) -> bool:
    soup = BeautifulSoup(html or "", "html.parser")
    for link in soup.find_all("a", href=True):
        rel = {str(x).casefold() for x in (link.get("rel") or [])}
        if "next" in rel:
            return True
        href = urljoin(search_url, str(link.get("href") or ""))
        if urlparse(href).netloc.casefold().removeprefix("www.") != urlparse(search_url).netloc.casefold().removeprefix("www."):
            continue
        raw_page = (parse_qs(urlparse(href).query).get("page") or [""])[0]
        if str(raw_page).isdigit() and int(raw_page) > current_page:
            return True
    return False


async def _discover_jobindex_search_results(
    search_url: str,
    patterns: list[str],
    max_results: int,
    max_pages: int,
    headless: bool,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> SearchDiscovery:
    """Traverse Jobindex's numbered result pages and merge their job cards."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright er ikke installeret") from exc

    all_cards: dict[str, SearchResultCard] = {}
    pages_loaded = 0
    total_available = 0
    limit_reached = False

    collect_script = """els => els.map(a => {
      const title = (a.innerText || a.textContent || '').trim();
      const box = a.closest('article, li, .PaidJob, .jix_robotjob, [data-job-id], [class*="job" i], [class*="result" i]');
      const snippet = ((box && (box.innerText || box.textContent)) || title || '').trim();
      return {url: a.href, title, snippet};
    })"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(user_agent=BROWSER_USER_AGENT, viewport={"width": 1440, "height": 1100})
        try:
            for page_number in range(1, max_pages + 2):
                page_url = _jobindex_page_url(search_url, page_number)
                await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
                await _dismiss_cookie_banner(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                await page.wait_for_timeout(1200)

                html = await page.content()
                total_available = max(total_available, _jobindex_total_count_from_html(html))
                page_cards = _jobindex_cards_from_html(search_url, html)
                raw = await page.eval_on_selector_all("a[href]", collect_script)
                dom_cards = _normalise_result_cards(search_url, raw, patterns)
                merged_page: dict[str, SearchResultCard] = {card.url: card for card in page_cards}
                for card in dom_cards:
                    existing = merged_page.get(card.url)
                    if existing is None:
                        merged_page[card.url] = card
                    else:
                        if len(card.title) > len(existing.title):
                            existing.title = card.title
                        if len(card.snippet) > len(existing.snippet):
                            existing.snippet = card.snippet
                        existing.job_data = _richer_job_data(existing.job_data, card.job_data)

                before = len(all_cards)
                for card in merged_page.values():
                    existing = all_cards.get(card.url)
                    if existing is None:
                        all_cards[card.url] = card
                    else:
                        if len(card.title) > len(existing.title):
                            existing.title = card.title
                        if len(card.snippet) > len(existing.snippet):
                            existing.snippet = card.snippet
                        existing.job_data = _richer_job_data(existing.job_data, card.job_data)
                pages_loaded += 1
                new_count = len(all_cards) - before

                if on_progress:
                    on_progress({
                        "listings_seen": len(all_cards),
                        "load_more_clicks": max(0, pages_loaded - 1),
                        "api_batches": pages_loaded,
                        "total_available": total_available,
                        "cards_after_checkpoint": len(all_cards),
                        "checkpoint_reached": False,
                        "message": f"Indlæser Jobindex-side {page_number}… {len(all_cards)} fundet",
                    })

                if len(all_cards) >= max_results:
                    limit_reached = True
                    break
                if not merged_page or new_count == 0:
                    if page_number == 1 and not all_cards:
                        raise RuntimeError("Jobindex-siden blev indlæst, men ingen jobkort kunne aflæses")
                    break
        finally:
            await browser.close()

    cards = list(all_cards.values())[:max_results]
    return SearchDiscovery(
        cards=cards,
        listings_seen=len(cards),
        load_more_clicks=max(0, pages_loaded - 1),
        limit_reached=limit_reached,
        api_batches=pages_loaded,
        total_available=total_available,
        checkpoint_supported=False,
        checkpoint_reached=False,
        cards_after_checkpoint=len(cards),
    )


def _card_date_posted(card: SearchResultCard) -> datetime | None:
    if not isinstance(card.job_data, dict):
        return None
    return _parse_timestamp(card.job_data.get("date_posted"))


def _publication_checkpoint_reached(
    cards: list[SearchResultCard], checkpoint_value: str,
) -> bool:
    """Conservatively detect that newest-first pagination crossed the checkpoint.

    A single promoted or pinned older advert must not stop pagination. We therefore
    require the tail of the currently loaded, dated result order to be older than
    the checkpoint. Jobnet batches are normally much larger than five cards.
    """
    cutoff = _parse_timestamp(checkpoint_value)
    if cutoff is None:
        return False
    known = [date for date in (_card_date_posted(card) for card in cards) if date is not None]
    if not known:
        return False
    tail = known[-min(5, len(known)):]
    return all(date < cutoff for date in tail)


def _apply_publication_checkpoint(
    cards: list[SearchResultCard], checkpoint_value: str,
) -> tuple[list[SearchResultCard], bool, str, str]:
    """Filter cards using a publication-date checkpoint.

    Undated cards are retained because silently dropping them is riskier than
    rechecking them. Equality is retained as a small overlap; deduplication makes
    this harmless and protects adverts published in the same timestamp bucket.
    """
    cutoff = _parse_timestamp(checkpoint_value)
    if cutoff is None:
        dates = [(_card_date_posted(card), card) for card in cards]
        known = [date for date, _ in dates if date is not None]
        newest = max(known).isoformat() if known else ""
        oldest = min(known).isoformat() if known else ""
        return cards, False, newest, oldest

    kept: list[SearchResultCard] = []
    known: list[datetime] = []
    reached = False
    for card in cards:
        posted = _card_date_posted(card)
        if posted is None:
            kept.append(card)
            continue
        known.append(posted)
        if posted >= cutoff:
            kept.append(card)
        else:
            reached = True
    newest = max(known).isoformat() if known else ""
    oldest = min(known).isoformat() if known else ""
    return kept, reached, newest, oldest


def _normalise_result_cards(
    search_url: str,
    raw_cards: list[dict[str, Any]],
    patterns: list[str] | None = None,
    limit: int | None = None,
) -> list[SearchResultCard]:
    """Validate and de-duplicate links extracted from a search-results page.

    Kept separate from Playwright so the important URL filtering can be unit tested.
    """
    result: list[SearchResultCard] = []
    seen: set[str] = set()
    for raw in raw_cards:
        candidate = str(raw.get("url") or "").strip()
        job_data = raw.get("job_data") if isinstance(raw.get("job_data"), dict) else None

        # DOM links must be detail pages on the searched portal. Structured Jobnet
        # records are trusted output from Jobnet's own search response and may point
        # directly to an employer's external advert.
        if job_data:
            parsed = urlparse(candidate)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
        elif not is_job_detail_url(search_url, candidate, patterns):
            continue

        parsed = urlparse(candidate)
        if "jobindex.dk" in parsed.netloc.casefold():
            candidate = _jobindex_canonical_detail_url(candidate)
            parsed = urlparse(candidate)
        else:
            candidate = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
        source_job_id = str((job_data or {}).get("source_job_id") or "").strip()
        source = str((job_data or {}).get("source") or "").strip()
        if not source_job_id and "jobnet.dk" in parsed.netloc.casefold():
            match = re.search(r"/find-job/([^/?#]+)", parsed.path, re.I)
            if match:
                source, source_job_id = "jobnet", match.group(1)
        if not source_job_id and "jobindex.dk" in parsed.netloc.casefold():
            source, source_job_id = "jobindex", _jobindex_source_job_id(candidate)
        dedupe_key = f"{source}:{source_job_id}" if source_job_id else candidate
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
        snippet = re.sub(r"\s+", " ", str(raw.get("snippet") or title)).strip()[:2400]
        result.append(SearchResultCard(
            url=candidate, title=title, snippet=snippet, job_data=job_data
        ))
        if limit and len(result) >= limit:
            break
    return result


def _find_first_list(value: Any, wanted_key: str) -> list[dict[str, Any]]:
    """Find a named list in an API payload without depending on one wrapper shape."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() == wanted_key.casefold() and isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
        for child in value.values():
            found = _find_first_list(child, wanted_key)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_list(child, wanted_key)
            if found:
                return found
    return []


def _find_total_count(value: Any) -> int:
    wanted = {
        "total", "totalcount", "totalresults", "totalhits", "numberofresults",
        "totalnumberofjobads", "totaljobadcount", "jobadcount", "numberofjobads",
        "numberofhits", "totaljobads",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized in wanted and isinstance(child, (int, float)):
                return max(0, int(child))
            if normalized in wanted and isinstance(child, str) and child.strip().isdigit():
                return max(0, int(child.strip()))
        for child in value.values():
            count = _find_total_count(child)
            if count:
                return count
    elif isinstance(value, list):
        for child in value:
            count = _find_total_count(child)
            if count:
                return count
    return 0


def _jobnet_cards_from_payload(payload: Any) -> list[SearchResultCard]:
    """Convert Jobnet's structured search response into complete job records.

    The response contains enough information to score and store most adverts without
    opening a second page. This matters for external adverts: their ``jobAdUrl`` can
    point to the employer while a synthesized ``/find-job/<id>`` page may not exist.
    """
    jobs = _find_first_list(payload, "jobAds")
    cards: list[SearchResultCard] = []
    for job in jobs:
        job_id = str(
            job.get("jobAdId") or job.get("id") or job.get("identifier") or ""
        ).strip()
        # ``occupation`` is often a broad classification; ``title`` is the actual
        # headline and therefore much more useful for matching.
        title = str(
            job.get("title") or job.get("jobAdTitle") or job.get("headline")
            or job.get("occupation") or ""
        ).strip()
        employer = str(
            job.get("hiringOrgName") or job.get("companyName") or job.get("employerName")
            or job.get("workplaceName") or ""
        ).strip()

        address = str(job.get("workPlaceAddress") or job.get("workplaceAddress") or "").strip()
        postal_code = str(job.get("postalCode") or "").strip()
        district = str(job.get("postalDistrictName") or job.get("location") or job.get("city") or "").strip()
        location_parts: list[str] = []
        for part in (address, " ".join(x for x in (postal_code, district) if x).strip()):
            if part and part not in location_parts:
                location_parts.append(part)
        location = ", ".join(location_parts)

        description_html = str(
            job.get("description") or job.get("jobDescription") or job.get("summary") or ""
        )
        description = BeautifulSoup(description_html, "html.parser").get_text("\n", strip=True)

        supplied_url = str(
            job.get("jobAdUrl") or job.get("applicationUrl") or job.get("applyUrl")
            or job.get("applicationWebAddress") or job.get("url") or ""
        ).strip()
        supplied_url = urljoin("https://jobnet.dk", supplied_url) if supplied_url else ""
        supplied_is_jobnet_detail = bool(
            supplied_url and is_job_detail_url(
                "https://jobnet.dk/find-job", supplied_url, ["/find-job/"]
            )
        )
        supplied_host = urlparse(supplied_url).netloc.casefold() if supplied_url else ""
        supplied_is_external = bool(supplied_host and "jobnet.dk" not in supplied_host)

        internal_detail_url = f"https://jobnet.dk/find-job/{job_id}" if job_id else ""
        if supplied_is_jobnet_detail:
            source_url = supplied_url
        elif supplied_is_external:
            # Preserve the real employer-hosted advert. Do not invent a Jobnet page.
            source_url = supplied_url
        else:
            source_url = internal_detail_url or supplied_url
        if not source_url:
            continue

        application_url = supplied_url or source_url
        employment_parts: list[str] = []
        explicit_type = str(job.get("employmentType") or "").strip()
        if explicit_type:
            employment_parts.append(explicit_type)
        if job.get("workHourPartTime") is True:
            employment_parts.append("Deltid")
        elif job.get("workHourPartTime") is False:
            employment_parts.append("Fuldtid")

        data = ExtractedJob(
            source="jobnet",
            source_job_id=job_id,
            source_url=source_url,
            application_url=application_url,
            title=title,
            employer=employer,
            location=location,
            description=description,
            date_posted=str(job.get("publicationDate") or job.get("datePosted") or ""),
            deadline=str(job.get("applicationDeadline") or job.get("validThrough") or ""),
            employment_type=", ".join(dict.fromkeys(employment_parts)),
        ).dict()

        snippet = " · ".join(part for part in (title, employer, location, description) if part)
        cards.append(SearchResultCard(
            url=source_url, title=title, snippet=snippet[:2400], job_data=data
        ))
    return cards


def extracted_job_from_card(card: SearchResultCard) -> ExtractedJob | None:
    """Return a validated structured advert carried by a result card, if available."""
    if not isinstance(card.job_data, dict):
        return None
    allowed = ExtractedJob.__dataclass_fields__.keys()
    values = {key: card.job_data.get(key, "") for key in allowed}
    if not values.get("source_url"):
        values["source_url"] = card.url
    if not values.get("application_url"):
        values["application_url"] = card.url
    job = ExtractedJob(**values)
    return None if job_validation_error(job) else job


async def extract_candidate_job(card: SearchResultCard) -> tuple[ExtractedJob, bool, bool]:
    """Extract one candidate with a Jobindex result-card safety net.

    Returns ``(job, fetched_detail_page, used_result_card_fallback)``.
    Jobnet structured cards remain browser-free. Jobindex attempts the full advert
    first and falls back to its result-card text only when the detail page fails.
    """
    fallback = extracted_job_from_card(card)
    if source_from_url(card.url) == "jobindex":
        try:
            return await extract_job(card.url), True, False
        except Exception:
            if fallback is not None:
                return fallback, True, True
            raise
    if fallback is not None:
        return fallback, False, False
    return await extract_job(card.url), True, False


async def _find_visible_load_more(page: Any, pattern: re.Pattern[str]) -> Any | None:
    """Return the visible load-more control, ignoring hidden responsive duplicates."""
    controls = page.locator("button, a, [role='button']")
    try:
        count = await controls.count()
    except Exception:
        return None
    # Search from the end: Jobnet has at times rendered a hidden header copy before
    # the active control near the result list.
    for index in range(count - 1, -1, -1):
        control = controls.nth(index)
        try:
            if not await control.is_visible():
                continue
            text = re.sub(r"\s+", " ", (await control.inner_text()) or "").strip()
            aria = str(await control.get_attribute("aria-label") or "").strip()
            if pattern.search(text) or pattern.search(aria):
                return control
        except Exception:
            continue
    return None


async def discover_search_results(
    search_url: str,
    patterns: list[str],
    max_results: int = 500,
    max_load_more_clicks: int = 70,
    headless: bool = True,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> SearchDiscovery:
    """Load a search page and return structured result cards.

    For Jobnet this listens to the same `/bff/FindJob/Search` XHR responses used by
    the site itself. DOM links are retained as a fallback for Jobindex and future
    layout changes. The load-more control is selected from *visible* elements so a
    hidden responsive duplicate cannot stop pagination.

    ``checkpoint`` is source-neutral. The Jobnet adapter currently understands
    ``{"kind": "published_at", "value": <ISO timestamp>}`` and orders the live
    search newest-first before stopping as soon as older adverts are encountered.
    Jobindex uses deterministic numbered pages (``page=2``, ``page=3`` …) and is
    currently scanned without an incremental checkpoint.
    """
    if "jobindex.dk" in urlparse(search_url).netloc.casefold():
        return await _discover_jobindex_search_results(
            search_url=search_url,
            patterns=patterns,
            max_results=max_results,
            max_pages=max_load_more_clicks,
            headless=headless,
            on_progress=on_progress,
        )

    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    checkpoint_kind = str(checkpoint.get("kind") or "")
    checkpoint_value = str(checkpoint.get("value") or "")
    is_jobnet = "jobnet.dk" in urlparse(search_url).netloc.casefold()
    checkpoint_supported = is_jobnet and checkpoint_kind == "published_at"
    runtime_search_url = _jobnet_publication_order_url(search_url) if checkpoint_supported else search_url
    checkpoint_reached = False
    newest_date_posted = ""
    oldest_date_posted = ""

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright er ikke installeret") from exc

    raw_cards: list[dict[str, Any]] = []
    api_cards: dict[str, SearchResultCard] = {}
    api_batches = 0
    total_available = 0
    clicks = 0
    stagnant_rounds = 0
    limit_reached = False
    response_tasks: set[asyncio.Task[Any]] = set()

    collect_script = """els => els.map(a => {
      const title = (a.innerText || a.textContent || '').trim();
      const box = a.closest('article, li, [role=\"listitem\"], [data-testid*=\"job\" i], [class*=\"job\" i], [class*=\"result\" i]');
      const snippet = ((box && (box.innerText || box.textContent)) || title || '').trim();
      return {url: a.href, title, snippet};
    })"""

    def merged_cards(dom_cards: list[SearchResultCard]) -> list[SearchResultCard]:
        merged: dict[str, SearchResultCard] = dict(api_cards)
        for card in dom_cards:
            existing = merged.get(card.url)
            if existing:
                if not existing.title and card.title:
                    existing.title = card.title
                if len(card.snippet) > len(existing.snippet):
                    existing.snippet = card.snippet
            else:
                merged[card.url] = card
        return list(merged.values())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(user_agent=BROWSER_USER_AGENT, viewport={"width": 1440, "height": 1100})

        async def capture_jobnet_response(response: Any) -> None:
            nonlocal api_batches, total_available
            if "/bff/findjob/search" not in response.url.casefold() or response.status != 200:
                return
            try:
                payload = await response.json()
                batch = _jobnet_cards_from_payload(payload)
                if batch:
                    api_batches += 1
                    for card in batch:
                        api_cards.setdefault(card.url, card)
                total_available = max(total_available, _find_total_count(payload))
            except Exception:
                # DOM extraction remains available; a malformed telemetry response
                # should not abort the entire user's scan.
                return

        def schedule_capture(response: Any) -> None:
            task = asyncio.create_task(capture_jobnet_response(response))
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)

        page.on("response", schedule_capture)
        try:
            await page.goto(runtime_search_url, wait_until="domcontentloaded", timeout=60000)
            await _dismiss_cookie_banner(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            await page.wait_for_timeout(3500)
            if response_tasks:
                await asyncio.gather(*list(response_tasks), return_exceptions=True)

            while True:
                current_raw = await page.eval_on_selector_all("a[href]", collect_script)
                dom_cards = _normalise_result_cards(search_url, current_raw, patterns)
                current_cards = merged_cards(dom_cards)
                raw_cards = [card.__dict__ for card in current_cards]
                if checkpoint_supported:
                    filtered_now, reached_now, newest_now, oldest_now = _apply_publication_checkpoint(
                        current_cards, checkpoint_value
                    )
                    checkpoint_reached = checkpoint_reached or _publication_checkpoint_reached(
                        current_cards, checkpoint_value
                    )
                    newest_date_posted = newest_now or newest_date_posted
                    oldest_date_posted = oldest_now or oldest_date_posted
                else:
                    filtered_now = current_cards

                if on_progress:
                    on_progress({
                        "listings_seen": len(current_cards),
                        "load_more_clicks": clicks,
                        "api_batches": api_batches,
                        "total_available": total_available,
                        "cards_after_checkpoint": len(filtered_now),
                        "checkpoint_reached": checkpoint_reached,
                        "message": f"Indlæser søgeresultater… {len(current_cards)} fundet",
                    })

                if checkpoint_supported and checkpoint_value and checkpoint_reached:
                    break
                if len(current_cards) >= max_results:
                    limit_reached = True
                    break
                if clicks >= max_load_more_clicks:
                    limit_reached = True
                    break

                before = len(current_cards)
                load_more_text = re.compile(r"indlæs\s+flere\s+job(?:s)?", re.I)

                # Bring the bottom control into the rendered/visible part of virtualised pages.
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(450)
                except Exception:
                    pass
                button = await _find_visible_load_more(page, load_more_text)

                clicked = False
                if button is not None:
                    try:
                        await button.scroll_into_view_if_needed(timeout=2500)
                        await button.click(timeout=7000)
                        clicked = True
                    except Exception:
                        # Jobnet can re-render the component between lookup and click.
                        # A direct DOM click is safe here because this is only pagination.
                        try:
                            await button.evaluate("element => element.click()")
                            clicked = True
                        except Exception:
                            clicked = False

                if clicked:
                    clicks += 1
                    # Wait for either the XHR listener or DOM fallback to observe growth.
                    grew = False
                    for _ in range(20):
                        await page.wait_for_timeout(350)
                        if response_tasks:
                            await asyncio.gather(*list(response_tasks), return_exceptions=True)
                        after_raw = await page.eval_on_selector_all("a[href]", collect_script)
                        after_dom = _normalise_result_cards(search_url, after_raw, patterns)
                        after_cards = merged_cards(after_dom)
                        if len(after_cards) > before:
                            grew = True
                            break
                    if grew:
                        stagnant_rounds = 0
                    else:
                        stagnant_rounds += 1
                else:
                    # One final generic scroll helps pages that lazily mount the control.
                    await page.mouse.wheel(0, 6000)
                    await page.wait_for_timeout(900)
                    stagnant_rounds += 1

                after_raw = await page.eval_on_selector_all("a[href]", collect_script)
                after_dom = _normalise_result_cards(search_url, after_raw, patterns)
                after_cards = merged_cards(after_dom)
                raw_cards = [card.__dict__ for card in after_cards]
                if len(after_cards) > before:
                    stagnant_rounds = 0
                if stagnant_rounds >= 3:
                    break
        finally:
            if response_tasks:
                await asyncio.gather(*list(response_tasks), return_exceptions=True)
            await browser.close()

    all_cards = _normalise_result_cards(search_url, raw_cards, patterns, max_results)
    cards = all_cards
    if checkpoint_supported:
        cards, reached_now, newest_now, oldest_now = _apply_publication_checkpoint(
            all_cards, checkpoint_value
        )
        checkpoint_reached = checkpoint_reached or _publication_checkpoint_reached(
            all_cards, checkpoint_value
        )
        newest_date_posted = newest_now or newest_date_posted
        oldest_date_posted = oldest_now or oldest_date_posted
    return SearchDiscovery(
        cards=cards,
        listings_seen=len(all_cards),
        load_more_clicks=clicks,
        limit_reached=limit_reached,
        api_batches=api_batches,
        total_available=total_available,
        checkpoint_supported=checkpoint_supported,
        checkpoint_reached=checkpoint_reached,
        cards_after_checkpoint=len(cards),
        newest_date_posted=newest_date_posted,
        oldest_date_posted=oldest_date_posted,
    )


async def discover_links(
    search_url: str,
    patterns: list[str],
    max_links: int = 40,
    headless: bool = True,
) -> list[str]:
    """Backwards-compatible URL-only wrapper used by older integrations/tests."""
    discovery = await discover_search_results(
        search_url, patterns, max_results=max_links, headless=headless
    )
    return [card.url for card in discovery.cards[:max_links]]
