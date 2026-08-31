import asyncio

from jobagent.extractors import (
    ExtractedJob,
    is_job_detail_url,
    job_validation_error,
    parse_job_html,
)

HTML = '''<html><head><script type="application/ld+json">{
"@context":"https://schema.org","@type":"JobPosting","title":"Frontendudvikler",
"hiringOrganization":{"name":"Acme A/S"},"description":"<p>Angular og TypeScript i et lille produktteam med ansvar for hele løsningen.</p>",
"identifier":{"value":"ABC-42"},"jobLocation":{"address":{"postalCode":"2100","addressLocality":"København Ø"}},
"validThrough":"2026-08-01"
}</script></head><body><a href="https://careers.acme.dk/apply/ABC-42">Ansøg nu</a></body></html>'''


def test_jsonld_job_extraction():
    job = parse_job_html("https://jobnet.dk/find-job/abc-42", HTML)
    assert job.title == "Frontendudvikler"
    assert job.employer == "Acme A/S"
    assert job.source_job_id == "ABC-42"
    assert job.application_url == "https://careers.acme.dk/apply/ABC-42"
    assert job_validation_error(job) == ""


def test_open_graph_fallback_extraction():
    html = '''<html><head>
      <meta property="og:title" content="Webudvikler – Jobnet">
      <meta property="og:description" content="Vi søger en erfaren webudvikler til udvikling og vedligeholdelse af moderne løsninger for vores kunder.">
    </head><body><main><h1>Webudvikler</h1><div class="company-name">Eksempel A/S</div></main></body></html>'''
    job = parse_job_html("https://jobnet.dk/find-job/123456", html)
    assert job.title == "Webudvikler"
    assert job.employer == "Eksempel A/S"
    assert job_validation_error(job) == ""


def test_empty_spa_shell_is_rejected():
    job = parse_job_html(
        "https://jobnet.dk/find-job/123456",
        "<html><body><div id='root'></div></body></html>",
    )
    assert "titel" in job_validation_error(job)


def test_jobnet_search_url_is_not_a_job_detail():
    search = "https://jobnet.dk/find-job?postalCode=2720&kmRadius=5"
    assert not is_job_detail_url(search, search, ["/find-job/"])
    assert is_job_detail_url(search, "https://jobnet.dk/find-job/a1b2c3d4", ["/find-job/"])
    assert not is_job_detail_url(search, "https://jobnet.dk/find-job", ["/find-job/"])


def test_jobindex_detail_filter():
    search = "https://www.jobindex.dk/jobsoegning?q=frontend"
    assert is_job_detail_url(search, "https://www.jobindex.dk/jobannonce/123456/frontendudvikler", ["/jobannonce/"])
    assert not is_job_detail_url(search, "https://www.jobindex.dk/jobsoegning?q=frontend", ["/jobannonce/"])


def test_employer_from_embedded_spa_json():
    html = """<html><body><h1>Frontendudvikler</h1>
    <script type="application/json">{"page":{"jobAdvertisement":{"companyName":"Københavns Kommune"}}}</script>
    <main>Vi søger en frontendudvikler til Angular og TypeScript i et produktteam med ansvar for moderne løsninger.</main>
    </body></html>"""
    job = parse_job_html("https://jobnet.dk/find-job/abcdef12", html)
    assert job.employer == "Københavns Kommune"


def test_employer_from_visible_organisation_name():
    html = """<html><body><h1>Projektleder til digitalisering</h1>
    <main>Hos Københavns Kommune arbejder vi med digitalisering og nye løsninger for borgerne.
    Stillingen er placeret centralt i København og annoncen indeholder rigeligt med tekst til validering.</main>
    </body></html>"""
    job = parse_job_html("https://jobnet.dk/find-job/abcdef13", html)
    assert job.employer == "Københavns Kommune"


