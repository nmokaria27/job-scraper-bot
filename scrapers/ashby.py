import asyncio
import json
from urllib.parse import quote
import httpx
from scrapers.base import BaseScraper, Job
import config

ASHBY_BASE = "https://jobs.ashbyhq.com"
ASHBY_API_BASE = "https://api.ashbyhq.com/posting-api/job-board"
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class AshbyScraper(BaseScraper):
    PLATFORM = "ashby"

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        board_name = quote(company_slug, safe="")
        url = f"{ASHBY_API_BASE}/{board_name}"
        data: dict | None = None
        max_attempts = max(1, config.REQUEST_RETRY_ATTEMPTS + 1)

        async with httpx.AsyncClient(
            timeout=config.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "job-scraper-bot/1.0"},
        ) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.get(url)

                    if response.status_code == 404:
                        print(f"[WARN] ashby/{company_slug}: company not found on Ashby (404)")
                        return []

                    if (
                        response.status_code in RETRYABLE_STATUS_CODES
                        and attempt < max_attempts
                    ):
                        print(
                            f"[WARN] ashby/{company_slug}: HTTP {response.status_code} "
                            f"on attempt {attempt}/{max_attempts}; retrying"
                        )
                        await asyncio.sleep(attempt)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    break

                except httpx.HTTPStatusError as e:
                    print(
                        f"[ERROR] ashby/{company_slug}: HTTP error "
                        f"{e.response.status_code} — {e}"
                    )
                    return []
                except httpx.RequestError as e:
                    if attempt < max_attempts:
                        print(
                            f"[WARN] ashby/{company_slug}: "
                            f"{type(e).__name__} on attempt {attempt}/{max_attempts}; retrying"
                        )
                        await asyncio.sleep(attempt)
                        continue
                    print(
                        f"[ERROR] ashby/{company_slug}: connection error "
                        f"({type(e).__name__}) — {e!r}"
                    )
                    return []
                except json.JSONDecodeError as e:
                    print(f"[ERROR] ashby/{company_slug}: failed to parse JSON — {e}")
                    return []

        if data is None:
            print(f"[ERROR] ashby/{company_slug}: no response data received after retries")
            return []

        jobs: list[Job] = []
        raw_jobs = data.get("jobs", [])

        for raw in raw_jobs:
            title = raw.get("title")
            if not title:
                continue

            location = raw.get("location") or "Remote / Not Specified"
            secondary_locations = [
                secondary.get("location")
                for secondary in raw.get("secondaryLocations", [])
                if secondary.get("location")
            ]
            if secondary_locations:
                location = " / ".join([location, *secondary_locations])

            job_url = raw.get("jobUrl", "")
            if job_url and not job_url.startswith("http"):
                job_url = f"{ASHBY_BASE}{job_url}"

            job = Job(
                id=f"ashby-{company_slug}-{raw.get('id') or raw.get('jobUrl')}",
                title=title,
                company=company_slug.replace("-", " ").title(),
                location=location,
                url=job_url,
                platform=self.PLATFORM,
                posted_at=raw.get("publishedDate") or raw.get("updatedAt") or "Unknown",
            )
            jobs.append(job)

        print(f"[OK] ashby/{company_slug}: {len(jobs)} jobs found")
        await asyncio.sleep(0)  # yield control back to event loop
        return jobs
