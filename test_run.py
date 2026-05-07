"""
Standalone scraper verification script.

Runs scrapers and prints all jobs found, bypassing keyword/location filters.
Does NOT send Discord notifications and does NOT write to seen_jobs.json.

Usage:
  python test_run.py                    # Test Greenhouse (stripe, anthropic)
  python test_run.py --hours 12         # Only show jobs posted in last 12 hours
  python test_run.py --simplify         # Test SimplifyJobs (first 10 active jobs)
  python test_run.py --simplify --hours 48  # SimplifyJobs, last 48h (note: dates = original post date)

Tips:
  --hours filters on the job's posted_at timestamp (ISO string from each API).
  For SimplifyJobs, date_posted is when the COMPANY posted, not when SimplifyJobs
  indexed it — so filtering by a short window will return 0 for old listings.
  For Greenhouse/Lever, updated_at/createdAt are live and recent hits are expected.

Add or change TEST_* lists below to test different platforms/slugs.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from scrapers.ashby import AshbyScraper
from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.simplify import SimplifyScraper
from scrapers.hackernews import HackerNewsScraper

# --- Configurable test targets ---
GREENHOUSE_TEST: list[str] = ["stripe", "anthropic"]
ASHBY_TEST: list[str] = []       # All current slugs are 404; add verified ones here
LEVER_TEST: list[str] = ["anyscale"]


def _parse_hours() -> int | None:
    """Return the value of --hours N if present, else None."""
    try:
        idx = sys.argv.index("--hours")
        return int(sys.argv[idx + 1])
    except (ValueError, IndexError):
        return None


def _filter_by_recency(jobs: list[Job], hours: int) -> list[Job]:
    """Return only jobs whose posted_at is within the last N hours."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    recent: list[Job] = []
    skipped = 0
    for job in jobs:
        try:
            dt = datetime.fromisoformat(job.posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                recent.append(job)
        except (ValueError, TypeError):
            skipped += 1  # unparseable date — omit from recency check
    if skipped:
        print(f"  [INFO] {skipped} jobs had unparseable dates and were excluded from recency filter")
    return recent


def _print_jobs(jobs: list[Job], limit: int | None = None) -> None:
    displayed = jobs[:limit] if limit else jobs
    extra = f" (of {len(jobs)} total)" if limit and len(jobs) > limit else ""
    print(f"\n{'=' * 60}")
    print(f"Jobs shown: {len(displayed)}{extra}")
    print("=" * 60)
    for i, job in enumerate(displayed, 1):
        print(f"\n[{i}] {job.title}")
        print(f"     Company:  {job.company}")
        print(f"     Platform: {job.platform}")
        print(f"     Location: {job.location}")
        print(f"     Posted:   {job.posted_at}")
        print(f"     URL:      {job.url}")
        print(f"     ID:       {job.id}")


async def test_simplify(hours: int | None) -> None:
    """Test SimplifyJobs — fetch all active jobs and optionally filter by recency."""
    print("=" * 60)
    print("SimplifyJobs Test Run")
    note = f" (last {hours}h)" if hours else " (showing first 10 of all active)"
    print(f"Fetching all active listings{note}...")
    print("NOTE: date_posted = original company post date, not when SimplifyJobs indexed it.")
    print("No Discord notifications. No file writes.")
    print("=" * 60)

    scraper = SimplifyScraper()
    jobs = await scraper.fetch_jobs()

    if hours:
        before = len(jobs)
        jobs = _filter_by_recency(jobs, hours)
        print(f"  → {len(jobs)} of {before} jobs posted in last {hours}h")
        _print_jobs(jobs)
    else:
        _print_jobs(jobs, limit=10)


async def test_ats(hours: int | None) -> None:
    """Test ATS scrapers (Greenhouse, Lever, Ashby) with optional recency filter."""
    print("=" * 60)
    print("ATS Scraper Test Run")
    if hours:
        print(f"Showing only jobs posted in last {hours} hours.")
    print("No Discord notifications. No file writes.")
    print("=" * 60)

    all_jobs: list[Job] = []

    # --- Greenhouse ---
    if GREENHOUSE_TEST:
        print(f"\n[Greenhouse] Testing: {GREENHOUSE_TEST}")
        scraper = GreenhouseScraper()
        for slug in GREENHOUSE_TEST:
            jobs = await scraper.fetch_jobs(slug)
            all_jobs.extend(jobs)

    # --- Lever ---
    if LEVER_TEST:
        print(f"\n[Lever] Testing: {LEVER_TEST}")
        scraper = LeverScraper()
        for slug in LEVER_TEST:
            jobs = await scraper.fetch_jobs(slug)
            all_jobs.extend(jobs)

    # --- Ashby ---
    if ASHBY_TEST:
        print(f"\n[Ashby] Testing: {ASHBY_TEST}")
        scraper = AshbyScraper()
        for slug in ASHBY_TEST:
            jobs = await scraper.fetch_jobs(slug)
            all_jobs.extend(jobs)

    if hours and all_jobs:
        before = len(all_jobs)
        all_jobs = _filter_by_recency(all_jobs, hours)
        print(f"\n  → {len(all_jobs)} of {before} jobs posted in last {hours}h")

    _print_jobs(all_jobs)


async def main() -> None:
    hours = _parse_hours()
    if "--simplify" in sys.argv:
        await test_simplify(hours)
    else:
        await test_ats(hours)


if __name__ == "__main__":
    asyncio.run(main())
