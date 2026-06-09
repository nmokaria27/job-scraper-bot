# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Python async job scraper that runs on GitHub Actions (free, no server). It scrapes Greenhouse, Lever, Ashby, SimplifyJobs, and HackerNews every 15 minutes, deduplicates against `seen_jobs.json`, and posts new matches to Discord webhooks. Supports multiple Discord channels with independent keyword/location filters.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# First-time setup — seed seen_jobs.json without sending Discord notifications
python main.py --init

# Normal run (sends Discord notifications for new jobs)
python main.py

# Verify scrapers are working (no Discord, no file writes)
python test_run.py
python test_run.py --simplify
python test_run.py --hours 24        # only jobs posted in last 24h

# Discover new ATS company slugs from source URLs
python discover_companies.py --sources "https://..."

# Run tests (requires pytest: pip install pytest)
python -m pytest tests/
python -m pytest tests/test_filters_and_dedupe.py   # unit tests — fast, no network
python -m pytest tests/test_live_platform_smoke.py  # hits real APIs

# Syntax check all Python files
python -m py_compile main.py config.py discord_notifier.py scrapers/base.py
```

## Architecture

### Data flow

```
GitHub Actions cron (every 15 min)
  → main.py
      → scrape_all_raw()           # fetches ALL jobs, no filtering
          → SimplifyScraper        # bulk: Summer2026 + New-Grad JSON repos
          → HackerNewsScraper      # bulk: "Who is Hiring?" thread
          → GreenhouseScraper      # per-company via companies.get_companies()
          → LeverScraper           # per-company
          → AshbyScraper           # per-company
      → filter_recent_jobs()       # drops jobs older than RECENT_POSTING_MAX_AGE_HOURS
      → for each ChannelConfig:
          → filter_for_channel()   # keyword + location match
          → dedupe vs seen_jobs["channels"][channel.name]
          → discord_notifier.notify_jobs_batch()
          → mark_seen_global() + mark_seen_channel()
  → save seen_jobs.json
  → git commit seen_jobs.json [skip ci]
```

### Key design decisions

**Scrape once, fan out**: All scrapers run once per cycle. Filtering happens per-channel after scraping, so adding channels doesn't increase API calls.

**Per-channel deduplication**: `seen_jobs.json` has a `"channels"` dict — each channel tracks its own set of sent job IDs. A job new to `pm-jobs` won't be suppressed just because it was already sent to `swe-jobs`.

**Only mark seen on success**: If a Discord webhook POST fails, the job is NOT marked seen and will retry on the next run.

**Word-boundary matching**: `scrapers/base.py` uses `\b` regex so `"intern"` won't match `"internal"`, `"vp"` won't match `"mvp"`, etc.

**AND keyword logic**: When a channel has both early-career keywords (`intern`, `new grad`) and role keywords (`software engineer`), a title must match BOTH groups. This prevents `"HR Intern"` from hitting an SWE channel.

### Channel configuration priority

`config.load_channels()` resolves in this order:
1. Individual webhook env vars (`SWE_WEBHOOK_URL`, `PM_WEBHOOK_URL`, `FULL_TIME_WEBHOOK_URL`) — uses built-in keyword defaults from `config.py`
2. `CHANNELS_JSON` env var / `channels.json` file — can add extra channels or override built-in ones by name
3. `DISCORD_WEBHOOK_URL` — single-channel fallback

### Companies list

`companies.get_companies()` merges:
- Static slugs in `companies.py` (`COMPANIES` dict)
- Dynamically discovered slugs from `discovered_companies.json` (controlled by `INCLUDE_DISCOVERED_COMPANIES` env var)

Bulk scrapers (`simplify`, `hackernews`) use empty lists as markers — the orchestrator calls them once, not per-slug.

### ATS slug verification

To verify a Greenhouse slug works before adding it:
```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/<slug>/jobs" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))"
```

### seen_jobs.json structure

```json
{
  "jobs": [{"id": "greenhouse-stripe-12345", "seen_at": "2026-05-08T..."}],
  "channels": {
    "swe-jobs": ["greenhouse-stripe-12345", "simplify-abc123"],
    "pm-jobs": ["simplify-xyz789"]
  },
  "last_run": "2026-05-08T...",
  "total_notified": 42
}
```

Entries older than `SEEN_JOBS_MAX_AGE_DAYS` (default 30) are pruned on each run. Migration from old single-channel format is automatic.

## Important config constants

Defined in `config.py`:
- `DEFAULT_SWE_KEYWORDS` / `DEFAULT_SWE_EXCLUDED_KEYWORDS` — used when `SWE_WEBHOOK_URL` is set
- `DEFAULT_PM_KEYWORDS` / `DEFAULT_PM_EXCLUDED_KEYWORDS` — used when `PM_WEBHOOK_URL` is set  
- `DEFAULT_SWE_FULL_TIME_KEYWORDS` — used when `FULL_TIME_WEBHOOK_URL` is set
- `REQUEST_RETRY_ATTEMPTS` (default 2) — Ashby scraper retries on 5xx/timeout

## GitHub Actions

The workflow (`.github/workflows/scraper.yml`) runs at minutes `7,22,37,52` of every hour (off-peak to reduce GitHub queue delays). It's also triggered by cron-job.org via `workflow_dispatch` for reliable 15-minute scheduling.

Secrets go in **Settings → Secrets** (webhook URLs, `CHANNELS_JSON`). Non-secret tuning vars go in **Settings → Variables** (`RECENT_POSTING_MAX_AGE_HOURS`, `ATS_CONCURRENCY`, `SEND_NO_NEW_SUMMARY`).
