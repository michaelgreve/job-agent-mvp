## Completed in 0.2.5

- Added a dedicated **Ny** status for jobs first discovered by automated scans.
- New jobs remain **Ny** until the user reviews/changes them; later scans do not silently age them out.
- Existing jobs remain untouched and manual imports still begin as **Fundet**.
- Added Ny to dashboard counts, filters and status badges.

# Roadmap

## Completed in 0.2.7

- Added numbered, recorded and transactional SQLite migrations.
- Enabled SQLite WAL mode, foreign-key enforcement and a five-second busy timeout.
- Avoided repeated schema initialization for every database connection.
- Replaced the deprecated FastAPI startup event with a lifespan handler.
- Added GitHub Actions coverage for Python 3.11–3.14.
- Added cross-platform line-ending rules.
- Added regression coverage for failed migrations and archived-job deduplication.

## Completed in 0.2.3

- Combined parsing of Jobindex's outer detail page and loaded iframe documents.
- Structured Jobindex result-card fallback for adverts that fail to render in detail view.
- Employer, location and publication-date extraction from common Jobindex card markup.
- Visible fallback count in scan status without treating recovered candidates as errors.

## Completed in 0.2.2

- Dedicated Jobindex source adapter.
- Numbered Jobindex pagination using the `page` query parameter.
- Support for `/vis-job/`, `/jobannonce/`, `/job/` and `/c?t=` result links.
- Canonicalisation and deduplication of Jobindex tracking links.
- Generic scan-status label for Jobnet batches and Jobindex pages.

## Completed in 0.2.1

- Source-neutral scan checkpoint model per saved search.
- Jobnet publication-date ordering and incremental result filtering.
- Atomic checkpoint commits only after a complete error-free scan.
- Checkpoint reset per search and during a full database reset.
- Protection against stopping early on a single pinned older advert.

## Completed in 0.2.0

- Shared application-generation context for all providers.
- Replaceable Template, OpenAI, Ollama and Maya-handoff providers.
- Structured facts-used and warnings metadata.
- Ollama connection testing and local model discovery.
- OpenAI connection testing without exposing the API key to the browser.
- Editable drafts with version-preserving saves.
- Non-destructive database migration from 0.1.x.

## Next practical steps

1. Generate polished DOCX and PDF application files.
2. Parse the selected CV locally and compare it with `profile.json` without inventing facts.
3. Add per-job generation controls, such as length, tone and preferred provider override.
4. Add a factual consistency checker before a draft can be marked ready.
5. Improve browser-assistant support for common Danish ATS platforms.
6. Read submission-confirmation emails and propose status updates.
7. Add scheduled scans and a daily review digest.
