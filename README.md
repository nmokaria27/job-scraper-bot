# Job Scraper Discord Bot

Scrapes ~30,000 job postings per run from ~110 company ATS boards (Greenhouse, Lever, Ashby), direct big-tech feeds (Amazon, NVIDIA/Salesforce/Adobe/Capital One/Intel via Workday), curated new-grad GitHub lists (SimplifyJobs, vanshb03, speedyapply, jobright-ai, zapplyjobs, ApplyGuy, new-grad-2027-tracker) and Hacker News "Who is Hiring?", then posts new entry-level matches to Discord. Three channels are built in:

| Channel | Secret | What it catches |
|---|---|---|
| `pm-jobs` | `PM_WEBHOOK_URL` | Product Manager, APM, TPM, Product Owner, Product Analyst, PM internships (not Group/Sr/Director) |
| `swe-ai-full-time` | `FULL_TIME_WEBHOOK_URL` | New-grad / entry-level Software, AI/ML, Research, Data Science, SRE, MTS (no interns, no seniors, no sales/support/hardware "engineers") |
| `beauty-finance` | `BEAUTY_FINANCE_WEBHOOK_URL` | Beauty/CPG product development, formulation and packaging, plus early-career FP&A, financial analysis and consulting |

Runs entirely on GitHub Actions — no server, no database, no cost. A full run takes about 45 seconds.

## How it works

1. `main.py` fetches every source once (`scrape_all_raw`). Each source is isolated: if one API breaks, the run loses that source, not everything.
2. Jobs older than `RECENT_POSTING_MAX_AGE_HOURS` (24) are dropped. Sources that only know the posting *date* get an extra day of grace.
3. Each channel applies its keyword, exclusion, location and foreign-location filters.
4. Duplicates across sources (same URL, or same company+title+location) collapse to the source closest to the employer.
5. Jobs not yet sent to that channel are posted newest-first, capped at 25 per run; the rest are queued and flushed on quiet runs.
6. `seen_jobs.json` is committed back to the repo so state persists.

---

## Setup

### 1. Fork or clone to a **public** repo

Public repos get unlimited Actions minutes; private repos get 500/month, which ~96 runs/day exhausts in days.

### 2. Create Discord webhooks

Server Settings → Integrations → Webhooks → New Webhook → pick the channel → Copy Webhook URL. One per channel.

### 3. Add GitHub secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `PM_WEBHOOK_URL` | PM channel webhook |
| `FULL_TIME_WEBHOOK_URL` | SWE/AI/ML full-time channel webhook |
| `BEAUTY_FINANCE_WEBHOOK_URL` | Beauty/finance channel webhook |
| `CHANNELS_JSON` | *(optional)* JSON list to add channels or override a built-in one by name — see `channels.json.example` |
| `EXCLUDED_COMPANIES` | *(optional)* comma-separated employers to never notify in single-channel mode (default: Microsoft, Uber, Meta) |
| `DISCORD_WEBHOOK_URL` | *(optional)* single-channel fallback when no built-in channel webhook is set |

Optional repository **Variables** (not secrets):

| Variable | Default | Purpose |
|---|---|---|
| `RECENT_POSTING_MAX_AGE_HOURS` | `24` | Posting freshness window |
| `SEND_NO_NEW_SUMMARY` | `false` | Post a "no new jobs" embed on quiet runs — leave off, it's noise |
| `ATS_CONCURRENCY` / `REQUEST_TIMEOUT` | `8` / `25` | Board fetch parallelism and per-request timeout |
| `SIMPLIFY_URLS`, `SPEEDYAPPLY_URLS`, `JOBRIGHT_URLS`, `ZAPPLY_URLS`, `JSON_SOURCE_URLS`, `WORKDAY_TENANTS` | see `config.py` | Override source lists (e.g. when a repo rolls to a new year) |
| `RUN_COMPANY_DISCOVERY`, `DISCOVERY_SOURCE_URLS`, `INCLUDE_DISCOVERED_COMPANIES` | — | Auto-discover extra ATS slugs |
| `INCLUDE_NON_SPONSORING_COMPANIES` | `false` | Include US-person-only defense/federal employers |

### 4. Enable Actions write permission

Settings → Actions → General → Workflow permissions → **Read and write permissions**.

### 5. Seed the state (no notifications)

