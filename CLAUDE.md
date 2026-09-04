# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Python async job scraper that runs on GitHub Actions (free, no server). Every run it pulls ~30k postings from ~110 ATS boards (Greenhouse, Lever, Ashby), direct big-tech feeds (Amazon, Workday tenants), curated GitHub new-grad lists (SimplifyJobs, vanshb03, speedyapply, jobright-ai, zapplyjobs, ApplyGuy, new-grad-2027-tracker) and the HN "Who is Hiring?" thread, filters per Discord channel, dedupes against `seen_jobs.json`, and posts new matches to Discord webhooks.

Two channels are built in: **pm-jobs** (entry-level PM/APM/TPM) and **swe-ai-full-time** (new-grad SWE/AI/ML/data). The internship channel (`SWE_WEBHOOK_URL`) was retired on 2026-09-04 and is ignored if set.

## Commands

```bash
pip install -r requirements.txt

# Unit tests — fast, no network (67 tests)
python -m pytest tests/ -q
python -m pytest tests/test_live_platform_smoke.py   # opt-in, hits real APIs: RUN_LIVE_SCRAPER_TESTS=1

# DRY RUN: what each channel WOULD notify right now (no Discord, no writes, seen-state ignored)
python test_run.py --channels
python test_run.py --channels --hours 72

# Verify one source is fetching (no Discord, no writes)
python test_run.py                       # Greenhouse + Lever + Ashby boards
python test_run.py --source zapply       # simplify | speedyapply | jobright | zapply | jsonsource | amazon | workday | hackernews
python test_run.py --source amazon --hours 24

# Seed seen_jobs.json without notifying (after adding sources/channels). Needs the
# channel webhooks set so the right channel names are seeded; values can be dummies.
PM_WEBHOOK_URL=x FULL_TIME_WEBHOOK_URL=x python main.py --init
# ...or in CI: Actions → Job Scraper → Run workflow → mode: init

# Normal run (sends Discord notifications)
python main.py

# Syntax check
python -m py_compile main.py config.py companies.py discord_notifier.py scrapers/*.py
```

## Architecture

### Data flow

```
GitHub Actions (cron 7,22,37,52 * * * * — see "Scheduling" below)
  → main.py
      → scrape_all_raw()              # fetches ALL jobs, no filtering; every source isolated (a crash = [] not a dead run)
          bulk (run concurrently, once each):
            SimplifyScraper           # SimplifyJobs + vanshb03 listings.json
            SpeedyApplyScraper        # speedyapply README tables
            JobRightScraper           # jobright-ai README tables (PM intern, PM new grad, SWE new grad)
            ZapplyScraper             # zapplyjobs/New-Grad-Jobs-2027 README (refreshes ~15 min)
            JsonSourceScraper         # ApplyGuy + new-grad-2027-tracker JSON (field specs in scrapers/json_sources.py)
            AmazonScraper             # amazon.jobs search.json
            WorkdayScraper            # NVIDIA / Salesforce / Adobe / Capital One / Intel CXS API
            HackerNewsScraper         # "Who is Hiring?" thread
          per company slug (semaphore ATS_CONCURRENCY):
            GreenhouseScraper / LeverScraper / AshbyScraper   # companies.get_companies()
      → filter_recent_jobs()          # drops jobs older than RECENT_POSTING_MAX_AGE_HOURS (+24h grace for date-only sources)
      → for each ChannelConfig:
          → filter_for_channel()      # keyword + exclusion + location (+ excluded_locations veto)
          → dedupe_jobs_for_channel() # collapse same URL or same company|title|location, keep best source
          → drop already-seen, sort newest-first, cap at MAX_NOTIFICATIONS_PER_RUN (rest queued)
          → discord_notifier.notify_jobs_batch()
          → mark seen (id + normalised URL)
  → save seen_jobs.json + queued_jobs.json → git commit [skip ci]
```

### Key design decisions

**Scrape once, fan out**: all scrapers run once per cycle; filtering is per-channel afterwards.

**Shared HTTP layer** (`scrapers/fetch.py`): one client factory (redirects on, gzip only — brotli crashed on large Ashby payloads), retries with backoff on timeouts/5xx/429, error messages that always name the exception type. Every scraper goes through it. `REQUEST_TIMEOUT` default is 25s because 10s made Databricks/Figma time out every run.

**Word-boundary matching** (`scrapers/base.py`): alphanumeric boundaries so `intern` ≠ `internal`, `vp` ≠ `mvp`, `sr` matches `Sr.`. Exclusions are ignored when the excluded word sits inside a matched positive phrase (`manager` inside `product manager`).

