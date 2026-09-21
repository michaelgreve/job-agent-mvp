# Michael's Jobagent — 0.3.0

A local-first personal job-search assistant for Jobnet, Jobindex and the employer career sites they link to.

It is intentionally an **assistant**, not an unsupervised mass-application bot. It collects, merges, scores, drafts and fills common fields, but it never clicks the final Send/Submit button.

## Version 0.3.0: career-direction scoring

Jobs now receive separate scores for Qualification, Direction and Curiosity. Overall is the configurable weighted result: 30% Qualification, 50% Direction and 20% Curiosity. A strong career-change opportunity can therefore survive scanning even when its Qualification score is modest, while familiar frontend work remains highly qualified without automatically ranking first.

The SQLite migration adds the three dimension columns and a scoring-version marker without replacing job records. On startup, jobs created by an older scorer are recalculated from their saved advert text. Status, archive state, drafts, sources, discovery timestamps and scan checkpoints remain unchanged.

The job detail view explains each dimension and its positive and negative signals. The list can sort and filter on every dimension. Application packages include all four scores, matched career tracks and reasons, while explicitly instructing the writer not to mention scores in the application.

## Version 0.2.8: neutral AI-agent handoff

The clipboard workflow is now labelled **Send til AI-agent** throughout the app. It produces a provider-neutral package that can be pasted into any suitable external AI agent, then accepts the finished application back as a local draft. Existing settings using the internal `clipboard` provider continue to work unchanged.

## Version 0.2.7: engineering foundation

- Uses numbered, recorded and transactional SQLite migrations.
- Enables SQLite WAL mode and a busy timeout for safer concurrent local access.
- Initializes the database once per process instead of repeating schema work for every query.
- Uses FastAPI's lifespan API instead of the deprecated startup event hook.
- Runs the test suite on Python 3.11–3.14 in GitHub Actions.
- Defines cross-platform line endings for shell, PowerShell, Python and data files.

## What it does

- Scans configurable Jobnet and Jobindex search-result URLs.
- Scores all available structured Jobnet cards and uses a broad configurable prefilter before opening Jobindex detail pages.
- Traverses Jobindex's numbered result pages and recognises its current advert and tracking-link formats.
- Reads Jobindex adverts embedded in frames and falls back safely to the result card when a detail page will not render.
- Uses source-neutral scan checkpoints so completed Jobnet searches only revisit adverts published since the previous successful run.
- Imports individual adverts from a URL or pasted text.
- Separately scores Qualification, Direction and Curiosity using the versioned configuration in `jobagent/scoring.v2.json`.
- Penalises red flags such as SAFe, PI planning, daily Scrum and burndown charts.
- Detects exact and fuzzy duplicates across sites.
- Keeps a private SQLite application ledger.
- Imports prior applications from CSV to prevent reapplying.
- Generates Danish applications through one provider-independent pipeline.
- Makes every generated draft editable and keeps draft history.
- Opens an assisted browser, fills common fields, pastes the latest draft and uploads the selected CV where fields are clearly labelled.
- Exports submitted applications as a Joblog-friendly CSV.

## Version 0.2.3: resilient Jobindex detail extraction

Jobindex detail pages do not all render in the same way. Some contain the advert in an embedded frame, while others may redirect or fail to expose the full advert in a fresh headless browser session. The browser extractor now combines the outer page with loaded frame documents before parsing.

Each Jobindex result card also carries a structured fallback containing its title, employer, location, publication date and available preview text. The scanner still attempts the full detail page first, but if that fails it keeps the candidate using the result-card data instead of reporting an error and discarding the job. The scan status shows how many Jobindex cards were used as fallbacks.

## Version 0.2.2: Jobindex pagination and result parsing

Jobindex now has its own source adapter instead of relying on the generic link collector. It recognises current `/vis-job/<id>` adverts, legacy `/jobannonce/` and `/job/` links, plus Jobindex tracking links such as `/c?t=<id>`. Tracking links are converted to one stable detail URL before deduplication.