Actions → **Job Scraper** → **Run workflow** → mode **`init`**. This marks every *current* match as seen so the first real run doesn't flood the channels. Do this again whenever you add sources or channels.

### 6. Schedule

GitHub Actions runs the scraper every 30 minutes at **:07** and **:37** UTC, and skips midnight–6am Eastern. No external cron is required. GitHub's scheduler is best-effort: on this repo, gaps of a few hours are normal. `RECENT_POSTING_MAX_AGE_HOURS=24` means a late run still catches the posting; it just arrives later. A manual run is Actions → Job Scraper → Run workflow.

---

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in webhooks (or leave for dry runs)

python -m pytest tests/ -q      # unit tests, no network

python test_run.py --channels   # DRY RUN: prints what each channel would post right now
python test_run.py --channels --hours 72
BEAUTY_FINANCE_WEBHOOK_URL=x python test_run.py --channels  # beauty-finance dry run
python test_run.py --source beauty-finance-public         # Coty, Shiseido, Manifest, consulting RSS
python test_run.py --source jobright-finance              # Jobright Accounting & Finance README
python test_run.py --source zapply --hours 24   # one source: simplify | speedyapply | jobright | zapply | jsonsource | amazon | workday | hackernews
python test_run.py              # all Greenhouse / Lever / Ashby boards

PM_WEBHOOK_URL=x FULL_TIME_WEBHOOK_URL=x python main.py --init   # seed locally
python main.py                  # real run (posts to Discord)
```

`--channels` is the tool for tuning filters: it ignores seen-state and shows the full picture for the window.

---

## Customization

### Keywords, exclusions, locations

Defaults are in `config.py` (`DEFAULT_PM_*`, `DEFAULT_SWE_FULL_TIME_*`, `DEFAULT_LOCATIONS`, `DEFAULT_EXCLUDED_LOCATIONS`). Matching is whole-word and case-insensitive. An excluded word is ignored when it sits inside a matched positive phrase (`manager` in `product manager`).

Locations: short entries (`us`, `ca`, `ny`) match as whole tokens, longer ones as substrings. Entries in `excluded_locations` (Canada, UK, India, …) veto a job unless a concrete US location also appears, so "Remote - Canada" is rejected while "Remote - US or Canada" passes. Blank or work-model-only locations ("Hybrid", "N/A") are kept.

Companies: `excluded_companies` (default `DEFAULT_EXCLUDED_COMPANIES` = Microsoft, Uber, Meta) drops a posting whose company name contains the entry as a whole word — `meta` hits "Meta" and "Meta Platforms" but not "Metabase". Applies before keyword matching, on every built-in channel.

Bare `engineer` / `developer` are deliberately not full-time positives — they let "Systems Engineer", "Test Engineer" and "Salesforce Developer" through. Only role phrases (`software engineer`, `backend`, `machine learning`, `engineer, software`, `engineer graduate`, …) match. Internships are excluded on both channels.

To change a built-in channel without editing code, put a channel with the same `name` in `CHANNELS_JSON` / `channels.json` — it replaces the defaults. Any other `name` adds a channel.

### Companies

Edit `companies.py`. Every slug in it was verified on 2026-09-04. Verify a new one:

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/<slug>/jobs" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))"
curl -s "https://api.lever.co/v0/postings/<slug>?mode=json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"
curl -s "https://api.ashbyhq.com/posting-api/job-board/<slug>" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))"
```

Workday tenants use `WORKDAY_TENANTS="tenant:wdN:site:Label,..."`; find the values in a company's careers URL (`https://<tenant>.<wdN>.myworkdayjobs.com/<site>`).

### Sources

