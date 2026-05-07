import json
from datetime import datetime, timezone
import httpx
from scrapers.base import BaseScraper, Job
import config


class SimplifyScraper(BaseScraper):
    PLATFORM = "simplify"

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> list[Job]:
        """Fetch and parse jobs from a single SimplifyJobs JSON URL."""
        try:
            response = await client.get(url)
            response.raise_for_status()
            entries = response.json()
        except httpx.HTTPStatusError as e:
            print(f"[ERROR] simplify ({url}): HTTP error {e.response.status_code} — {e}")
            return []
        except httpx.RequestError as e:
            print(f"[ERROR] simplify ({url}): connection error — {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"[ERROR] simplify ({url}): failed to parse JSON — {e}")
            return []

        # Use the last path segment of the URL as a short label for logs
        label = url.split("/")[4] if len(url.split("/")) > 4 else url

        jobs: list[Job] = []
        for entry in entries:
            # Only include active and visible listings
            if not entry.get("active") or not entry.get("is_visible"):
                continue

            title = entry.get("title")
            if not title:
                continue

            # Join the locations list into a single string
            raw_locations = entry.get("locations") or []
            location = " / ".join(raw_locations) if raw_locations else "Unknown"

            # date_posted is Unix timestamp in seconds
            date_posted = entry.get("date_posted")
            if date_posted:
                try:
                    posted_at = datetime.fromtimestamp(
                        date_posted, tz=timezone.utc
                    ).isoformat()
                except (ValueError, OSError):
                    posted_at = "Unknown"
            else:
                posted_at = "Unknown"

            job = Job(
                id=f"simplify-{entry['id']}",
                title=title,
                company=entry.get("company_name", "Unknown"),
                location=location,
                url=entry.get("url", ""),
                platform=self.PLATFORM,
                posted_at=posted_at,
            )
            jobs.append(job)

        print(f"[OK] simplify ({label}): {len(jobs)} active jobs found")
        return jobs

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        """
        Fetch jobs from all URLs in config.SIMPLIFY_URLS.
        company_slug is ignored — each URL returns all listings for that repo.
        Deduplicates by job ID across URLs.
        """
        seen_ids: set[str] = set()
        all_jobs: list[Job] = []

        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            for url in config.SIMPLIFY_URLS:
                jobs = await self._fetch_one(client, url)
                for job in jobs:
                    if job.id not in seen_ids:
                        seen_ids.add(job.id)
                        all_jobs.append(job)

        return all_jobs
