from datetime import datetime, timezone

import config
from scrapers.base import BaseScraper, Job
from scrapers.fetch import fetch_json, make_client


class SimplifyScraper(BaseScraper):
    """SimplifyJobs listings.json format (also used by the vanshb03 repos)."""

    PLATFORM = "simplify"

    @classmethod
    def parse_entries(cls, entries: object) -> list[Job]:
        if not isinstance(entries, list):
            return []

        jobs: list[Job] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # Only include active and visible listings
            if not entry.get("active") or not entry.get("is_visible"):
                continue

            title = entry.get("title")
            if not title or not entry.get("id"):
                continue

            # Join the locations list into a single string
            raw_locations = entry.get("locations") or []
            location = " / ".join(raw_locations) if raw_locations else "Unknown"

            # date_posted is Unix timestamp in seconds
            date_posted = entry.get("date_posted")
            posted_at = "Unknown"
            if date_posted:
                try:
                    posted_at = datetime.fromtimestamp(date_posted, tz=timezone.utc).isoformat()
                except (ValueError, OSError, TypeError):
                    posted_at = "Unknown"

            jobs.append(
                Job(
                    id=f"simplify-{entry['id']}",
                    title=title,
                    company=entry.get("company_name", "Unknown"),
                    location=location,
                    url=entry.get("url", ""),
                    platform=cls.PLATFORM,
                    posted_at=posted_at,
                )
            )
        return jobs

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        """
        Fetch jobs from all URLs in config.SIMPLIFY_URLS.
        company_slug is ignored — each URL returns all listings for that repo.
        Deduplicates by job ID across URLs.
        """
        seen_ids: set[str] = set()
        all_jobs: list[Job] = []

        async with make_client() as client:
            for url in config.SIMPLIFY_URLS:
                # Use owner/repo as a short label for logs
                parts = url.split("/")
                label = f"simplify ({parts[3]}/{parts[4]})" if len(parts) > 4 else f"simplify ({url})"
                entries = await fetch_json(client, url, label)
                if entries is None:
                    continue
                jobs = self.parse_entries(entries)
                print(f"[OK] {label}: {len(jobs)} active jobs found")
                for job in jobs:
                    if job.id not in seen_ids:
                        seen_ids.add(job.id)
                        all_jobs.append(job)

        return all_jobs
