"""
Standalone scraper verification script.

Runs scrapers and prints fetch counts, bypassing keyword/location filters.
Does NOT send Discord notifications and does NOT write to seen_jobs.json.

Usage:
  python test_run.py                       # ATS platforms (Greenhouse, Lever, Ashby)
  python test_run.py --hours 24            # only jobs posted in the last 24 hours
  python test_run.py --source simplify     # one bulk source (see SOURCES below)
  python test_run.py --source amazon --hours 48
  python test_run.py --channels            # DRY RUN: what each channel would notify
  python test_run.py --channels --hours 72 # ...over a wider window

Legacy flags --simplify / --speedyapply / --jobright still work.

--channels is the tool to use when tuning keywords: it runs the full scrape,
applies the built-in PM + full-time channel filters (or your configured
channels if webhooks are set), dedupes, and prints every would-be
notification grouped by channel. No seen-state is consulted, so you see the
complete picture for the window.
"""

import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import config
from companies import get_companies
from scrapers.ashby import AshbyScraper
from scrapers.base import Job
from scrapers.bigtech import AmazonScraper, WorkdayScraper
from scrapers.greenhouse import GreenhouseScraper
from scrapers.hackernews import HackerNewsScraper
from scrapers.json_sources import JsonSourceScraper
from scrapers.lever import LeverScraper
from scrapers.markdown_table import JobRightScraper, SpeedyApplyScraper, ZapplyScraper
from scrapers.simplify import SimplifyScraper

SOURCES = {
    "simplify": SimplifyScraper,
    "speedyapply": SpeedyApplyScraper,
    "jobright": JobRightScraper,
    "zapply": ZapplyScraper,
    "jsonsource": JsonSourceScraper,
    "amazon": AmazonScraper,
    "workday": WorkdayScraper,
    "hackernews": HackerNewsScraper,
}


def _arg_value(flag: str) -> str | None:
    try:
        idx = sys.argv.index(flag)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return None


def _parse_hours() -> int | None:
    raw = _arg_value("--hours")
    try:
        return int(raw) if raw else None
    except ValueError:
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


def _print_summary(jobs: list[Job]) -> None:
    if not jobs:
        print("\n[SUMMARY] No jobs found.")
        return

    by_platform = Counter(job.platform for job in jobs)
    by_company = Counter(job.company for job in jobs)
    print("\n[SUMMARY] Jobs by platform:")
    for platform, count in sorted(by_platform.items()):
        print(f"  - {platform}: {count}")
    print("[SUMMARY] Top companies:")
    for company, count in by_company.most_common(10):
        print(f"  - {company}: {count}")


async def test_source(name: str, hours: int | None) -> None:
    """Fetch one bulk source and print what it returned."""
    scraper_cls = SOURCES[name]
    print("=" * 60)
    print(f"{name} Test Run")
    note = f" (last {hours}h)" if hours else " (showing first 10)"
    print(f"Fetching job listings{note}...")
    print("No Discord notifications. No file writes.")
    print("=" * 60)

    jobs = await scraper_cls().fetch_jobs()

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
    raw_by_platform: dict[str, list[Job]] = {}
    companies = get_companies()

    platform_targets = {
        "greenhouse": companies.get("greenhouse", []),
        "lever": companies.get("lever", []),
        "ashby": companies.get("ashby", []),
    }

    for platform, slugs in platform_targets.items():
        if not slugs:
            continue
        print(f"\n[{platform.title()}] Testing {len(slugs)} configured company board(s)")

        scraper = {
            "greenhouse": GreenhouseScraper(),
            "lever": LeverScraper(),
            "ashby": AshbyScraper(),
        }[platform]

        for slug in slugs:
            jobs = await scraper.fetch_jobs(slug)
            raw_by_platform.setdefault(platform, []).extend(jobs)
            all_jobs.extend(jobs)

    print("\n[RAW COUNTS]")
    for platform in ("greenhouse", "lever", "ashby"):
        print(f"  - {platform}: {len(raw_by_platform.get(platform, []))}")

    if hours and all_jobs:
        before = len(all_jobs)
        all_jobs = _filter_by_recency(all_jobs, hours)
        print(f"\n  → {len(all_jobs)} of {before} jobs posted in last {hours}h")

    _print_summary(all_jobs)
    _print_jobs(all_jobs)


def _dry_run_channels() -> list[config.ChannelConfig]:
    """Configured channels if any webhooks are set, else the built-in defaults."""
    channels = config.load_channels(require_webhooks=False)
    if channels and not (len(channels) == 1 and channels[0].name == "default"):
        return channels
    return [
        config.ChannelConfig(
            name="pm-jobs",
            webhook_url="",
            keywords=config.DEFAULT_PM_KEYWORDS,
            excluded_keywords=config.DEFAULT_PM_EXCLUDED_KEYWORDS,
            locations=config.DEFAULT_LOCATIONS,
            excluded_locations=config.DEFAULT_EXCLUDED_LOCATIONS,
        ),
        config.ChannelConfig(
            name="swe-ai-full-time",
            webhook_url="",
            keywords=config.DEFAULT_SWE_FULL_TIME_KEYWORDS,
            excluded_keywords=config.DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS,
            locations=config.DEFAULT_LOCATIONS,
            excluded_locations=config.DEFAULT_EXCLUDED_LOCATIONS,
        ),
    ]


async def test_channels(hours: int | None) -> None:
    """Full scrape + per-channel filtering, printed instead of posted."""
    import main  # local import: main pulls in every scraper

    window = hours or config.RECENT_POSTING_MAX_AGE_HOURS
    config.RECENT_POSTING_MAX_AGE_HOURS = window
    channels = _dry_run_channels()

    print("=" * 60)
    print("Channel DRY RUN")
    print(f"Window: last {window}h | Channels: {[ch.name for ch in channels]}")
    print("No Discord notifications. No file writes. Seen-state ignored.")
    print("=" * 60)

    all_jobs, total = await main.scrape_all_raw()
    print(f"\n[INFO] Total postings fetched: {total}")
    recent = main.filter_recent_jobs(all_jobs)
    print("[INFO] Recent by platform:", dict(sorted(Counter(j.platform for j in recent).items())))

    for ch in channels:
        matches = main.dedupe_jobs_for_channel(ch.name, main.filter_for_channel(recent, ch))
        print(f"\n{'=' * 60}")
        print(f"[{ch.name}] {len(matches)} would-be notification(s)")
        print("=" * 60)
        for job in sorted(matches, key=lambda j: (j.platform, j.company.lower(), j.title.lower())):
            print(f"{job.platform:11} | {job.company[:28]:28} | {job.title[:80]:80} | {job.location[:45]}")


async def main_entry() -> None:
    hours = _parse_hours()
    source = _arg_value("--source")

    if "--channels" in sys.argv:
        await test_channels(hours)
        return

    if not source:
        for legacy in ("simplify", "speedyapply", "jobright", "zapply", "amazon", "workday", "hackernews"):
            if f"--{legacy}" in sys.argv:
                source = legacy
                break

    if source:
        if source not in SOURCES:
            print(f"Unknown source '{source}'. Choose from: {', '.join(SOURCES)}")
            sys.exit(2)
        await test_source(source, hours)
        return

    await test_ats(hours)


if __name__ == "__main__":
    asyncio.run(main_entry())
