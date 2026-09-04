from datetime import datetime, timezone

from scrapers.base import BaseScraper, Job, company_name_from_slug
from scrapers.fetch import fetch_json, make_client


class LeverScraper(BaseScraper):
    PLATFORM = "lever"
    BASE_URL = "https://api.lever.co/v0/postings/{company}?mode=json"

    @classmethod
    def parse_job(cls, raw: dict, company_slug: str) -> Job | None:
        title = raw.get("text")
        if not title or not raw.get("id"):
            return None

        categories = raw.get("categories") or {}
        all_locations = [loc for loc in categories.get("allLocations") or [] if loc]
        location = " / ".join(all_locations) or categories.get("location") or "Remote / Not Specified"

        # createdAt is Unix timestamp in milliseconds
        created_at_ms = raw.get("createdAt")
        posted_at = "Unknown"
        if created_at_ms:
            try:
                posted_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).isoformat()
            except (ValueError, OSError, TypeError):
                posted_at = "Unknown"

        return Job(
            id=f"lever-{company_slug}-{raw['id']}",
            title=title,
            company=company_name_from_slug(company_slug),
            location=location,
            url=raw.get("hostedUrl", ""),
            platform=cls.PLATFORM,
            posted_at=posted_at,
        )

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        url = self.BASE_URL.format(company=company_slug)
        label = f"lever/{company_slug}"
        async with make_client() as client:
            data = await fetch_json(client, url, label)
        if not isinstance(data, list):
            return []

        jobs: list[Job] = []
        for raw in data:
            job = self.parse_job(raw, company_slug)
            if job:
                jobs.append(job)

        print(f"[OK] {label}: {len(jobs)} jobs found")
        return jobs
