"""
Job Scraper — main orchestrator (multi-channel).

Usage:
  python main.py          # Normal run: scrape, filter per channel, notify Discord
  python main.py --init   # Seed seen_jobs.json without sending any notifications
                          # Run this ONCE before enabling the cron to avoid a
                          # flood of notifications for all existing job postings.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import config
from config import ChannelConfig, load_channels
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
    Migrates legacy formats automatically.
    Returns a dict with:
      - "jobs": list of {"id", "seen_at"} (global dedup for --init)
      - "channels": {channel_name: [job_id, ...]} (per-channel dedup)
      - "last_run": ISO timestamp
      - "total_notified": int
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

        # Ensure required keys exist. Channel migration needs the real configured
        # channel names, so that happens after channels are loaded in main().
        data.setdefault("jobs", [])
        data.setdefault("channels", {})
        data.setdefault("last_run", _now_iso())
        data.setdefault("total_notified", 0)

        return data

    except FileNotFoundError:
        print("[INFO] seen_jobs.json not found — starting fresh.")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[WARN] seen_jobs.json is corrupt ({e}) — starting fresh.")

    return {"jobs": [], "channels": {}, "last_run": _now_iso(), "total_notified": 0}


def prune_seen_jobs(data: dict) -> dict:
    """Remove job entries older than SEEN_JOBS_MAX_AGE_DAYS."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.SEEN_JOBS_MAX_AGE_DAYS)
    before = len(data["jobs"])

    # Prune global jobs list
    surviving_ids: set[str] = set()
    new_jobs = []
    for entry in data["jobs"]:
        if _parse_dt(entry.get("seen_at", "")) > cutoff:
            new_jobs.append(entry)
            surviving_ids.add(entry["id"])
    data["jobs"] = new_jobs

    # Prune channel lists to match (remove IDs that were pruned globally)
    for ch_name in list(data.get("channels", {})):
        data["channels"][ch_name] = [
            jid for jid in data["channels"][ch_name] if jid in surviving_ids
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


def get_channel_seen_ids(data: dict, channel_name: str) -> set[str]:
    return set(data.get("channels", {}).get(channel_name, []))


def ensure_channel_seen_state(data: dict, channels: list[ChannelConfig]) -> None:
    """
    Ensure seen_jobs.json has per-channel dedupe lists.

    When migrating from the old single-channel schema, existing global seen IDs
    are copied into the configured channel names to avoid re-notifying every
    historical match on the first multi-channel run.
    """
    channel_map = data.setdefault("channels", {})
    global_ids = [entry["id"] for entry in data.get("jobs", []) if entry.get("id")]
    configured_names = [ch.name for ch in channels]

    if global_ids and not channel_map:
        for name in configured_names:
            channel_map[name] = list(global_ids)
        print(f"[INFO] Migrated existing seen job IDs to channels: {configured_names}")
    elif (
        global_ids
        and set(channel_map) == {"default"}
        and "default" not in configured_names
    ):
        default_ids = channel_map.get("default", global_ids)
        for name in configured_names:
            channel_map.setdefault(name, list(default_ids))
        print(f"[INFO] Migrated default seen job IDs to channels: {configured_names}")

    for name in configured_names:
        channel_map.setdefault(name, [])


def mark_seen_global(data: dict, job_id: str) -> None:
    """Add job to global seen list (with timestamp for TTL pruning)."""
    existing_ids = get_seen_ids(data)
    if job_id not in existing_ids:
        data["jobs"].append({"id": job_id, "seen_at": _now_iso()})


def mark_seen_channel(data: dict, channel_name: str, job_id: str) -> None:
    """Mark a job as sent to a specific channel."""
    data.setdefault("channels", {}).setdefault(channel_name, [])
    if job_id not in data["channels"][channel_name]:
        data["channels"][channel_name].append(job_id)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

# Reuse a single scraper instance for its filter methods
_filter_scraper = GreenhouseScraper()


def filter_for_channel(jobs: list[Job], channel: ChannelConfig) -> list[Job]:
    """Apply a channel's keyword + excluded + location filters to jobs."""
    return [
        job for job in jobs
        if job.title
        and _filter_scraper.matches_keywords(
            job.title, channel.keywords, channel.excluded_keywords
        )
        and _filter_scraper.matches_location(job.location, channel.locations)
    ]


# ---------------------------------------------------------------------------
# Core scraping logic (raw — no filtering)
# ---------------------------------------------------------------------------

async def scrape_all_raw() -> tuple[list[Job], int]:
    """
    Run all scrapers across all sources. Returns ALL jobs with NO filtering.
    Bulk scrapers (simplify, hackernews) are called once.
    ATS scrapers (greenhouse, lever, ashby) are called per company slug.
    Returns (all_raw_jobs, total_postings_checked).
    """
    ats_scrapers = {
        "greenhouse": GreenhouseScraper(),
        "lever": LeverScraper(),
        "ashby": AshbyScraper(),
    }

    bulk_scrapers = {
        "simplify": SimplifyScraper(),
        "hackernews": HackerNewsScraper(),
    }

    all_jobs: list[Job] = []
    total_checked = 0

    # --- Bulk scrapers first ---
    for platform, scraper in bulk_scrapers.items():
        if platform not in COMPANIES:
            continue
        jobs = await scraper.fetch_jobs("")
        total_checked += len(jobs)
        all_jobs.extend(jobs)

    # --- ATS scrapers per company ---
    for platform, company_slugs in COMPANIES.items():
        if platform not in ats_scrapers:
            continue
        scraper = ats_scrapers[platform]
        for slug in company_slugs:
            jobs = await scraper.fetch_jobs(slug)
            total_checked += len(jobs)
            all_jobs.extend(jobs)
            await asyncio.sleep(config.SLEEP_BETWEEN_COMPANIES)

    return all_jobs, total_checked


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main(init_mode: bool = False) -> None:
    # Load channels (don't require webhooks in init mode)
    channels = load_channels(require_webhooks=not init_mode)

    print(f"[INFO] Job Scraper starting — {'INIT MODE' if init_mode else 'normal run'}")
    print(f"[INFO] Channels configured: {[ch.name for ch in channels]}")

    for ch in channels:
        print(f"  • {ch.name}: {len(ch.keywords)} keywords, "
              f"{len(ch.excluded_keywords)} exclusions, "
              f"{len(ch.locations)} location filters")

    # Load + prune seen_jobs.json
    seen_data = load_seen_jobs()
    seen_data = prune_seen_jobs(seen_data)
    ensure_channel_seen_state(seen_data, channels)

    # Scrape all platforms (raw, no filtering)
    print("\n[INFO] Scraping all platforms...")
    all_jobs, total_checked = await scrape_all_raw()
    print(f"[INFO] Total postings fetched: {total_checked}")

    # --- INIT MODE: seed seen_jobs.json without notifying ---
    if init_mode:
        seeded = 0
        global_seen = get_seen_ids(seen_data)

        for ch in channels:
            matching = filter_for_channel(all_jobs, ch)
            for job in matching:
                mark_seen_global(seen_data, job.id)
                mark_seen_channel(seen_data, ch.name, job.id)
                if job.id not in global_seen:
                    seeded += 1
                    global_seen.add(job.id)

        seen_data["last_run"] = _now_iso()
        try:
            save_seen_jobs(seen_data)
            print(f"\n[INIT] Seeded {seeded} job IDs into seen_jobs.json")
            print(f"[INIT] Channels initialized: {[ch.name for ch in channels]}")
            print("[INIT] No Discord notifications sent. You're ready to run normally.")
        except OSError as e:
            print(f"[ERROR] Failed to save seen_jobs.json: {e}")
        return

    # --- NORMAL MODE: per-channel filter → dedupe → notify ---
    total_notified = 0

    for ch in channels:
        print(f"\n{'='*60}")
        print(f"[CHANNEL] Processing: {ch.name}")
        print(f"{'='*60}")

        # Filter jobs for this channel's keywords/exclusions/locations
        matching = filter_for_channel(all_jobs, ch)
        ch_seen_ids = get_channel_seen_ids(seen_data, ch.name)

        # Find jobs not yet sent to THIS channel
        new_for_channel = [job for job in matching if job.id not in ch_seen_ids]

        print(f"[INFO] Matching: {len(matching)} | New for channel: {len(new_for_channel)}")

        if not new_for_channel:
            print(f"[INFO] No new jobs for '{ch.name}'. Skipping.")
            continue

        # Apply per-run notification cap
        capped = len(new_for_channel) > config.MAX_NOTIFICATIONS_PER_RUN
        jobs_to_notify = new_for_channel[:config.MAX_NOTIFICATIONS_PER_RUN]

        if capped:
            print(f"[WARN] Capping at {config.MAX_NOTIFICATIONS_PER_RUN} "
                  f"(found {len(new_for_channel)} new)")

        # Mark ALL new jobs as seen for this channel (even if over cap)
        for job in new_for_channel:
            mark_seen_global(seen_data, job.id)
            mark_seen_channel(seen_data, ch.name, job.id)

        # Send Discord notifications
        notified = await discord_notifier.notify_jobs_batch(
            jobs_to_notify, ch.webhook_url
        )
        total_notified += len(notified)

        # Send summary embed
        await discord_notifier.send_summary(
            new_count=len(notified),
            total_checked=total_checked,
            capped=capped,
            webhook_url=ch.webhook_url,
            channel_name=ch.name,
        )

        print(f"[INFO] Notified {len(notified)} jobs to '{ch.name}'")

    # Update metadata and save
    seen_data["last_run"] = _now_iso()
    seen_data["total_notified"] = seen_data.get("total_notified", 0) + total_notified

    try:
        save_seen_jobs(seen_data)
        print(f"\n[INFO] seen_jobs.json saved ({len(seen_data['jobs'])} total entries)")
    except OSError as e:
        print(f"[ERROR] Failed to save seen_jobs.json: {e}")

    print(f"[INFO] Run complete — {total_notified} total notifications sent")


if __name__ == "__main__":
    init_mode = "--init" in sys.argv
    asyncio.run(main(init_mode=init_mode))
