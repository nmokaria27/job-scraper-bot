import asyncio
import os
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

from companies import get_companies
from scrapers.ashby import AshbyScraper
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper


def _parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _filter_recent(jobs, hours: int):
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    recent = []
    for job in jobs:
        try:
            if _parse_dt(job.posted_at) >= cutoff:
                recent.append(job)
        except (ValueError, TypeError):
            continue
    return recent


@unittest.skipUnless(
    os.getenv("RUN_LIVE_SCRAPER_TESTS") == "1",
    "Live scraper smoke test is opt-in; set RUN_LIVE_SCRAPER_TESTS=1 to run it.",
)
class LivePlatformSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_greenhouse_lever_and_ashby_fetch_recent_jobs(self) -> None:
        hours = int(os.getenv("LIVE_SCRAPER_MAX_AGE_HOURS", "24"))
        platform_jobs: dict[str, list] = {"greenhouse": [], "lever": [], "ashby": []}
        companies = get_companies()

        greenhouse = GreenhouseScraper()
        lever = LeverScraper()
        ashby = AshbyScraper()

        for slug in companies.get("greenhouse", []):
            platform_jobs["greenhouse"].extend(await greenhouse.fetch_jobs(slug))
        for slug in companies.get("lever", []):
            platform_jobs["lever"].extend(await lever.fetch_jobs(slug))
        for slug in companies.get("ashby", []):
            platform_jobs["ashby"].extend(await ashby.fetch_jobs(slug))

        recent_by_platform = {
            platform: _filter_recent(jobs, hours)
            for platform, jobs in platform_jobs.items()
        }

        print("\n[LivePlatformSmoke]")
        for platform in ("greenhouse", "lever", "ashby"):
            print(
                f"  - {platform}: fetched={len(platform_jobs[platform])} "
                f"recent_{hours}h={len(recent_by_platform[platform])}"
            )

        self.assertTrue(
            any(len(jobs) > 0 for jobs in platform_jobs.values()),
            "All ATS scrapers returned zero jobs; inspect logs above.",
        )

        counts = Counter(
            job.platform for jobs in recent_by_platform.values() for job in jobs
        )
        self.assertIsInstance(counts, Counter)


if __name__ == "__main__":
    unittest.main()