| Source | Kind | Notes |
|---|---|---|
| SimplifyJobs `Summer2027-Internships`, `New-Grad-Positions`; vanshb03 `Summer2027-Internships`, `New-Grad-2027` | `listings.json` | Precise `date_posted`. Roll the year in `SIMPLIFY_URLS` when a new cycle starts; don't list renamed old repos (GitHub serves both names → double download) |
| speedyapply `2027-SWE-College-Jobs`, `2027-AI-College-Jobs` | README table | Relative age (`3d`) |
| jobright-ai `2026-Product-Management-Internship`, `2026-Product-Management-New-Grad`, `2026-Software-Engineer-New-Grad` | README table | This is the data behind intern-list.com. Date-only |
| zapplyjobs `New-Grad-Jobs-2027` | README table | Regenerated every ~15 min, ages in minutes |
| ApplyGuy `2027-New-Grad-Jobs`, harrycodingnow `new-grad-2027-tracker` | JSON | Trackers that pull Workday/Eightfold boards (NVIDIA, Qualcomm, Microsoft, Jane Street…). Field specs in `scrapers/json_sources.py` |
| amazon.jobs | JSON | Six search queries, sorted by recency |
| Workday | JSON (POST) | NVIDIA, Salesforce, Adobe, Capital One, Intel |
| Greenhouse / Lever / Ashby | JSON | ~110 boards in `companies.py` |
| Hacker News | JSON | Monthly "Who is Hiring?" thread |

Evaluated and not used: intern-list.com (Airtable embeds, no API — jobright-ai repos are the same data), briansjobsearch.com (a search-query builder, no listings), Microsoft careers API (ignores query/paging), Google/Meta/Apple/Uber/Tesla careers (no public JSON).

---

## Work authorization filtering

Tuned for an international candidate. US defense primes (Lockheed, Northrop, L3Harris, BAE,
General Dynamics, Anduril), federal-services integrators (CACI, Leidos, Booz Allen, Peraton,
SAIC, MITRE, Noblis) and national labs are excluded by default: most engineering roles there
require a US person — citizen or permanent resident — regardless of visa sponsorship policy.

Measured 2026-09-17: **23 of 214** swe-channel postings (11%) came from these employers.

Two halves, because they arrive from different places:

| | where | list |
|---|---|---|
| Company-name exclusion | every source, mostly the aggregator feeds | `config.DEFAULT_NON_SPONSORING_COMPANIES` (41 entries) |
| ATS board split | `companies.py` | `US_ONLY_COMPANIES` (anduril, shield-ai, skydio) |

Set the repo variable `INCLUDE_NON_SPONSORING_COMPANIES = true` to include both.

**Important caveat:** per-company H-1B sponsorship is *not* knowable from any job board API — it
lives in DOL LCA disclosure filings. These lists cover the **structural** cases only (ITAR /
clearance / federal), which are reliable. Everything remaining is commercial, where sponsorship is
common but **not guaranteed**. Verify before applying.

---

## Directory structure

```
.
├── .github/workflows/scraper.yml  # cron + workflow_dispatch (mode: normal | init)
├── scrapers/
│   ├── base.py                    # Job dataclass, keyword + location matching
│   ├── fetch.py                   # shared httpx client, retries, error formatting
│   ├── greenhouse.py / lever.py / ashby.py
│   ├── simplify.py                # SimplifyJobs-format listings.json
│   ├── markdown_table.py          # header-driven README table parser (speedyapply, jobright, zapply)
│   ├── json_sources.py            # spec-driven JSON trackers (ApplyGuy, gradtracker)
│   ├── bigtech.py                 # Amazon, Workday
│   └── hackernews.py
├── companies.py                   # ATS slugs (verified 2026-09-04)
├── config.py                      # env parsing, channel defaults, source URLs
├── discord_notifier.py            # webhook posting, 429 handling
├── main.py                        # orchestrator (--init seeds without notifying)
├── test_run.py                    # dry runs: --channels, --source <name>
├── tests/                         # unit tests (pytest)
├── seen_jobs.json                 # dedupe state, committed by Actions
├── queued_jobs.json               # capped notifications awaiting a quiet run
├── channels.json.example          # generated from config defaults
└── .env.example
```

---

## Troubleshooting

**Runs are hours apart** → GitHub cron delay; set up the external trigger (Setup step 6).

**A burst of old jobs after adding sources** → run the workflow with mode `init` first.

**Workflow fails on `git push`** → enable Read and write permissions (Setup step 4). The workflow rebases before pushing and runs in one concurrency group.

**`[WARN] ... not found (404)`** → the company moved ATS; check its careers URL and update `companies.py`.

**`[ERROR] ... ReadTimeout`** → raise the `REQUEST_TIMEOUT` variable (default 25 s).

**Too much noise** → `python test_run.py --channels --hours 72`, then extend the exclusion lists in `config.py` (or override the channel via `CHANNELS_JSON`).