def test_search_cards_are_deduplicated_and_non_job_links_removed():
    from jobagent.extractors import _normalise_result_cards

    search = "https://jobnet.dk/find-job?postalCode=2720&kmRadius=50"
    cards = _normalise_result_cards(search, [
        {"url": search, "title": "Find job", "snippet": "Søgeside"},
        {"url": "https://jobnet.dk/find-job/abcde123", "title": "Frontendudvikler", "snippet": "Angular og TypeScript"},
        {"url": "https://jobnet.dk/find-job/abcde123#top", "title": "Frontendudvikler", "snippet": "Dublet"},
        {"url": "https://jobnet.dk/hjaelp", "title": "Hjælp", "snippet": "Ikke en annonce"},
    ], ["/find-job/"])

    assert len(cards) == 1
    assert cards[0].url == "https://jobnet.dk/find-job/abcde123"
    assert cards[0].title == "Frontendudvikler"


def test_jobnet_xhr_payload_becomes_rich_result_cards():
    from jobagent.extractors import _find_total_count, _jobnet_cards_from_payload

    payload = {
        "jobAds": [
            {
                "jobAdId": "abc-123",
                "occupation": "Frontendudvikler",
                "hiringOrgName": "Acme A/S",
                "postalDistrictName": "København",
                "description": "<p>Angular, TypeScript og ansvar for hele løsningen.</p>",
            },
            {
                "jobAdId": "def-456",
                "occupation": "IT-supporter",
                "hiringOrgName": "Eksempel ApS",
                "postalDistrictName": "Vanløse",
                "description": "Brugersupport og praktisk fejlfinding.",
            },
        ],
        "totalCount": 8604,
    }

    cards = _jobnet_cards_from_payload(payload)
    assert len(cards) == 2
    assert cards[0].url == "https://jobnet.dk/find-job/abc-123"
    assert cards[0].title == "Frontendudvikler"
    assert "Acme A/S" in cards[0].snippet
    assert "Angular" in cards[0].snippet
    assert _find_total_count(payload) == 8604


def test_jobnet_xhr_payload_supports_wrapped_response_and_supplied_detail_url():
    from jobagent.extractors import _jobnet_cards_from_payload

    payload = {
        "data": {
            "result": {
                "jobAds": [{
                    "jobAdId": "xyz-789",
                    "title": "Webudvikler",
                    "companyName": "Widgets A/S",
                    "location": "Frederiksberg",
                    "jobAdUrl": "/find-job/xyz-789",
                }]
            }
        }
    }
    cards = _jobnet_cards_from_payload(payload)
    assert len(cards) == 1
    assert cards[0].url == "https://jobnet.dk/find-job/xyz-789"
    assert "Widgets A/S" in cards[0].snippet

def test_visible_load_more_ignores_hidden_responsive_copy():
    import asyncio
    import re
    from jobagent.extractors import _find_visible_load_more

    class Control:
        def __init__(self, text, visible):
            self.text = text
            self.visible = visible

        async def is_visible(self):
            return self.visible

        async def inner_text(self):
            return self.text

        async def get_attribute(self, name):
            return None

    class Controls:
        def __init__(self, items):
            self.items = items

        async def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Page:
        def __init__(self):
            self.items = [
                Control("Indlæs flere job", False),
                Control("Noget andet", True),
                Control("Indlæs flere jobs", True),
            ]

        def locator(self, selector):
            return Controls(self.items)

    async def run_test():
        result = await _find_visible_load_more(
            Page(), re.compile(r"indlæs\s+flere\s+job(?:s)?", re.I)
        )
        assert result is not None
        assert await result.inner_text() == "Indlæs flere jobs"

    asyncio.run(run_test())


