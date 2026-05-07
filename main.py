"""
Job Scraper — main orchestrator.

Usage:
  python main.py          # Normal run: scrape, filter, notify Discord
  python main.py --init   # Seed seen_jobs.json without sending any notifications
                          # Run this ONCE before enabling the cron to avoid a
                          # flood of notifications for all existing job postings.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import config
from companies import COMPANIES
from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.simplify import SimplifyScraper
from scrapers.hackernews import HackerNewsScraper
import discord_notifier


# ---------------------------------------------------------------------------
# seen_jobs.json helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def load_seen_jobs() -> dict:
    """
    Load seen_jobs.json. Handles missing file, empty file, and corrupt JSON.
    Migrates the legacy format ({"job_ids": [...]}) automatically.
    Returns a dict with key "jobs" (list of {"id", "seen_at"} dicts).
    """
    try:
        with open(config.SEEN_JOBS_PATH, "r") as f:
            data = json.load(f)

        # Migrate old format: {"job_ids": [...], ...}
        if "job_ids" in data and "jobs" not in data:
            now = _now_iso()
            data["jobs"] = [
                {"id": jid, "seen_at": now} for jid in data.pop("job_ids", [])
            ]

        # Ensure required keys exist
        data.setdefault("jobs", [])
        data.setdefault("last_run", _now_iso())
        data.setdefault("total_notified", 0)
        return data

    except FileNotFoundError:
        print("[INFO] seen_jobs.json not found — starting fresh.")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[WARN] seen_jobs.json is corrupt ({e}) — starting fresh.")

    return {"jobs": [], "last_run": _now_iso(), "total_notified": 0}


def prune_seen_jobs(data: dict) -> dict:
    """Remove job entries older than SEEN_JOBS_MAX_AGE_DAYS."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.SEEN_JOBS_MAX_AGE_DAYS)
    before = len(data["jobs"])
    data["jobs"] = [
        entry for entry in data["jobs"]
        if _parse_dt(entry.get("seen_at", "")) > cutoff
    ]
    pruned = before - len(data["jobs"])
    if pruned:
        print(f"[INFO] Pruned {pruned} stale job IDs from seen_jobs.json")
    return data


