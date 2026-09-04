from scrapers.base import BaseScraper, Job, company_name_from_slug, has_real_location
from scrapers.fetch import fetch_json, make_client


class GreenhouseScraper(BaseScraper):
    PLATFORM = "greenhouse"
    # `content=true` adds the `offices` list, which is the only way to get a
    # real place for boards that put "Hybrid" / "In-Office" in location.name
    # (Cloudflare does this for every posting).
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"

    @staticmethod
    def _location(raw: dict) -> str:
        name = ((raw.get("location") or {}).get("name") or "").strip()
        offices = [
            office.get("name", "").strip()
            for office in raw.get("offices") or []
            if has_real_location(office.get("name", ""))
        ]
        if has_real_location(name):
            return name
        if offices:
            joined = " / ".join(offices)
            return f"{joined} ({name})" if name else joined
        return name or "Remote / Not Specified"

    @classmethod
    def parse_job(cls, raw: dict, company_slug: str) -> Job | None:
        title = raw.get("title")
        if not title or raw.get("id") is None:
            return None
        return Job(
            id=f"greenhouse-{company_slug}-{raw['id']}",
            title=title,
            company=company_name_from_slug(company_slug),
            location=cls._location(raw),
            url=raw.get("absolute_url", ""),
            platform=cls.PLATFORM,
            # first_published is the true posting date; updated_at moves every
            # time the listing is edited and caused old jobs to re-surface.
            posted_at=raw.get("first_published") or raw.get("updated_at") or "Unknown",
        )

    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        url = self.BASE_URL.format(company=company_slug)
        label = f"greenhouse/{company_slug}"
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
