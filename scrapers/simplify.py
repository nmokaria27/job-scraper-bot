import json
from datetime import datetime, timezone
import httpx
from scrapers.base import BaseScraper, Job
import config

LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/"
    "Summer2026-Internships/dev/.github/scripts/listings.json"
)


class SimplifyScraper(BaseScraper):
    PLATFORM = "simplify"

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        """
        Fetches all jobs from the SimplifyJobs GitHub JSON in one request.
        company_slug is ignored — this endpoint returns all listings at once.
        """
        try:
            async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
                response = await client.get(LISTINGS_URL)
                response.raise_for_status()
                entries = response.json()

        except httpx.HTTPStatusError as e:
            print(f"[ERROR] simplify: HTTP error {e.response.status_code} — {e}")
            return []
        except httpx.RequestError as e:
            print(f"[ERROR] simplify: connection error — {e}")
            return []
        except json.JSONDecodeError as e:
            print(f"[ERROR] simplify: failed to parse JSON — {e}")
            return []

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

        print(f"[OK] simplify: {len(jobs)} active jobs found")
        return jobs