def _parse_dt(iso: str) -> datetime:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def save_seen_jobs(data: dict) -> None:
    with open(config.SEEN_JOBS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_seen_ids(data: dict) -> set[str]:
    return {entry["id"] for entry in data.get("jobs", [])}


def mark_seen(data: dict, job_id: str) -> None:
    data["jobs"].append({"id": job_id, "seen_at": _now_iso()})


# ---------------------------------------------------------------------------
# Core scraping logic
# ---------------------------------------------------------------------------

def _filter_jobs(scraper, jobs: list[Job]) -> list[Job]:
    """Apply keyword + excluded + location filters to a list of jobs."""
    return [
        job for job in jobs
        if job.title
        and scraper.matches_keywords(job.title, config.KEYWORDS, config.EXCLUDED_KEYWORDS)
        and scraper.matches_location(job.location, config.LOCATIONS)
    ]


async def scrape_all() -> tuple[list[Job], int]:
    """
    Run all scrapers across all sources.
    Bulk scrapers (simplify, hackernews) are called once.
    ATS scrapers (greenhouse, lever, ashby) are called per company slug.
    Returns (all_matching_jobs, total_postings_checked).
    """
    ats_scrapers = {
        "greenhouse": GreenhouseScraper(),
        "lever": LeverScraper(),
        "ashby": AshbyScraper(),
    }

    # Bulk scrapers are called once (not per-company)
    bulk_scrapers = {
        "simplify": SimplifyScraper(),
        "hackernews": HackerNewsScraper(),
    }

    all_jobs: list[Job] = []
    total_checked = 0

    # --- Bulk scrapers first ---
    for platform, scraper in bulk_scrapers.items():
        if platform not in COMPANIES:
            continue  # disabled — key not present in companies.py
        jobs = await scraper.fetch_jobs("")
        total_checked += len(jobs)
        all_jobs.extend(_filter_jobs(scraper, jobs))

    # --- ATS scrapers per company ---
    for platform, company_slugs in COMPANIES.items():
        if platform not in ats_scrapers:
            continue  # skip bulk-scraper marker keys
        scraper = ats_scrapers[platform]
        for slug in company_slugs:
            jobs = await scraper.fetch_jobs(slug)
            total_checked += len(jobs)
            all_jobs.extend(_filter_jobs(scraper, jobs))
            await asyncio.sleep(config.SLEEP_BETWEEN_COMPANIES)

    return all_jobs, total_checked


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main(init_mode: bool = False) -> None:
    if not init_mode:
        config.validate()  # Only require webhook URL in normal mode

    print(f"[INFO] Job Scraper starting — {'INIT MODE' if init_mode else 'normal run'}")
    print(f"[INFO] Keywords: {config.KEYWORDS}")
    print(f"[INFO] Excluded: {config.EXCLUDED_KEYWORDS}")
    if config.LOCATIONS:
        print(f"[INFO] Location filter: {config.LOCATIONS}")

    # Load + prune seen_jobs.json
    seen_data = load_seen_jobs()
    seen_data = prune_seen_jobs(seen_data)
    seen_ids = get_seen_ids(seen_data)

    # Scrape all platforms
    matching_jobs, total_checked = await scrape_all()

    # Identify truly new jobs (not in seen_ids)
    new_jobs: list[Job] = []
    for job in matching_jobs:
        if job.id not in seen_ids:
            new_jobs.append(job)

    print(f"\n[INFO] Total postings checked: {total_checked}")
    print(f"[INFO] Matching jobs (filtered): {len(matching_jobs)}")
    print(f"[INFO] New jobs (not yet seen): {len(new_jobs)}")

    # --- INIT MODE: seed seen_jobs.json without notifying ---
    if init_mode:
        # Mark ALL matching jobs as seen (no notifications)
        for job in matching_jobs:
            if job.id not in seen_ids:
                mark_seen(seen_data, job.id)

        seen_data["last_run"] = _now_iso()
        try:
            save_seen_jobs(seen_data)
            print(f"\n[INIT] Seeded {len(new_jobs)} job IDs into seen_jobs.json")
            print("[INIT] No Discord notifications sent. You're ready to run normally.")
        except OSError as e:
            print(f"[ERROR] Failed to save seen_jobs.json: {e}")
        return

    # --- NORMAL MODE ---
    if not new_jobs:
        print("[INFO] No new jobs found. Nothing to notify.")
        seen_data["last_run"] = _now_iso()
        try:
            save_seen_jobs(seen_data)
        except OSError as e:
            print(f"[ERROR] Failed to save seen_jobs.json: {e}")
        return

    # Apply per-run notification cap
    capped = len(new_jobs) > config.MAX_NOTIFICATIONS_PER_RUN
    jobs_to_notify = new_jobs[:config.MAX_NOTIFICATIONS_PER_RUN]
    jobs_to_mark_only = new_jobs[config.MAX_NOTIFICATIONS_PER_RUN:]  # mark seen, no notify

    if capped:
        print(f"[WARN] Capping notifications at {config.MAX_NOTIFICATIONS_PER_RUN} "
              f"(found {len(new_jobs)} new jobs)")

    # Always save seen_jobs.json even if Discord notifications fail
    try:
        # Mark all new jobs as seen (both notified and cap-overflow)
        for job in new_jobs:
            mark_seen(seen_data, job.id)
        seen_data["last_run"] = _now_iso()

        # Send Discord notifications
        notified_jobs = await discord_notifier.notify_jobs_batch(jobs_to_notify)

        # Update total_notified count
        seen_data["total_notified"] = seen_data.get("total_notified", 0) + len(notified_jobs)

        # Send summary embed
        await discord_notifier.send_summary(
            new_count=len(notified_jobs),
            total_checked=total_checked,
            capped=capped,
        )

        print(f"\n[INFO] Notified: {len(notified_jobs)} jobs")
        if capped:
            print(f"[INFO] Silently marked {len(jobs_to_mark_only)} additional jobs as seen")

    finally:
        try:
            save_seen_jobs(seen_data)
            print(f"[INFO] seen_jobs.json saved ({len(seen_data['jobs'])} total entries)")
        except OSError as e:
            print(f"[ERROR] Failed to save seen_jobs.json: {e}")


if __name__ == "__main__":
    init_mode = "--init" in sys.argv
    asyncio.run(main(init_mode=init_mode))