**AND keyword logic**: a channel that mixes early-career keywords (`intern`, `new grad`) with role keywords must match both. Neither built-in channel does this any more — the full-time channel is role-keyword only, so "Software Engineer" with no level tag matches (by design: most entry roles aren't tagged).

**Location filter with foreign veto**: positives as before (short tokens whole-word, longer substrings). `excluded_locations` (Canada, UK, India, ... whole-word) veto a job unless a *strong* positive also matched; `remote`, `ca`, `wa` are weak. So "Remote - Canada" and "Toronto, ON, CA" are rejected, "Remote - US or Canada" and "London / New York, NY" pass. Work-model-only locations ("Hybrid", "N/A", "3 Locations") count as unknown and pass.

**Greenhouse `content=true`**: gives `offices`, the only real location for boards that put "Hybrid" in `location.name` (Cloudflare), and `first_published` is used instead of `updated_at` (edits used to re-surface old jobs).

**Ashby dates**: the API field is `publishedAt`. The old code read `publishedDate` (doesn't exist), so every Ashby job was undated. Some boards (Snowflake) genuinely have no date → "Unknown" → kept by the recency filter → sorted last before the cap.

**Date-only sources**: jobright, Amazon, Workday, ApplyGuy, gradtracker only know the posting *date* and emit `YYYY-MM-DD`. `filter_recent_jobs` grants those +24h so a job posted at 23:00 isn't dropped forever by the next morning's run.

**Dedupe**: within a run, jobs sharing a normalised URL *or* the same company|title|location collapse to the highest-priority source (`PLATFORM_DEDUPE_PRIORITY`). Persisted seen-state uses only id + URL, because big employers re-post the same title/location as genuinely new reqs.

**Per-channel seen-state**: `seen_jobs.json["channels"][name]`. Channels no longer configured are pruned on normal runs (never on `--init`). Only successfully posted jobs are marked seen; capped ones are queued (6h TTL) and marked seen.

**Markdown tables are header-driven** (`scrapers/markdown_table.py`): columns are located by header name, so speedyapply's 5- and 6-column tables, jobright's link-in-title cells, and zapply's plain-text company cells all parse. The old fixed-index parser dropped 75% of speedyapply rows.

### Channel configuration priority

`config.load_channels()`:
1. `PM_WEBHOOK_URL` / `FULL_TIME_WEBHOOK_URL` → built-in channels with `config.py` defaults
2. `CHANNELS_JSON` env / `channels.json` → add channels or override a built-in one by name (supports `excluded_locations`)
3. `DISCORD_WEBHOOK_URL` → single-channel fallback (uses the full-time defaults)

### Companies list

`companies.get_companies()` merges static slugs in `companies.py` (all verified 2026-09-04) with `discovered_companies.json` (`INCLUDE_DISCOVERED_COMPANIES`). Bulk scrapers use empty lists as markers so the orchestrator runs them once.

Verify a slug:
```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/<slug>/jobs" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))"
curl -s "https://api.lever.co/v0/postings/<slug>?mode=json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"
curl -s "https://api.ashbyhq.com/posting-api/job-board/<slug>" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))"
```

### seen_jobs.json structure

```json
{
  "jobs": [{"id": "greenhouse-stripe-12345", "seen_at": "2026-09-04T..."}, {"id": "url:https://...", "seen_at": "..."}],
  "channels": {"pm-jobs": ["..."], "swe-ai-full-time": ["..."]},
  "last_run": "2026-09-04T...",
  "total_notified": 4577
}
```
Entries older than `SEEN_JOBS_MAX_AGE_DAYS` (30) are pruned each run.

## Scheduling (read this before touching the cron)

GitHub's `schedule` trigger is best-effort. Over the last 100 runs the median gap was **104 minutes** (max 12 h), not 15. `workflow_dispatch` from an external cron is the fix — see README "Reliable 15-minute scheduling". Until that's set up, `RECENT_POSTING_MAX_AGE_HOURS=24` means nothing is lost, only delayed.

`SEND_NO_NEW_SUMMARY` is currently `true` as a repo variable; that posts a "no new jobs" embed to every channel on every run. Set it to `false`.

## GitHub Actions

`.github/workflows/scraper.yml`: cron at minutes `7,22,37,52`, `workflow_dispatch` with a `mode` input (`normal` | `init`), `timeout-minutes: 15`, single concurrency group. Secrets: `PM_WEBHOOK_URL`, `FULL_TIME_WEBHOOK_URL` (`CHANNELS_JSON` optional). Non-secret tuning goes in Variables (`RECENT_POSTING_MAX_AGE_HOURS`, `ATS_CONCURRENCY`, `SEND_NO_NEW_SUMMARY`, `REQUEST_TIMEOUT`, source URL overrides).
