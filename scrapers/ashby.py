from urllib.parse import quote

from scrapers.base import BaseScraper, Job, company_name_from_slug
from scrapers.fetch import fetch_json, make_client

ASHBY_BASE = "https://jobs.ashbyhq.com"
ASHBY_API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyScraper(BaseScraper):
    PLATFORM = "ashby"

    @classmethod
    def parse_job(cls, raw: dict, company_slug: str) -> Job | None:
        title = raw.get("title")
        if not title:
            return None

        location = raw.get("location") or "Remote / Not Specified"
        secondary_locations = [
            secondary.get("location")
            for secondary in raw.get("secondaryLocations") or []
            if secondary.get("location")
        ]
        if secondary_locations:
            location = " / ".join([location, *secondary_locations])

        job_url = raw.get("jobUrl", "")
        if job_url and not job_url.startswith("http"):
            job_url = f"{ASHBY_BASE}{job_url}"

        return Job(
            id=f"ashby-{company_slug}-{raw.get('id') or raw.get('jobUrl')}",
            title=title,
            company=company_name_from_slug(company_slug),
            location=location,
            url=job_url,
            platform=cls.PLATFORM,
            # The posting API's field is `publishedAt`. The old code read
            # `publishedDate`, which never existed, so every Ashby job was
            # undated and slipped past the recency filter.
            posted_at=raw.get("publishedAt") or raw.get("publishedDate") or raw.get("updatedAt") or "Unknown",
        )

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        board_name = quote(company_slug, safe="")
        url = f"{ASHBY_API_BASE}/{board_name}"
        label = f"ashby/{company_slug}"
        async with make_client() as client:
            data = await fetch_json(client, url, label)
        if not isinstance(data, dict):
            return []

        jobs: list[Job] = []
        for raw in data.get("jobs", []):
            job = self.parse_job(raw, company_slug)
            if job:
                jobs.append(job)

        print(f"[OK] {label}: {len(jobs)} jobs found")
        return jobs