def test_jobnet_external_ad_uses_real_url_and_structured_data():
    from jobagent.extractors import _jobnet_cards_from_payload, extracted_job_from_card

    payload = {
        "jobAds": [{
            "jobAdId": "27bbbf96-3e9e-488b-9328-cfaaba208e01",
            "occupation": "IT-konsulent",
            "title": "IT-konsulent til applikationsdrift og automatisering",
            "hiringOrgName": "Statens It",
            "postalCode": 2750,
            "postalDistrictName": "Ballerup",
            "description": "<p>Du arbejder med applikationsdrift, automatisering, PowerShell, Python, SQL og teknisk problemløsning i et erfarent team.</p>",
            "jobAdUrl": "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=123&ProjectId=456",
            "isExternal": True,
            "publicationDate": "2026-07-10T00:00:00+02:00",
            "applicationDeadline": "2026-08-01T23:59:00+02:00",
            "workHourPartTime": False,
        }]
    }

    cards = _jobnet_cards_from_payload(payload)
    assert len(cards) == 1
    card = cards[0]
    assert card.url.startswith("https://candidate.hr-manager.net/")
    assert card.title == "IT-konsulent til applikationsdrift og automatisering"
    assert card.job_data["source"] == "jobnet"
    assert card.job_data["source_job_id"] == "27bbbf96-3e9e-488b-9328-cfaaba208e01"
    assert card.job_data["employer"] == "Statens It"
    assert "2750 Ballerup" in card.job_data["location"]
    assert card.job_data["application_url"].startswith("https://candidate.hr-manager.net/")

    extracted = extracted_job_from_card(card)
    assert extracted is not None
    assert extracted.title == card.title
    assert "PowerShell" in extracted.description


def test_trusted_jobnet_external_card_survives_normalisation_and_dedupes_dom_copy():
    from jobagent.extractors import _normalise_result_cards

    job_id = "27bbbf96-3e9e-488b-9328-cfaaba208e01"
    external = "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=123&ProjectId=456"
    job_data = {
        "source": "jobnet",
        "source_job_id": job_id,
        "source_url": external,
        "application_url": external,
        "title": "IT-konsulent",
        "employer": "Statens It",
        "location": "2750 Ballerup",
        "description": "En tilstrækkeligt lang annoncetekst om applikationsdrift, automatisering og teknisk problemløsning.",
    }
    cards = _normalise_result_cards(
        "https://jobnet.dk/find-job?postalCode=2720&kmRadius=50",
        [
            {"url": external, "title": "IT-konsulent", "snippet": "Teknisk job", "job_data": job_data},
            {"url": f"https://jobnet.dk/find-job/{job_id}", "title": "IT-konsulent", "snippet": "DOM-kopi"},
        ],
        ["/find-job/"],
    )
    assert len(cards) == 1
    assert cards[0].url == external
    assert cards[0].job_data["source_job_id"] == job_id


def test_jobindex_current_detail_and_tracking_urls_are_recognised():
    search = "https://www.jobindex.dk/jobsoegning/it/systemudvikling?sort=date"
    assert is_job_detail_url(search, "https://www.jobindex.dk/vis-job/h1631460", [])
    assert is_job_detail_url(search, "https://jobindex.dk/c?t=h1631460&ctx=w&jobsearch_position=1", [])
    assert is_job_detail_url(search, "https://www.jobindex.dk/job/h1631460", [])
    assert not is_job_detail_url(search, "https://www.jobindex.dk/jobsoegning/it/systemudvikling?page=2", [])


def test_jobindex_tracking_url_is_canonicalised_and_deduplicated():
    from jobagent.extractors import _normalise_result_cards

    search = "https://www.jobindex.dk/jobsoegning/it/systemudvikling?sort=date"
    cards = _normalise_result_cards(search, [
        {
            "url": "https://www.jobindex.dk/c?t=h1631460&ctx=w&jobsearch_position=1",
            "title": "Senior Frontend Developer",
            "snippet": "Angular og TypeScript hos Grundfos",
        },
        {
            "url": "https://www.jobindex.dk/vis-job/h1631460",
            "title": "Senior Frontend Developer",
            "snippet": "Dublet",
        },
    ])
    assert len(cards) == 1
    assert cards[0].url == "https://www.jobindex.dk/vis-job/h1631460"


def test_jobindex_page_url_replaces_existing_page_parameter():
    from jobagent.extractors import _jobindex_page_url

    base = "https://www.jobindex.dk/jobsoegning/it/systemudvikling?address=Vanloese&sort=date&page=8"
    assert _jobindex_page_url(base, 1) == (
        "https://www.jobindex.dk/jobsoegning/it/systemudvikling?address=Vanloese&sort=date"
    )
    assert _jobindex_page_url(base, 3).endswith("address=Vanloese&sort=date&page=3")


