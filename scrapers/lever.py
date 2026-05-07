import asyncio
import json
from datetime import datetime, timezone
import httpx
from scrapers.base import BaseScraper, Job
import config


class LeverScraper(BaseScraper):
    PLATFORM = "lever"
    BASE_URL = "https://api.lever.co/v0/postings/{company}?mode=json"

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        url = self.BASE_URL.format(company=company_slug)
        try:
            async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
                response = await client.get(url)

                if response.status_code == 404:
                    print(f"[WARN] lever/{company_slug}: company not found on Lever (404)")
                    return []

                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as e:
            print(f"[ERROR] lever/{company_slug}: HTTP error {e.response.status_code} — {e}")
            return []
        except httpx.RequestError as e:
            print(f"[ERROR] lever/{company_slug}: connection error — {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"[ERROR] lever/{company_slug}: failed to parse JSON — {e}")
            return []

        jobs: list[Job] = []

        for raw in data:
            title = raw.get("text")
            if not title:
                continue

            categories = raw.get("categories") or {}
            location = categories.get("location") or "Remote / Not Specified"

            # createdAt is Unix timestamp in milliseconds
            created_at_ms = raw.get("createdAt")
            if created_at_ms:
                try:
                    posted_at = datetime.fromtimestamp(
                        created_at_ms / 1000, tz=timezone.utc
                    ).isoformat()
                except (ValueError, OSError):
                    posted_at = "Unknown"
            else:
                posted_at = "Unknown"

            job = Job(
                id=f"lever-{company_slug}-{raw['id']}",
                title=title,
                company=company_slug.replace("-", " ").title(),
                location=location,
                url=raw.get("hostedUrl", ""),
                platform=self.PLATFORM,
                posted_at=posted_at,
            )
            jobs.append(job)

        print(f"[OK] lever/{company_slug}: {len(jobs)} jobs found")
        await asyncio.sleep(0)  # yield control back to event loop
        return jobs
