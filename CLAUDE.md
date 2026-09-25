# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Python async job scraper that runs on GitHub Actions (free, no server). Every run it pulls ~30k postings from ~110 ATS boards (Greenhouse, Lever, Ashby), direct big-tech feeds (Amazon, Workday tenants), curated GitHub new-grad lists (SimplifyJobs, vanshb03, speedyapply, jobright-ai, zapplyjobs, ApplyGuy, new-grad-2027-tracker) and the HN "Who is Hiring?" thread, filters per Discord channel, dedupes against `seen_jobs.json`, optionally ranks the survivors with the Jev judge (`jev.py`, off by default), and posts new matches to Discord webhooks.

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

# Jev judge evaluation (needs JEV_API_KEY; never touches the run path, writes nothing)
python jev_eval.py --golden --sweep                 # vs the labelled corpus in tests/
python jev_eval.py --live --hours 24                # real scrape: drops + ranking head-to-head
python jev_eval.py --live --save-corpus /tmp/c.json # cache the scrape...
python jev_eval.py --live --from-file /tmp/c.json   # ...and re-judge without scraping again

# Syntax check
python -m py_compile main.py config.py companies.py discord_notifier.py jev.py scrapers/*.py
```

## Architecture

### Data flow

```
GitHub Actions (cron 7,37 — every 30 min, see "Scheduling" below)
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
          → filter_for_channel()      # excluded_companies → keyword + exclusion → location (+ excluded_locations veto)
          → dedupe_jobs_for_channel() # collapse same URL or same company|title|location, keep best source
          → drop already-seen
          → jev.judge_for_channel()   # OPTIONAL second pass; no-op unless JEV_ENABLED. Never raises.
          → jev.promote_jobs()        # OPTIONAL, only when under cap; admits regex-rejected jobs (JEV_PROMOTE)
          → _rank_for_notification()  # best-fit first, recency within a bucket; == _newest_first when unjudged
          → cap at MAX_NOTIFICATIONS_PER_RUN (rest queued)
          → discord_notifier.notify_jobs_batch()
          → mark seen (id + normalised URL)
  → save seen_jobs.json + queued_jobs.json → git commit [skip ci]
```

### Key design decisions

**Scrape once, fan out**: all scrapers run once per cycle; filtering is per-channel afterwards.

**Shared HTTP layer** (`scrapers/fetch.py`): one client factory (redirects on, gzip only — brotli crashed on large Ashby payloads), retries with backoff on timeouts/5xx/429, error messages that always name the exception type. Every scraper goes through it. `REQUEST_TIMEOUT` default is 25s because 10s made Databricks/Figma time out every run.

**Word-boundary matching** (`scrapers/base.py`): alphanumeric boundaries so `intern` ≠ `internal`, `vp` ≠ `mvp`, `sr` matches `Sr.`. Exclusions are ignored when the excluded word sits inside a matched positive phrase (`manager` inside `product manager`).

**No bare `engineer` / `developer` positives** (2026-09-11): the full-time channel matches role *phrases* only (`software engineer`, `backend`, `machine learning`, `engineer, software`, `engineer graduate`, …). Bare words let 60%+ noise through from zapply ("Vibration Analyst", "Space Systems Engineer", "Salesforce Developer"). Exclusions also veto security/clearance/QA/analyst/enterprise-IT titles, and internships on both channels. `excluded_companies` (Microsoft, Uber, Meta by default) is a whole-word match on the company name, checked before keywords.

**AND keyword logic**: a channel that mixes early-career keywords (`intern`, `new grad`) with role keywords must match both. Neither built-in channel does this any more — the full-time channel is role-keyword only, so "Software Engineer" with no level tag matches (by design: most entry roles aren't tagged).

**Location filter with foreign veto**: positives as before (short tokens whole-word, longer substrings). `excluded_locations` (Canada, UK, India, ... whole-word) veto a job unless a *strong* positive also matched; `remote`, `ca`, `wa` are weak. So "Remote - Canada" and "Toronto, ON, CA" are rejected, "Remote - US or Canada" and "London / New York, NY" pass. Work-model-only locations ("Hybrid", "N/A", "3 Locations") count as unknown and pass.

**Greenhouse `content=true`**: gives `offices`, the only real location for boards that put "Hybrid" in `location.name` (Cloudflare), and `first_published` is used instead of `updated_at` (edits used to re-surface old jobs).

**Ashby dates**: the API field is `publishedAt`. The old code read `publishedDate` (doesn't exist), so every Ashby job was undated. Some boards (Snowflake) genuinely have no date → "Unknown" → kept by the recency filter → sorted last before the cap.

**Date-only sources**: jobright, Amazon, Workday, ApplyGuy, gradtracker only know the posting *date* and emit `YYYY-MM-DD`. `filter_recent_jobs` grants those +24h so a job posted at 23:00 isn't dropped forever by the next morning's run.

**Jev second-pass judge** (`jev.py`, added 2026-09-17, off by default): a calibrated classifier
(typesafe.ai) that scores the jobs surviving the regex filters. Three independent switches —
`JEV_ENABLED` / `JEV_RANKING` / `JEV_ENFORCE` — because they are the rollout ladder, each revertable
by flipping a GitHub variable with no deploy. Four invariants, none of which should be relaxed
without re-running `jev_eval.py`:

- *Second pass only.* Jev sees only what the regex accepted and can reject or reorder, never
  promote. Titles from HN and the aggregators are attacker controlled and Jev does not treat state
  as hostile, so this caps the blast radius of prompt injection at "ranks higher within an already
  qualifying set". Letting Jev overrule a regex *rejection* would make it a real pollution vector.
- *Fail open.* Every error path yields `jev.UNJUDGED`, whose `keep` is True. Same convention as
  `run_bulk` / `fetch_company` returning `[]`. An outage must never silence the bot.
- *Ranking is invisible when unjudged.* `_rank_for_notification(jobs, all_unjudged)` reproduces
  `_newest_first` exactly; `tests/test_jev.py` locks this in. Fit is bucketed (`JEV_FIT_BUCKET`)
  rather than sorted raw so a marginally-better older job cannot outrank a fresher one.
- *Judge after the seen-filter, not before.* Judging `matching` instead of the unseen subset costs
  ~100x the calls for identical user-visible behaviour.

Confidence is applied **per dimension**, never as a `min()` across answers — a `min()` let one
uncertain answer veto every other confident rejection. Jev is never asked anything date- or
count-shaped (documented weakness); recency stays with `filter_recent_jobs`. The model is pinned to
`jev-1.13.0`, not `jev-latest`, because the thresholds are tuned against it.

**Bounded promotion** (`JEV_PROMOTE`, off by default) is the one exception to "second pass only".
It judges jobs the *keyword* filter rejected (company and location filters still apply) and admits
them only when a channel is under its cap, capped at `JEV_PROMOTE_MAX_CALLS`, and only on a
positive match: accepted `role_family` + non-senior + confidence + `fit >= JEV_PROMOTE_MIN_FIT`.
An ordinary `keep` is insufficient — see `jev.should_promote`. Promoted jobs are flagged in the
Discord embed so an admission is never silent.

A **full first pass** was measured with `jev_eval.py --first-pass` and rejected: 918 calls/run
(~$30/mo, ~95s/run vs ~15s), and it removes the structural bound on prompt injection — under a
second pass Jev can only reject or reorder, so a malicious title cannot enter the channel. It also
removes the safety net: a `qa_test` criteria bug during development would have pushed 8 QA roles
straight to Discord under first pass, and was harmless under second pass because the regex already
rejected them. The experiment did surface real keyword gaps, which were fixed in
`DEFAULT_SWE_FULL_TIME_KEYWORDS` for free (level-suffix titles like "Software Engineer I",
"Junior Developer", plus retrieval/ML-infra phrases).

Phase 0 measurements (three scrape windows, 2026-09-17): 0/28 regex-accepted golden titles wrongly
dropped; explicit new-grad roles filling the 25-cap went 2-3 → 25 under fit ranking; ~300 calls,
~15s, ~$0.017 per run.

**Work-authorization filtering** (2026-09-17): the bot targets an international candidate, so US
defense primes, federal-services integrators and ITAR employers are excluded by default via
`config.DEFAULT_NON_SPONSORING_COMPANIES` (41 whole-word company names) plus
`companies.US_ONLY_COMPANIES` (3 ATS boards). `INCLUDE_NON_SPONSORING_COMPANIES=true` restores both.
The company-name half matters more than the ATS half: these employers arrive mostly from the
aggregator feeds, not from `companies.py`, so an ATS-list split alone would not filter them.
Measured at 23/214 (11%) of swe-channel postings. Note this is NOT a sponsorship database —
per-company H-1B status lives in DOL LCA filings and is unknowable from a job board API; the lists
cover structural US-person requirements only. `config.effective_excluded_companies()` is the single
place that combines base + non-sponsoring exclusions; channel defaults call it.

**Dedupe**: within a run, jobs sharing a normalised URL *or* the same company|title|location collapse to the highest-priority source (`PLATFORM_DEDUPE_PRIORITY`). Persisted seen-state uses only id + URL, because big employers re-post the same title/location as genuinely new reqs.

**Per-channel seen-state**: `seen_jobs.json["channels"][name]`. Channels no longer configured are pruned on normal runs (never on `--init`). Only successfully posted jobs are marked seen; capped ones are queued (6h TTL) and marked seen.

**Markdown tables are header-driven** (`scrapers/markdown_table.py`): columns are located by header name, so speedyapply's 5- and 6-column tables, jobright's link-in-title cells, and zapply's plain-text company cells all parse. The old fixed-index parser dropped 75% of speedyapply rows.

### Channel configuration priority

`config.load_channels()`:
1. `PM_WEBHOOK_URL` / `FULL_TIME_WEBHOOK_URL` → built-in channels with `config.py` defaults
2. `CHANNELS_JSON` env / `channels.json` → add channels or override a built-in one by name (supports `excluded_locations`, `excluded_companies`)
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

GitHub's `schedule` trigger is best-effort. The workflow asks for every 30 minutes (`7,37` UTC, quiet 12am–6am Eastern). Measured gaps on this repo are often a few hours, not 30 minutes. `RECENT_POSTING_MAX_AGE_HOURS=24` means a late run still catches the posting.

`SEND_NO_NEW_SUMMARY` is currently `true` as a repo variable; that posts a "no new jobs" embed to every channel on every run. Set it to `false`.

## GitHub Actions

`.github/workflows/scraper.yml`: cron at minutes `7,37` (every 30 min, hours `0-3,10-23` UTC), `workflow_dispatch` with a `mode` input (`normal` | `init`), `timeout-minutes: 15`, single concurrency group. Secrets: `PM_WEBHOOK_URL`, `FULL_TIME_WEBHOOK_URL` (`CHANNELS_JSON`, `JEV_API_KEY` optional). Non-secret tuning goes in Variables (`RECENT_POSTING_MAX_AGE_HOURS`, `ATS_CONCURRENCY`, `SEND_NO_NEW_SUMMARY`, `REQUEST_TIMEOUT`, source URL overrides, `JEV_ENABLED`, `JEV_RANKING`, `JEV_ENFORCE`).

`JEV_ENFORCE` is the only one that can delete a job from a channel. Do not enable it without a week of `[JEV] would drop:` shadow logs and a clean `python jev_eval.py --golden` (0 regression risk). Rollback is setting the variable back to `false`; dropped jobs are never marked seen, so they return as candidates on the next run. See README § "Jev second-pass judge".
