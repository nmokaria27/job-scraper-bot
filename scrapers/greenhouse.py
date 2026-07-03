import asyncio
import httpx
from scrapers.base import BaseScraper, Job
from scrapers.http_utils import fetch_json
from utils import format_company_name
import config


class GreenhouseScraper(BaseScraper):
    PLATFORM = "greenhouse"
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        url = self.BASE_URL.format(company=company_slug)
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            data, is_404 = await fetch_json(client, url, self.PLATFORM, company_slug)
        if data is None:
            return []

        jobs: list[Job] = []
        raw_jobs = data.get("jobs", [])

        for raw in raw_jobs:
            title = raw.get("title")
            if not title:
                continue

            location = (raw.get("location") or {}).get("name") or "Remote / Not Specified"
            job = Job(
                id=f"greenhouse-{company_slug}-{raw['id']}",
                title=title,
                company=format_company_name(company_slug),
                location=location,
                url=raw.get("absolute_url", ""),
                platform=self.PLATFORM,
                posted_at=raw.get("updated_at", "Unknown"),
            )
            jobs.append(job)

        print(f"[OK] greenhouse/{company_slug}: {len(jobs)} jobs found")
        await asyncio.sleep(0)  # yield control back to event loop
        return jobs