The adapter walks Jobindex's numbered pages by adding `page=2`, `page=3` and so on to the saved search URL. It merges duplicate promoted cards across pages, stops when a page yields no new adverts, and honours the existing maximum-result and maximum-pagination limits. The saved URL itself is not modified.

Jobindex result-page loads are shown together with Jobnet batches as **resultatindlæsninger** in the scan status. Incremental scan checkpoints remain disabled for Jobindex until its publication-date information can be verified safely across result pages.

## Version 0.2.1: incremental scan checkpoints

Each saved search can now carry a local **scanningskontrolpunkt**. This is a source-neutral marker: Jobnet currently interprets it as a publication timestamp, while future source adapters may use another date, advert ID or opaque cursor.

For Jobnet, enabling **Kun nye opslag siden sidste fuldførte scanning** makes the runtime search order results by publication date. The scanner stops after it has safely crossed the previous checkpoint and only sends newer cards through scoring and candidate inspection. The saved search URL itself is not rewritten.

The new checkpoint is captured at scan start and committed only after the entire scan finishes without extraction errors, result limits or an unprocessed candidate overflow. If anything is incomplete, the old checkpoint remains in place, so the next run repeats work rather than skipping jobs.

A database reset also clears all scan checkpoints. You can clear one search independently under **Indstillinger → Søgninger → Nulstil kontrolpunkt**.

## Version 0.2.0: interchangeable application generators

Open **Indstillinger → AI og ansøgninger** and choose one of four modes:

### 1. Skabelon

- No connection, account or metered cost.
- Uses deterministic keyword matching to select profile highlights.
- Useful as a fallback, but deliberately marked as generic.

### 2. OpenAI API

- Sends the advert, score explanation, writing preferences and factual career profile to the OpenAI Responses API.
- Requests a structured result containing the application, facts used and warnings.
- Stores the result locally in the application ledger.
- Requires a separately billed API key; ChatGPT Plus is not used as an API credential.

Create a local `.env` file from `.env.example`:

```bash
cp .env.example .env
```

Then add:

```text
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-5-mini
```

Restart the app after changing `.env`. The API key is never sent to the browser interface or written to `profile.json`.

### 3. Lokal Ollama

- Keeps the advert and career profile on the local computer.
- Uses Ollama's local chat API with a JSON schema.
- Can discover installed models from Ollama automatically.
- Has no provider token bill, although generation speed and quality depend on the computer and model.

After installing Ollama separately, pull a model such as:

```bash
ollama pull qwen3:8b
```

Then use **Find lokale modeller** in the AI settings.

### 4. Send til AI-agent

- Creates a complete handoff package containing the advert, matching explanation, factual profile and writing rules.
- Copies the package into your existing ChatGPT conversation.
- Lets you paste the AI agent's finished application back into the app and save it as the current draft.
- Works with any suitable AI agent and requires no direct provider integration in the app.

## Draft review

Every stored draft now shows:

- provider and model;
- an editable application field;
- **Gem ændringer** and **Kopiér ansøgning** buttons;
- facts the AI says it used;
- warnings or details that should be checked.

Saving edits creates a new draft version rather than silently destroying the generated original.

## Safety boundaries

The browser assistant:

- never clicks Send, Submit or Apply;
- does not bypass CAPTCHA, MitID or other authentication;
- does not answer declarations, salary questions or legal questions;
- uses a separate local browser profile;
- leaves final review and submission to you.

The drafting prompt:

- treats the structured career profile as an allow-list of facts;
- distinguishes learning from professional experience;
- explicitly forbids invented experience, education, results and motivation;
- still requires human review before use.

## Requirements

- Python 3.11–3.14
- macOS, Linux or Windows
- Around 250 MB free space for the local Chromium browser
- Optional: Ollama or an OpenAI API key

## Setup — macOS or Linux

```bash
./setup.sh
./start.sh
```

On macOS you can double-click `setup.command` once and then `start.command` whenever you want to run it.

The dashboard opens at:

```text
http://127.0.0.1:8765
```