def test_jobindex_result_html_supports_paid_robot_and_tracking_cards():
    from jobagent.extractors import _jobindex_cards_from_html

    search = "https://www.jobindex.dk/jobsoegning/it/systemudvikling?sort=date"
    html = """
    <html><body>
      <div class="PaidJob">
        <h2><a href="/c?t=h111&ctx=w">Frontendudvikler</a></h2>
        <p><b>Acme A/S</b>, København</p>
        <p>Angular og TypeScript i et produktteam, hvor løsningen udvikles og vedligeholdes i tæt samarbejde.</p>
        <time datetime="2026-07-20"></time>
      </div>
      <div class="jix_robotjob">
        <a href="/vis-job/h222"><b>Softwareudvikler</b></a>
        <p><b>Widgets ApS</b>, Frederiksberg</p>
        <p>Udvikling og integrationer mellem flere forretningskritiske systemer samt løbende fejlfinding.</p>
      </div>
    </body></html>
    """
    cards = _jobindex_cards_from_html(search, html)
    assert [card.url for card in cards] == [
        "https://www.jobindex.dk/vis-job/h111",
        "https://www.jobindex.dk/vis-job/h222",
    ]
    assert cards[0].title == "Frontendudvikler"
    assert "Acme A/S" in cards[0].snippet
    assert cards[0].job_data is not None
    assert cards[0].job_data["source_job_id"] == "h111"
    assert cards[0].job_data["employer"] == "Acme A/S"
    assert cards[0].job_data["date_posted"] == "2026-07-20"
    assert cards[1].title == "Softwareudvikler"
    assert cards[1].job_data is not None
    assert cards[1].job_data["employer"] == "Widgets ApS"
    assert cards[1].job_data["location"] == "Frederiksberg"


def test_jobindex_candidate_falls_back_to_result_card_when_detail_page_fails(monkeypatch):
    import jobagent.extractors as extractors

    card = extractors.SearchResultCard(
        url="https://www.jobindex.dk/jobannonce/h1683617/example",
        title="Systemudvikler med fokus på DevOps",
        snippet=(
            "Systemudvikler med fokus på DevOps hos PFA. Du arbejder med CI/CD, "
            "automatisering, stabile løsninger, monitorering og fejlfinding i tæt samarbejde."
        ),
        job_data=extractors.ExtractedJob(
            source="jobindex",
            source_job_id="h1683617",
            source_url="https://www.jobindex.dk/jobannonce/h1683617/example",
            application_url="https://www.jobindex.dk/jobannonce/h1683617/example",
            title="Systemudvikler med fokus på DevOps",
            employer="PFA",
            location="København",
            description=(
                "Du arbejder med CI/CD, automatisering, stabile løsninger, monitorering "
                "og fejlfinding i tæt samarbejde med udvikling, test og drift."
            ),
        ).dict(),
    )

    async def fail_detail(_url):
        raise RuntimeError("detail page did not render")

    monkeypatch.setattr(extractors, "extract_job", fail_detail)
    job, fetched, fallback = asyncio.run(extractors.extract_candidate_job(card))
    assert fetched is True
    assert fallback is True
    assert job.title == "Systemudvikler med fokus på DevOps"
    assert job.employer == "PFA"


def test_browser_documents_combine_outer_title_and_iframe_description():
    from jobagent.extractors import _combine_browser_documents

    outer = "<html><body><h1>Frontendudvikler</h1></body></html>"
    frame = (
        "<html><body><main>Vi søger en frontendudvikler til Angular og TypeScript. "
        "Du skal udvikle og vedligeholde en moderne webplatform sammen med teamet.</main></body></html>"
    )
    combined = _combine_browser_documents([outer, frame])
    job = parse_job_html("https://www.jobindex.dk/jobannonce/h123/frontendudvikler", combined)
    assert job.title == "Frontendudvikler"
    assert "moderne webplatform" in job.description
    assert job_validation_error(job) == ""
