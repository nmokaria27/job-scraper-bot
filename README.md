# Job Scraper Discord Bot

Scrapes ~30,000 job postings per run from ~110 company ATS boards (Greenhouse, Lever, Ashby), direct big-tech feeds (Amazon, NVIDIA/Salesforce/Adobe/Capital One/Intel via Workday), curated new-grad GitHub lists (SimplifyJobs, vanshb03, speedyapply, jobright-ai, zapplyjobs, ApplyGuy, new-grad-2027-tracker) and Hacker News "Who is Hiring?", then posts new entry-level matches to Discord. Two channels are built in:

| Channel | Secret | What it catches |
|---|---|---|
| `pm-jobs` | `PM_WEBHOOK_URL` | Product Manager, APM, TPM, Product Owner, Product Analyst, PM internships (not Group/Sr/Director) |
| `swe-ai-full-time` | `FULL_TIME_WEBHOOK_URL` | New-grad / entry-level Software, AI/ML, Research, Data Science, SRE, MTS (no interns, no seniors, no sales/support/hardware "engineers") |

Runs entirely on GitHub Actions — no server, no database, no cost. A full run takes about 45 seconds.

## How it works

1. `main.py` fetches every source once (`scrape_all_raw`). Each source is isolated: if one API breaks, the run loses that source, not everything.
2. Jobs older than `RECENT_POSTING_MAX_AGE_HOURS` (24) are dropped. Sources that only know the posting *date* get an extra day of grace.
3. Each channel applies its keyword, exclusion, location and foreign-location filters.
4. Duplicates across sources (same URL, or same company+title+location) collapse to the source closest to the employer.
5. *(optional)* The **Jev judge** (`jev.py`) scores the jobs that survived, so the 25-slot cap posts the best matches rather than merely the newest. Off by default; see [Jev second-pass judge](#jev-second-pass-judge).
6. Jobs not yet sent to that channel are posted best-first, capped at 25 per run; the rest are queued and flushed on quiet runs.
7. `seen_jobs.json` is committed back to the repo so state persists.

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
| `CHANNELS_JSON` | *(optional)* JSON list to add channels or override a built-in one by name — see `channels.json.example` |
| `EXCLUDED_COMPANIES` | *(optional)* comma-separated employers to never notify in single-channel mode (default: Microsoft, Uber, Meta) |
| `DISCORD_WEBHOOK_URL` | *(optional)* single-channel fallback if neither of the above is set |
| `JEV_API_KEY` | *(optional)* typesafe.ai key for the Jev judge — [console.typesafe.ai](https://console.typesafe.ai/settings/keys) |

Optional repository **Variables** (not secrets):

| Variable | Default | Purpose |
|---|---|---|
| `RECENT_POSTING_MAX_AGE_HOURS` | `24` | Posting freshness window |
| `SEND_NO_NEW_SUMMARY` | `false` | Post a "no new jobs" embed on quiet runs — leave off, it's noise |
| `ATS_CONCURRENCY` / `REQUEST_TIMEOUT` | `8` / `25` | Board fetch parallelism and per-request timeout |
| `SIMPLIFY_URLS`, `SPEEDYAPPLY_URLS`, `JOBRIGHT_URLS`, `ZAPPLY_URLS`, `JSON_SOURCE_URLS`, `WORKDAY_TENANTS` | see `config.py` | Override source lists (e.g. when a repo rolls to a new year) |
| `RUN_COMPANY_DISCOVERY`, `DISCOVERY_SOURCE_URLS`, `INCLUDE_DISCOVERED_COMPANIES` | — | Auto-discover extra ATS slugs |
| `JEV_ENABLED` | `false` | Master switch for the Jev judge |
| `JEV_RANKING` | `false` | Let Jev's fit score reorder the cap (changes order, never membership) |
| `JEV_ENFORCE` | `false` | Let Jev actually drop jobs — read the section below before enabling |
| `JEV_PROMOTE` | `false` | Let Jev surface jobs the keyword filter rejected (bounded; see below) |
| `INCLUDE_NON_SPONSORING_COMPANIES` | `false` | Include US-person-only defense/federal employers |
| `JEV_FIT_FLOOR`, `JEV_CONCURRENCY`, `JEV_MAX_CALLS_PER_RUN`, `JEV_RUN_DEADLINE_SECONDS`, `JEV_MODEL` | see `config.py` | Judge tuning |

### 4. Enable Actions write permission

Settings → Actions → General → Workflow permissions → **Read and write permissions**.

### 5. Seed the state (no notifications)

Actions → **Job Scraper** → **Run workflow** → mode **`init`**. This marks every *current* match as seen so the first real run doesn't flood the channels. Do this again whenever you add sources or channels.

### 6. Reliable 15-minute scheduling (important)

GitHub's cron is best-effort. On this repo the last 100 scheduled runs were a **median of 104 minutes apart** (max 12 hours). Nothing is lost — the 24 h window catches everything — but "as soon as it opens" needs an external trigger:

1. Create a fine-grained GitHub token with **Actions: Read and write** on this repo.
2. On [cron-job.org](https://cron-job.org) (free) create a job every 15 minutes:
   - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/scraper.yml/dispatches`
   - Method: `POST`, body: `{"ref":"main"}`
   - Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`
3. Check Actions: runs should now show event `workflow_dispatch` every 15 minutes. The single concurrency group prevents overlaps with the GitHub cron.

---

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in webhooks (or leave for dry runs)

python -m pytest tests/ -q      # 67 unit tests, no network

python test_run.py --channels   # DRY RUN: prints what each channel would post right now
python test_run.py --channels --hours 72
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
common but **not guaranteed**. Verify before applying. Jev also asks a `requires_clearance` question
per posting, which catches cleared roles at employers not on the list.

---

## Jev second-pass judge

[Jev](https://docs.typesafe.ai/introduction) is a calibrated classifier — Choice / Score / Noul,
no free text — used as a **second pass** after the regex filters. It only ever sees jobs the regex
already accepted, and it can only reject or reorder them. It can never promote a job the regex
rejected. That bound is deliberate: job titles from HN and the aggregator lists are attacker
controlled, so the worst a malicious title can do is rank itself higher inside an already-qualifying set.

Everything fails open. No key, no network, a 429, a timeout, a blown budget — all produce an
"unjudged" verdict that keeps the job. A Jev outage degrades to exactly the previous behaviour.

### Why it exists

The cap is 25 notifications per run, but the SWE channel regularly has 100+ new matches. Those 25
used to be chosen by **recency alone** — there was no relevance signal anywhere in the codebase.

Measured on three real scrape windows (2026-09-17, `jev_eval.py`):

| | recency (before) | fit (after) |
|---|---|---|
| Explicitly new-grad roles in the posted 25 | **2–3** | **25** |
| Mean fit of the posted 25 | 0.55 | 0.90 |
| Regex-accepted jobs wrongly dropped | — | **0 / 28** |

Cost is negligible: ~300 calls per run, ~15s wall clock, **~$0.017**. Output tokens are free.

### Turning it on (ranking only — recommended)

Add repository **Secret** `JEV_API_KEY`, then set **Variables**:

```
JEV_ENABLED = true
JEV_RANKING = true
JEV_ENFORCE = false
```

This is close to risk-free: ranking changes the *order* jobs are posted in, never which jobs
qualify. If Jev is down you get the old recency order. `tests/test_jev.py` locks that invariant in.

### Turning on enforcement (`JEV_ENFORCE`)

Enforcement lets Jev **delete** jobs from the channel. A wrong drop is a job you never see and
never learn about, so treat it as a one-way door and gate it on evidence.

**Step 1 — collect shadow data.** With `JEV_ENABLED=true` and `JEV_ENFORCE=false`, every run
already logs what it *would* have dropped:

```
[JEV] 'swe-ai-full-time': judged 41/41, would reject 3, mean fit 0.71
[JEV] would drop: Software Engineer - E2 @ Lockheed Martin (seniority=mid_level, fit=0.41)
```

Read those lines from Actions → Job Scraper → any run. Give it about a week.

**Step 2 — check the gate.** Only proceed if all three hold:

- Every `would drop` line over that week is a job you genuinely don't want.
- `python jev_eval.py --golden` still reports **0** regression risk.
- No `[JEV] circuit tripped` or repeated error lines.

**Step 3 — flip it.** Settings → Secrets and variables → Actions → **Variables** tab → `JEV_ENFORCE` → `true`.
(New variable? **New repository variable**, name `JEV_ENFORCE`, value `true`.)

Takes effect on the next scheduled run; no deploy, no code change.

**Step 4 — verify.** The next run logs the real drops:

```
[JEV] 'swe-ai-full-time': dropped 3 of 41
```

**To roll back**, set `JEV_ENFORCE` back to `false`. Ranking keeps working. Nothing is lost
permanently: a dropped job is never marked seen, so it returns as a candidate on the next run.

Expect enforcement to remove roughly **4–10%** of what the regex accepts — mostly mid-level titles
the keyword hacks miss (`Software Engineer - E2`, `Intermediate Software Engineer`), hardware roles,
and SRE/IT work.

### Bounded promotion (`JEV_PROMOTE`)

The keyword filter is deliberately strict — it matches role *phrases*, never bare `engineer` or
`developer`, because bare words let 60%+ noise through. The cost is that some genuinely good titles
are invisible to it, e.g. `Software Engineer I, Network` (blocked by the `network` exclusion, which
is right for `Network Engineer` and wrong here) or `Junior Programer` (typo).

Promotion is a narrow, guarded path for exactly those. It is **not** a first pass — a full first
pass was measured and rejected (see below). Promotion only runs:

- when a channel is **under** its 25-job cap, which is the only time extra candidates change what
  you see, and which keeps call volume small;
- on at most `JEV_PROMOTE_MAX_CALLS` (150) regex-rejected jobs, freshest first;
- admitting only a **positive** match: an accepted `role_family`, non-senior, confidence above
  `JEV_MIN_CONFIDENCE`, and `fit >= JEV_PROMOTE_MIN_FIT` (0.75). An ordinary "keep" is not enough.

Company and location filters still apply — those are preference and geographic fact, and Jev is
documented as weak at that class of judgement.

Promoted jobs are posted with a ✨ and a footer saying `surfaced by Jev (outside keyword filters)`,
so an admission is never silent. Turn it on with the repo variable `JEV_PROMOTE = true`.

**Why this is bounded rather than a full first pass.** Titles from HN and the aggregator lists are
attacker controlled, and Jev does not treat state as hostile. Under a second pass Jev can only
reject or reorder, so an injected title's worst case is ranking higher inside an already-qualifying
set. Promotion is the one path that can admit, so it demands several independent constrained
classifiers agree, caps the volume, and labels the result. A full first pass was measured at
~918 calls/run (~$30/mo, ~95s/run) and would have admitted 8 QA roles that a criteria bug briefly
let through — the regex caught them. `python jev_eval.py --first-pass` reproduces that experiment.

### Re-tuning

`jev_eval.py` is the harness. It never touches the run path and writes nothing.

```bash
python jev_eval.py --golden --sweep          # vs the labelled corpus in tests/, + threshold sweep
python jev_eval.py --live --hours 24         # real scrape: what Jev would drop and how it ranks
python jev_eval.py --live --save-corpus /tmp/c.json    # cache the scrape
python jev_eval.py --live --from-file /tmp/c.json      # re-judge without scraping again
```

`--golden` scores only the **regex-accepted** titles, because those are the only ones Jev sees in
production. Ranking runs through `main._rank_for_notification`, the same function production uses,
so the harness cannot drift from the bot.

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
├── jev.py                         # Jev second-pass judge (off by default)
├── jev_eval.py                    # Jev evaluation harness (--golden, --live)
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