## Setup — Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\start.ps1
```

Git Bash users can also run `./setup.sh` and `./start.sh`. Setup checks versioned Python commands, then `python3` and `python`, and requires Python 3.11–3.14 on `PATH`. If setup cannot find Python, check `python --version` and `python3 --version` in the same terminal, then reopen the terminal after installing Python or changing `PATH`. WSL requires Python installed inside the Linux environment.

## Setup on another computer

Clone the repository and run the setup command for the operating system above. On first start, the app creates local `data/profile.json` and `data/searches.json` files from the safe example configuration. Fill in the local profile and search URLs in the dashboard, copy the desired CV into `data/cv/`, and create a local `.env` only when an API provider is needed.

The profile, searches, CV, generated applications, browser profile and SQLite database are intentionally not synchronized through Git. Transfer any of them separately and securely when the same private state is needed on another computer.

## One-time setup

### Add the proper CV

Copy the PDF or DOCX file into:

```text
data/cv/
```

Restart the app and select it under **Indstillinger → Profil → Standard-CV**.

### Add searches

Create the desired searches normally on Jobnet and Jobindex, copy each result-page URL, then paste and enable them under **Indstillinger → Søgninger**.

### Import previous applications

Use **Importér historik** with a CSV. Recognised columns include:

- `Ansøgningsdato`, `Dato` or `Date`
- `Arbejdsgiver`, `Virksomhed` or `Company`
- `Stilling`, `Titel` or `Title`
- `URL`, `Annonce` or `Link`
- `Sted` or `Location`

## Typical workflow

1. Click **Scan søgninger**.
2. Inspect the jobs ranked by score.
3. Mark interesting ones **Kortlistet** and obvious misses **Fravalgt**.
4. Choose the desired generator under **AI og ansøgninger**.
5. Click **Lav ansøgning**.
6. Read, edit and save the draft.
7. Click **Åbn ansøgningsassistent**.
8. Review every field and submit manually.
9. Change the status to **Sendt** or **Bekræftet**.
10. Export the Joblog CSV when needed.

## Database migration and reset

Updating from 0.1.x automatically adds non-destructive metadata columns to the `drafts` table. Existing jobs, drafts and settings remain intact.

For a clean start, use **Indstillinger → Vedligeholdelse → Farezone**, type `NULSTIL`, and confirm. This deletes ledger records and clears scan checkpoints, while preserving profile, search URLs, CV files, AI settings and browser login data.

## Data and privacy

Local data lives under:

```text
data/
```

Important files:

```text
data/jobagent.db
data/profile.json
data/searches.json
data/cv/
data/browser-profile/
```

When OpenAI mode is selected, the advert and relevant structured profile are sent to the configured API endpoint. Template, Ollama and AI-agent handoff preparation run locally; naturally, the handoff content leaves the computer when you paste it into an external AI service.

## Known limitations

- Jobnet and Jobindex can change their page structure. Jobindex pagination is deterministic, but its result markup is still parsed defensively.
- Generic form filling cannot support every recruitment platform.
- CV file contents are not automatically parsed yet; drafting uses the factual career profile stored in `profile.json`.
- Applications are still exported as `.txt`; polished PDF/DOCX generation remains on the roadmap.
- It does not read confirmation emails or write directly into Jobnet Joblog.
- It does not schedule itself.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

Version 0.2.8 contains 57 tests covering scoring, extraction, Jobnet structured data, Jobindex link formats, pagination, frame extraction and result-card fallback, publication-date checkpoints, atomic checkpoint commits, duplicate detection including archived jobs, transactional database migrations, database reset, provider selection, AI-agent handoff, draft storage, Ny status and archiving.

### Ny-status (0.2.5)
Job, som første gang bliver fundet af en automatisk scanning, får status **Ny**. De bliver ved med at være Ny, indtil du selv ændrer status, så en senere scanning ikke skjuler job, du endnu ikke har gennemgået. Eksisterende job ændres ikke, og manuelt tilføjede job starter fortsat som **Fundet**.


## Arkiv
Job kan arkiveres uden at miste deres workflow-status. Den aktive liste skjuler arkiverede job som standard, og knappen **Arkiv** viser dem i en separat liste. Et arkiveret job bevarer fx status `Afslag`, `Fravalgt` eller `Sendt` og kan gendannes senere.
