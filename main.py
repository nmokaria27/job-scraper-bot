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
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import config
from config import ChannelConfig, load_channels
from companies import get_companies
from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.simplify import SimplifyScraper
from scrapers.hackernews import HackerNewsScraper
from scrapers.markdown_table import SpeedyApplyScraper, JobRightScraper, ZapplyScraper
from scrapers.json_sources import JsonSourceScraper
from scrapers.bigtech import AmazonScraper, WorkdayScraper
from scrapers.fetch import describe_error
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


def ensure_channel_seen_state(
    data: dict,
    channels: list[ChannelConfig],
    prune_stale: bool = False,
) -> None:
    """
    Ensure seen_jobs.json has per-channel dedupe lists.

    When migrating from the old single-channel schema, existing global seen IDs
    are copied into the configured channel names to avoid re-notifying every
    historical match on the first multi-channel run.

    With prune_stale=True, per-channel lists for channels that are no longer
    configured (e.g. the retired internship channel) are dropped so the file
    doesn't carry dead state forever. Only normal runs prune; --init never does.
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

    if prune_stale:
        for name in [n for n in channel_map if n not in configured_names]:
            removed = len(channel_map.pop(name) or [])
            print(f"[INFO] Dropped seen-state for retired channel '{name}' ({removed} ids)")


def drop_stale_queue_channels(queue_data: dict, channels: list[ChannelConfig]) -> None:
    """Remove queued jobs for channels that are no longer configured."""
    configured = {ch.name for ch in channels}
    queue_channels = queue_data.setdefault("channels", {})
    for name in [n for n in queue_channels if n not in configured]:
        removed = len(queue_channels.pop(name) or [])
        if removed:
            print(f"[INFO] Dropped {removed} queued job(s) for retired channel '{name}'")


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
# queued_jobs.json helpers (for capped notifications)
# ---------------------------------------------------------------------------

def load_queued_jobs() -> dict:
    """
    Load queued_jobs.json. Returns a dict shaped like:
      {"channels": {channel_name: [QueuedJob, ...]}, "last_run": ISO timestamp}
    """
    try:
        with open(config.QUEUED_JOBS_PATH, "r") as f:
            data = json.load(f)
        data.setdefault("channels", {})
        data.setdefault("last_run", _now_iso())
        return data
    except FileNotFoundError:
        return {"channels": {}, "last_run": _now_iso()}
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[WARN] queued_jobs.json is corrupt ({e}) — starting fresh.")
        return {"channels": {}, "last_run": _now_iso()}


def save_queued_jobs(data: dict) -> None:
    with open(config.QUEUED_JOBS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def prune_queued_jobs(data: dict) -> dict:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=config.QUEUED_JOBS_MAX_AGE_HOURS)
    channels = data.setdefault("channels", {})
    removed = 0
    for ch_name, items in list(channels.items()):
        if not isinstance(items, list):
            channels[ch_name] = []
            continue
        kept = []
        for item in items:
            queued_at = _parse_dt((item or {}).get("queued_at", ""))
            if queued_at != datetime.min.replace(tzinfo=timezone.utc) and queued_at >= cutoff:
                kept.append(item)
            else:
                removed += 1
        channels[ch_name] = kept
    if removed:
        print(f"[INFO] Pruned {removed} stale queued job(s) from queued_jobs.json")
    return data


def _queued_job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "seen_keys": get_job_seen_keys(job),
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "platform": job.platform,
        "posted_at": job.posted_at,
        "queued_at": _now_iso(),
    }


def enqueue_capped_jobs(queue_data: dict, channel_name: str, jobs: list[Job]) -> int:
    queue_data.setdefault("channels", {}).setdefault(channel_name, [])
    existing = queue_data["channels"][channel_name]
    existing_keys: set[str] = set()
    for item in existing:
        for key in (item or {}).get("seen_keys", []) or []:
            existing_keys.add(key)

    added = 0
    for job in jobs:
        keys = get_job_seen_keys(job)
        if any(k in existing_keys for k in keys):
            continue
        existing.append(_queued_job_payload(job))
        for k in keys:
            existing_keys.add(k)
        added += 1
    if added:
        print(f"[INFO] Queued {added} capped job(s) for '{channel_name}'")
    return added


def dequeue_flush_candidates(queue_data: dict, channel_name: str) -> list[Job]:
    items = queue_data.get("channels", {}).get(channel_name, [])
    if not items:
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=config.QUEUED_JOBS_MAX_AGE_HOURS)
    candidates: list[tuple[datetime, dict]] = []
    for item in items:
        queued_at = _parse_dt((item or {}).get("queued_at", ""))
        posted_at = _parse_dt((item or {}).get("posted_at", ""))
        min_dt = datetime.min.replace(tzinfo=timezone.utc)
        freshness = posted_at if posted_at != min_dt else queued_at
        if freshness == min_dt or freshness < cutoff:
            continue
        candidates.append((freshness, item))

    # newest first
    candidates.sort(key=lambda t: t[0], reverse=True)

    jobs: list[Job] = []
    for _, item in candidates[: config.MAX_NOTIFICATIONS_PER_RUN]:
        jobs.append(
            Job(
                id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                company=str(item.get("company", "")),
                location=str(item.get("location", "")),
                url=str(item.get("url", "")),
                platform=str(item.get("platform", "")),
                posted_at=str(item.get("posted_at", "Unknown")),
            )
        )
    return jobs


def drop_queued_items(queue_data: dict, channel_name: str, notified: list[Job]) -> int:
    items = queue_data.get("channels", {}).get(channel_name, [])
    if not items or not notified:
        return 0

    notified_keys: set[str] = set()
    for job in notified:
        for key in get_job_seen_keys(job):
            notified_keys.add(key)

    kept = []
    removed = 0
    for item in items:
        item_keys = set((item or {}).get("seen_keys", []) or [])
        if item_keys and (item_keys & notified_keys):
            removed += 1
            continue
        kept.append(item)
    queue_data["channels"][channel_name] = kept
    return removed


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
        and _filter_scraper.matches_location(
            job.location, channel.locations, channel.excluded_locations
        )
    ]


# Sources that only know the posting *date* (jobright, Amazon, Workday, the
# JSON trackers) emit "YYYY-MM-DD". Midnight would make a job posted at 23:00
# look 25h old by the next morning's run and be dropped forever, so date-only
# timestamps get an extra day of grace.
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def filter_recent_jobs(jobs: list[Job]) -> list[Job]:
    """Keep only jobs whose posting timestamp is within the configured window.
    Jobs with unknown/unparseable timestamps are kept (benefit of the doubt)."""
    max_age_hours = config.RECENT_POSTING_MAX_AGE_HOURS
    if max_age_hours <= 0:
        return jobs

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    date_only_cutoff = cutoff - timedelta(hours=24)
    # datetime.min means unparseable — keep those jobs rather than silently dropping them
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    recent = []
    for job in jobs:
        posted_dt = _parse_dt(job.posted_at)
        effective_cutoff = date_only_cutoff if _DATE_ONLY_RE.match(job.posted_at or "") else cutoff
        if posted_dt == min_dt or posted_dt >= effective_cutoff:
            recent.append(job)

    skipped = len(jobs) - len(recent)
    print(
        f"[INFO] Recent posting filter: kept {len(recent)} of {len(jobs)} "
        f"(last {max_age_hours}h, skipped {skipped})"
    )
    return recent


# When the same posting shows up from several sources, keep the one closest to
# the employer (direct ATS > curated list > aggregator > HN comment).
PLATFORM_DEDUPE_PRIORITY: dict[str, int] = {
    "greenhouse": 4,
    "lever": 4,
    "ashby": 4,
    "amazon": 4,
    "workday": 4,
    "simplify": 3,
    "speedyapply": 2,
    "jobright": 2,
    "zapply": 2,
    "applyguy": 2,
    "gradtracker": 2,
    "hackernews": 1,
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().casefold()


def _normalize_job_url(url: str) -> str:
    raw_url = (url or "").strip()
    if not raw_url:
        return ""

    parsed = urlsplit(raw_url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.casefold()
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def get_job_seen_key(job: Job) -> str:
    normalized_url = _normalize_job_url(job.url)
    if normalized_url:
        return f"url:{normalized_url}"

    return "fallback:" + "|".join(
        [
            _normalize_text(job.company),
            _normalize_text(job.title),
            _normalize_text(job.location),
        ]
    )


def get_job_identity_key(job: Job) -> str:
    """
    Company + title + location, normalised. Catches the same posting listed
    under different URLs (aggregators re-list with fresh IDs; the same req is
    posted as several ATS entries).
    """
    return "identity:" + "|".join(
        [
            _normalize_text(job.company),
            _normalize_text(job.title),
            _normalize_text(job.location),
        ]
    )


def get_job_seen_keys(job: Job) -> list[str]:
    """
    Keys persisted in seen_jobs.json: the raw id and the normalised URL.
    The identity key is deliberately NOT persisted — big employers re-post
    the same title/location as new reqs for months, and those are new jobs.
    """
    keys = [job.id, get_job_seen_key(job)]
    unique_keys: list[str] = []
    for key in keys:
        if key and key not in unique_keys:
            unique_keys.append(key)
    return unique_keys


def get_job_dedupe_keys(job: Job) -> list[str]:
    """Keys used to collapse duplicates inside a single run (URL or identity)."""
    return [get_job_seen_key(job), get_job_identity_key(job)]


def _newest_first(jobs: list[Job]) -> list[Job]:
    """Dated jobs newest first; undated ones last (so the cap prefers fresh posts)."""
    min_dt = datetime.min.replace(tzinfo=timezone.utc)

    def sort_key(job: Job) -> tuple[int, datetime]:
        posted = _parse_dt(job.posted_at)
        return (0 if posted != min_dt else 1, -posted.timestamp() if posted != min_dt else 0.0)

    return sorted(jobs, key=sort_key)


def _job_dedupe_rank(job: Job) -> tuple[int, bool, datetime, int, int]:
    posted_at = _parse_dt(job.posted_at)
    return (
        PLATFORM_DEDUPE_PRIORITY.get(job.platform, 0),
        posted_at != datetime.min.replace(tzinfo=timezone.utc),
        posted_at,
        len(job.url or ""),
        len(job.location or ""),
    )


def dedupe_jobs_for_channel(channel_name: str, jobs: list[Job]) -> list[Job]:
    """
    Collapse jobs that share a normalised URL *or* a company/title/location
    identity, keeping the highest-ranked source for each cluster.
    """
    index: dict[str, Job] = {}
    kept: dict[int, Job] = {}
    duplicates_removed = 0

    for job in jobs:
        keys = get_job_dedupe_keys(job)
        existing = next((index[key] for key in keys if key in index), None)

        if existing is None:
            kept[id(job)] = job
            for key in keys:
                index[key] = job
            continue

        duplicates_removed += 1
        winner = job if _job_dedupe_rank(job) > _job_dedupe_rank(existing) else existing
        if winner is job:
            kept.pop(id(existing), None)
            kept[id(job)] = job
            for key in get_job_dedupe_keys(existing):
                index[key] = job
        for key in keys:
            index[key] = winner

    if duplicates_removed:
        print(
            f"[INFO] Canonical dedupe removed {duplicates_removed} overlapping "
            f"match(es) for '{channel_name}'"
        )

    return list(kept.values())


def job_was_seen(seen_ids: set[str], job: Job) -> bool:
    return any(key in seen_ids for key in get_job_seen_keys(job))


def mark_job_seen(data: dict, channel_name: str, job: Job) -> None:
    for key in get_job_seen_keys(job):
        mark_seen_global(data, key)
        mark_seen_channel(data, channel_name, key)


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
        "speedyapply": SpeedyApplyScraper(),
        "jobright": JobRightScraper(),
        "zapply": ZapplyScraper(),
        "jsonsource": JsonSourceScraper(),
        "amazon": AmazonScraper(),
        "workday": WorkdayScraper(),
    }

    all_jobs: list[Job] = []
    total_checked = 0
    companies = get_companies()

    # --- Bulk scrapers first (each isolated: one broken source must not kill the run) ---
    async def run_bulk(platform: str, scraper) -> list[Job]:
        try:
            return await scraper.fetch_jobs("")
        except Exception as e:  # noqa: BLE001 — deliberate isolation boundary
            print(f"[ERROR] {platform}: scraper crashed — {describe_error(e)}")
            return []

    bulk_results = await asyncio.gather(
        *[run_bulk(platform, scraper) for platform, scraper in bulk_scrapers.items() if platform in companies]
    )
    for jobs in bulk_results:
        total_checked += len(jobs)
        all_jobs.extend(jobs)

    # --- ATS scrapers per company ---
    async def fetch_company(platform: str, slug: str, semaphore: asyncio.Semaphore) -> list[Job]:
        async with semaphore:
            try:
                jobs = await ats_scrapers[platform].fetch_jobs(slug)
            except Exception as e:  # noqa: BLE001 — deliberate isolation boundary
                print(f"[ERROR] {platform}/{slug}: scraper crashed — {describe_error(e)}")
                jobs = []
            if config.SLEEP_BETWEEN_COMPANIES > 0:
                await asyncio.sleep(config.SLEEP_BETWEEN_COMPANIES)
            return jobs

    semaphore = asyncio.Semaphore(max(1, config.ATS_CONCURRENCY))
    tasks = [
        fetch_company(platform, slug, semaphore)
        for platform, company_slugs in companies.items()
        if platform in ats_scrapers
        for slug in company_slugs
    ]
    for jobs in await asyncio.gather(*tasks):
        total_checked += len(jobs)
        all_jobs.extend(jobs)

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
    ensure_channel_seen_state(seen_data, channels, prune_stale=not init_mode)

    # Load + prune queued_jobs.json
    queue_data = load_queued_jobs()
    queue_data = prune_queued_jobs(queue_data)
    if not init_mode:
        drop_stale_queue_channels(queue_data, channels)

    # Scrape all platforms (raw, no filtering)
    print("\n[INFO] Scraping all platforms...")
    all_jobs, total_checked = await scrape_all_raw()
    print(f"[INFO] Total postings fetched: {total_checked}")

    # --- INIT MODE: seed seen_jobs.json without notifying ---
    if init_mode:
        seeded = 0
        global_seen = get_seen_ids(seen_data)

        for ch in channels:
            matching = dedupe_jobs_for_channel(ch.name, filter_for_channel(all_jobs, ch))
            for job in matching:
                if not job_was_seen(global_seen, job):
                    seeded += 1
                mark_job_seen(seen_data, ch.name, job)
                global_seen.update(get_job_seen_keys(job))

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
    all_jobs = filter_recent_jobs(all_jobs)
    total_notified = 0

    for ch in channels:
        print(f"\n{'='*60}")
        print(f"[CHANNEL] Processing: {ch.name}")
        print(f"{'='*60}")

        # Filter jobs for this channel's keywords/exclusions/locations
        matching = dedupe_jobs_for_channel(ch.name, filter_for_channel(all_jobs, ch))
        ch_seen_ids = get_channel_seen_ids(seen_data, ch.name)

        # Find jobs not yet sent to THIS channel, freshest first so the per-run
        # cap never spends its budget on undated backlog.
        new_for_channel = _newest_first(
            [job for job in matching if not job_was_seen(ch_seen_ids, job)]
        )

        print(f"[INFO] Matching: {len(matching)} | New for channel: {len(new_for_channel)}")

        if not new_for_channel:
            # Flush queue only when there are no new jobs for the channel
            flush_candidates = dequeue_flush_candidates(queue_data, ch.name)
            flushed: list[Job] = []
            if flush_candidates:
                print(f"[INFO] No new jobs for '{ch.name}'. Flushing {len(flush_candidates)} queued job(s).")
                flushed = await discord_notifier.notify_jobs_batch(
                    flush_candidates, ch.webhook_url
                )
                total_notified += len(flushed)
                removed = drop_queued_items(queue_data, ch.name, flushed)
                if removed:
                    print(f"[INFO] Flushed and removed {removed} queued job(s) for '{ch.name}'")
            else:
                print(f"[INFO] No new jobs for '{ch.name}'. Skipping.")
            await discord_notifier.send_summary(
                new_count=len(flushed),
                total_checked=total_checked,
                webhook_url=ch.webhook_url,
                channel_name=ch.name,
                force=config.SEND_NO_NEW_SUMMARY,
            )
            continue

        # Apply per-run notification cap
        capped = len(new_for_channel) > config.MAX_NOTIFICATIONS_PER_RUN
        jobs_to_notify = new_for_channel[:config.MAX_NOTIFICATIONS_PER_RUN]

        if capped:
            print(f"[WARN] Capping at {config.MAX_NOTIFICATIONS_PER_RUN} "
                  f"(found {len(new_for_channel)} new)")

        # Send Discord notifications
        notified = await discord_notifier.notify_jobs_batch(
            jobs_to_notify, ch.webhook_url
        )
        total_notified += len(notified)

        for job in notified:
            mark_job_seen(seen_data, ch.name, job)

        if len(notified) == len(jobs_to_notify):
            jobs_to_queue = new_for_channel[config.MAX_NOTIFICATIONS_PER_RUN:]
            if jobs_to_queue:
                # Queue capped jobs so they can be flushed later when there are no new jobs.
                enqueue_capped_jobs(queue_data, ch.name, jobs_to_queue)
                # Still mark as seen so they won't keep re-appearing as "new" on every run.
                for job in jobs_to_queue:
                    mark_job_seen(seen_data, ch.name, job)
        elif jobs_to_notify:
            failed = len(jobs_to_notify) - len(notified)
            print(f"[WARN] {failed} notification(s) failed; unsent jobs were left unmarked")

        # Send summary embed
        await discord_notifier.send_summary(
            new_count=len(notified),
            total_checked=total_checked,
            capped=capped,
            webhook_url=ch.webhook_url,
            channel_name=ch.name,
            force=config.SEND_NO_NEW_SUMMARY,
        )

        print(f"[INFO] Notified {len(notified)} jobs to '{ch.name}'")

    # Update metadata and save
    seen_data["last_run"] = _now_iso()
    seen_data["total_notified"] = seen_data.get("total_notified", 0) + total_notified
    queue_data["last_run"] = _now_iso()

    try:
        save_seen_jobs(seen_data)
        print(f"\n[INFO] seen_jobs.json saved ({len(seen_data['jobs'])} total entries)")
    except OSError as e:
        print(f"[ERROR] Failed to save seen_jobs.json: {e}")

    try:
        save_queued_jobs(queue_data)
        total_queued = sum(len(v) for v in queue_data.get("channels", {}).values() if isinstance(v, list))
        print(f"[INFO] queued_jobs.json saved ({total_queued} total queued entries)")
    except OSError as e:
        print(f"[ERROR] Failed to save queued_jobs.json: {e}")

    print(f"[INFO] Run complete — {total_notified} total notifications sent")


if __name__ == "__main__":
    init_mode = "--init" in sys.argv
    asyncio.run(main(init_mode=init_mode))
