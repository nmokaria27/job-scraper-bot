"""
Scraper for GitHub-hosted new-grad trackers that publish a JSON job list.

Each source is described by a small field-mapping spec so adding another
tracker is a config change, not a new scraper. Values in the spec may be a
single key or a list of fallback keys (first non-empty wins).
"""

import hashlib
import re
from datetime import datetime, timezone

import config
from scrapers.base import BaseScraper, Job
from scrapers.fetch import fetch_json, make_client

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# name -> spec. `root` is the key holding the list (empty string = top-level
# list). `location` may list several keys that get joined with ", ".
JSON_SOURCE_SPECS: dict[str, dict] = {
    "applyguy": {
        "platform": "applyguy",
        "root": "jobs",
        "id": "id",
        "company": "company",
        "title": "title",
        "location": ["location"],
        "url": ["listingUrl", "url"],
        "posted": ["posted"],
    },
    "gradtracker": {
        "platform": "gradtracker",
        "root": "jobs",
        "id": None,
        "company": "company",
        "title": "title",
        "location": ["location", "country"],
        "url": ["application_url"],
        "posted": ["date_posted", "first_seen"],
    },
}


def _pick(entry: dict, keys: object) -> str:
    if keys is None:
        return ""
    if isinstance(keys, str):
        keys = [keys]
    for key in keys:
        value = entry.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return ""


def _normalize_posted(value: str) -> str:
    """Keep 'YYYY-MM-DD' date-only strings as-is; normalise other ISO forms."""
    if not value:
        return "Unknown"
    if _ISO_DATE_RE.match(value):
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "Unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_json_source(payload: object, spec: dict) -> list[Job]:
    """Turn a decoded JSON payload into Job objects according to `spec`."""
    root = spec.get("root") or ""
    entries = payload.get(root, []) if (root and isinstance(payload, dict)) else payload
    if not isinstance(entries, list):
        return []

    platform = spec["platform"]
    jobs: list[Job] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("active") is False:
            continue
        title = _pick(entry, spec.get("title"))
        company = _pick(entry, spec.get("company"))
        if not title or not company:
            continue

        location_parts = [
            _pick(entry, key) for key in (spec.get("location") or [])
        ]
        location = ", ".join(part for part in location_parts if part) or "Unknown"
        url = _pick(entry, spec.get("url"))
        posted_at = _normalize_posted(_pick(entry, spec.get("posted")))

        raw_id = _pick(entry, spec.get("id"))
        if not raw_id:
            raw_id = hashlib.md5(f"{company}|{title}|{url}".encode()).hexdigest()[:12]

        jobs.append(
            Job(
                id=f"{platform}-{raw_id}",
                title=title,
                company=company,
                location=location,
                url=url,
                platform=platform,
                posted_at=posted_at,
            )
        )
    return jobs


class JsonSourceScraper(BaseScraper):
    """Fetch every enabled JSON tracker listed in config.JSON_SOURCE_URLS."""

    PLATFORM = "jsonsource"

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        seen_ids: set[str] = set()
        all_jobs: list[Job] = []

        async with make_client() as client:
            for name, url in config.JSON_SOURCE_URLS.items():
                spec = JSON_SOURCE_SPECS.get(name)
                if spec is None:
                    print(f"[WARN] jsonsource: no field spec for '{name}'; skipping")
                    continue
                label = f"{spec['platform']} ({name})"
                payload = await fetch_json(client, url, label)
                if payload is None:
                    continue
                jobs = parse_json_source(payload, spec)
                print(f"[OK] {label}: {len(jobs)} jobs found")
                for job in jobs:
                    if job.id not in seen_ids:
                        seen_ids.add(job.id)
                        all_jobs.append(job)

        return all_jobs
