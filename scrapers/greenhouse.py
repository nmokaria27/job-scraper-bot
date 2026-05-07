import asyncio
import json
import httpx
from scrapers.base import BaseScraper, Job
import config


class GreenhouseScraper(BaseScraper):
    PLATFORM = "greenhouse"
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        url = self.BASE_URL.format(company=company_slug)
        try:
            async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
                response = await client.get(url)

                if response.status_code == 404:
                    print(f"[WARN] greenhouse/{company_slug}: company not found on Greenhouse (404)")
                    return []

                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as e:
            print(f"[ERROR] greenhouse/{company_slug}: HTTP error {e.response.status_code} — {e}")
            return []
        except httpx.RequestError as e:
            print(f"[ERROR] greenhouse/{company_slug}: connection error — {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"[ERROR] greenhouse/{company_slug}: failed to parse JSON — {e}")
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
                company=company_slug.replace("-", " ").title(),
                location=location,
                url=raw.get("absolute_url", ""),
                platform=self.PLATFORM,
                posted_at=raw.get("updated_at", "Unknown"),
            )
            jobs.append(job)

        print(f"[OK] greenhouse/{company_slug}: {len(jobs)} jobs found")
        await asyncio.sleep(0)  # yield control back to event loop
        return jobs
