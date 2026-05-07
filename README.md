# Job Scraper Discord Bot

Scrapes job postings from Greenhouse, Lever, and Ashby ATS platforms every 30 minutes and sends rich Discord notifications for new matches. Runs entirely on GitHub Actions — no server, no database, no cost.

## How it works

1. GitHub Actions triggers `main.py` on a cron every 30 minutes
2. The scraper checks every company in `companies.py` across all three platforms
3. Jobs are filtered by your keyword list (with optional exclusions and location filter)
4. New job IDs are compared against `seen_jobs.json` — duplicates are skipped
5. New matches are posted to your Discord channel via webhook
6. `seen_jobs.json` is committed back to the repo to persist state

---

## Setup (under 5 minutes)

### 1. Fork or clone this repo to a new **public** GitHub repository

> **Why public?** GitHub Actions gives unlimited free minutes for public repos, but only 500 min/month for private. At 30-min intervals, a private repo would exhaust that in ~2 weeks.

```bash
git clone <your-repo-url>
cd job-scraper-bot
```

### 2. Create a Discord webhook

1. Open your Discord server → **Server Settings** → **Integrations** → **Webhooks**
2. Click **New Webhook**, give it a name, choose a channel
3. Click **Copy Webhook URL** — you'll need this in the next step

### 3. Configure local environment (for testing)

```bash
cp .env.example .env
# Edit .env and paste your Discord webhook URL
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
| `DISCORD_WEBHOOK_URL` | Your webhook URL from step 2 |
| `KEYWORDS` | *(optional)* Comma-separated keywords (leave blank for defaults) |
| `EXCLUDED_KEYWORDS` | *(optional)* Comma-separated words to exclude from titles |
| `LOCATIONS` | *(optional)* Comma-separated location substrings (blank = all locations) |
| `MAX_NOTIFICATIONS_PER_RUN` | *(optional)* Default: `25` |

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

### Change keywords

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
├── config.py                      # Env var loading + defaults
├── discord_notifier.py            # Discord webhook integration
├── main.py                        # Orchestrator (--init flag for first run)
├── test_run.py                    # Standalone scraper verification script
├── seen_jobs.json                 # Persisted deduplication state (committed by Actions)
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
