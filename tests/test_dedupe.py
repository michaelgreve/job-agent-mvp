from jobagent.dedupe import compare_jobs, normalize_url


def test_tracking_removed():
    assert normalize_url("https://Example.com/job/42/?utm_source=x&foo=bar") == "https://example.com/job/42?foo=bar"


def test_cross_site_duplicate_by_text():
    a = {"title":"Senior Frontendudvikler", "employer":"Acme A/S", "location":"København", "description":"Angular TypeScript udvikling af vores kundeportal", "canonical_url":"", "content_hash":"", "source_job_id":"", "source":"jobnet"}
    b = {"title":"Frontendudvikler, senior", "employer":"Acme", "location":"Copenhagen", "description":"Vi søger en senior til Angular og TypeScript udvikling af kundeportalen", "canonical_url":"", "content_hash":"", "source_job_id":"", "source":"jobindex"}
    score, _ = compare_jobs(a,b)
    assert score > 0.70
