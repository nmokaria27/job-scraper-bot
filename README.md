# Job Scraper Discord Bot

Scrapes job postings from Greenhouse, Lever, Ashby, SimplifyJobs, and Hacker News every 15 minutes (off-peak schedule) and sends rich Discord notifications for new matches. Supports one Discord channel or multiple channels with separate keyword/location filters. Runs entirely on GitHub Actions — no server, no database, no cost.

## How it works

1. GitHub Actions triggers `main.py` on a cron every 15 minutes (off-peak minutes)
2. The scraper checks every configured source in `companies.py`
3. Jobs are filtered for each configured Discord channel
4. New job IDs are compared against `seen_jobs.json` per channel — duplicates are skipped
5. New matches are posted to the matching Discord channel webhook
6. `seen_jobs.json` is committed back to the repo to persist state

---

## Setup (under 5 minutes)

### 1. Fork or clone this repo to a new **public** GitHub repository

> **Why public?** GitHub Actions gives unlimited free minutes for public repos, but only 500 min/month for private. At 15-min intervals, a private repo can exhaust that quickly (often around ~1 week, depending on workflow runtime).

```bash
git clone <your-repo-url>
cd job-scraper-bot
```

### 2. Create Discord webhook(s)

1. Open your Discord server → **Server Settings** → **Integrations** → **Webhooks**
2. Click **New Webhook**, give it a name, choose a channel
3. Click **Copy Webhook URL** — repeat this for each channel you want to notify

### 3. Configure local environment (for testing)

```bash
cp .env.example .env
# Edit .env and paste either DISCORD_WEBHOOK_URL or CHANNELS_JSON
```

For local multi-channel config, copy the example:

```bash
cp channels.json.example channels.json
# Edit channels.json with your channel names, webhooks, and filters
```

### 4. Install dependencies and run the initialization

```bash
pip install -r requirements.txt

# Seed seen_jobs.json — marks all CURRENT jobs as seen without notifying.
# This prevents a flood of notifications on the first real run.
python main.py --init

# Verify the scrapers work
python test_run.py
```

### 5. Push to GitHub

```bash
git add .
git commit -m "Initial setup"
git push origin main
```

### 6. Add GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret name | Value |
|---|---|
| `SWE_WEBHOOK_URL` | SWE Discord channel webhook URL |
| `PM_WEBHOOK_URL` | PM Discord channel webhook URL |
| `CHANNELS_JSON` | *(advanced override)* Custom channel JSON; ignored if `SWE_WEBHOOK_URL` or `PM_WEBHOOK_URL` is set |
| `DISCORD_WEBHOOK_URL` | Single-channel fallback webhook URL |
| `KEYWORDS` | *(single-channel only, optional)* Comma-separated keywords |
| `EXCLUDED_KEYWORDS` | *(single-channel only, optional)* Comma-separated words to exclude from titles |
| `LOCATIONS` | *(single-channel only, optional)* Comma-separated location substrings |
| `MAX_NOTIFICATIONS_PER_RUN` | *(optional)* Default: `25` |

Optional non-secret repository variables can be set under **Settings** → **Secrets and variables** → **Actions** → **Variables**:

| Variable name | Value |
|---|---|
| `RECENT_POSTING_MAX_AGE_HOURS` | Default: `24`; only notify postings with recent timestamps |
| `ATS_CONCURRENCY` | Default: `5`; number of company job boards fetched concurrently |
| `SEND_NO_NEW_SUMMARY` | Default: `false`; set to `true` to send a summary when no new jobs are found |

### 7. Enable Actions write permissions

Go to **Settings** → **Actions** → **General** → scroll to **Workflow permissions** → select **Read and write permissions** → Save.

### 8. Test the full pipeline

Go to **Actions** tab → select **Job Scraper** → click **Run workflow**.

Check that:
- The workflow completes without errors
- A `chore: update seen_jobs [skip ci]` commit appears in your repo
- No Discord notifications were sent (since `--init` already seeded everything)

The cron takes over from here. New job postings will appear in Discord automatically.

---

## Customization

### Add or remove companies

Edit `companies.py`. Each entry is a company slug — the identifier used in the ATS URL:

| Platform | Job board URL pattern | Slug |
|---|---|---|
| Greenhouse | `https://boards.greenhouse.io/<slug>/jobs` | `<slug>` |
| Lever | `https://jobs.lever.co/<slug>` | `<slug>` |
| Ashby | `https://jobs.ashbyhq.com/<slug>` | `<slug>` |

```python
COMPANIES = {
    "greenhouse": ["stripe", "anthropic", "your-new-company"],
    ...
}
```

### Configure multiple Discord channels

For the built-in SWE and PM channels, set `SWE_WEBHOOK_URL` and
`PM_WEBHOOK_URL` as GitHub repository secrets. The bot will use the default
SWE/PM keywords, exclusions, and locations from `config.py`.

Use `channels.json` locally or `CHANNELS_JSON` in GitHub Actions only if you
need custom channel definitions. Each channel has its own webhook, keywords,
excluded keywords, and optional locations.

```json
[
  {
    "name": "swe-jobs",
    "webhook_url": "https://discord.com/api/webhooks/...",
    "keywords": ["intern", "new grad", "software engineer"],
    "excluded_keywords": ["senior", "staff", "manager"],
    "locations": []
  }
]
```

After adding channels to an existing repo, run `python main.py --init` once if you want to seed current matches without sending notifications.

The keyword matcher treats mixed early-career and role keywords as an AND filter:
titles must match both an early-career term such as `intern` or `new grad` and
a role term such as `software engineer` or `product manager`. This avoids broad
matches like `HR Intern`. Excluded terms still block titles, except when the
excluded word appears only inside a positive phrase, such as `manager` inside
`product manager`.

Location filters keep jobs with blank location fields, since many remote roles
do not populate structured location data. Short filters like `US`, `CA`, or
`NY` are matched as standalone tokens to avoid matching unrelated words.

Normal runs only notify jobs whose `posted_at` timestamp is within
`RECENT_POSTING_MAX_AGE_HOURS` hours. Greenhouse exposes `updated_at`, not a
true first-published timestamp, so recently updated listings may still appear.

### Change single-channel keywords

Update the `KEYWORDS` secret in GitHub (or `KEYWORDS` in your `.env` for local runs).
Comma-separated, case-insensitive substring match against job titles.

### Filter out senior roles

Update the `EXCLUDED_KEYWORDS` secret. Default excludes: `senior, staff, lead, director, principal, manager, vp, head of`.

### Filter by location

Set the `LOCATIONS` secret to a comma-separated list of location substrings, e.g., `remote,san francisco,new york`. Leave blank to see all locations.

### Trigger manually

Go to **Actions** tab → **Job Scraper** → **Run workflow** button.

---

## Directory structure

```
.
├── .github/workflows/scraper.yml  # GitHub Actions cron job
├── scrapers/
│   ├── base.py                    # Job dataclass + abstract BaseScraper
│   ├── greenhouse.py              # Greenhouse ATS scraper
│   ├── lever.py                   # Lever ATS scraper
│   └── ashby.py                   # Ashby ATS scraper
├── companies.py                   # Master list of companies to scrape
├── config.py                      # Env/channel loading + defaults
├── discord_notifier.py            # Discord webhook integration
├── main.py                        # Orchestrator (--init flag for first run)
├── test_run.py                    # Standalone scraper verification script
├── seen_jobs.json                 # Persisted deduplication state (committed by Actions)
├── channels.json.example          # Multi-channel config example
├── requirements.txt
├── .env.example
└── README.md
```

---

## Troubleshooting

**Workflow fails with permission error on `git push`**
→ Check that **Read and write permissions** is enabled under Settings → Actions → General → Workflow permissions.

**No notifications arriving**
→ Run `python test_run.py` locally to verify scrapers return jobs. Check that your `DISCORD_WEBHOOK_URL` secret is set correctly.

**Too many notifications on first run**
→ You skipped `python main.py --init`. Delete `seen_jobs.json`, run `--init` locally, push the updated file, then re-enable the cron.

**A company returns 0 jobs / 404 warnings**
→ The company may have moved to a different ATS, or the slug may be wrong. Check the company's current careers page URL.
