import asyncio
import httpx
from scrapers.base import BaseScraper, Job
from scrapers.http_utils import fetch_json
from utils import format_company_name, timestamp_to_iso
import config


class LeverScraper(BaseScraper):
    PLATFORM = "lever"
    BASE_URL = "https://api.lever.co/v0/postings/{company}?mode=json"

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        url = self.BASE_URL.format(company=company_slug)
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            data, is_404 = await fetch_json(client, url, self.PLATFORM, company_slug)
        if data is None:
            return []

        jobs: list[Job] = []

        for raw in data:
            title = raw.get("text")
            if not title:
                continue

            categories = raw.get("categories") or {}
            location = categories.get("location") or "Remote / Not Specified"

            posted_at = timestamp_to_iso(raw.get("createdAt"), unit="milliseconds")

            job = Job(
                id=f"lever-{company_slug}-{raw['id']}",
                title=title,
                company=format_company_name(company_slug),
                location=location,
                url=raw.get("hostedUrl", ""),
                platform=self.PLATFORM,
                posted_at=posted_at,
            )
            jobs.append(job)

        print(f"[OK] lever/{company_slug}: {len(jobs)} jobs found")
        await asyncio.sleep(0)  # yield control back to event loop
        return jobs
