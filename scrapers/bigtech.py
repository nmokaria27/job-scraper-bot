"""
Direct scrapers for big-tech career sites that expose unauthenticated JSON
but are not on Greenhouse / Lever / Ashby:

- Amazon      amazon.jobs search.json          (SDE / applied scientist / PM)
- Workday     <tenant>.wd<N>.myworkdayjobs.com  (NVIDIA, Salesforce, Adobe, ...)

Both are undocumented endpoints; every scraper here returns [] on any
failure and the orchestrator isolates crashes, so a silent API change costs
coverage, not the run.
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import config
from scrapers.base import BaseScraper, Job
from scrapers.fetch import fetch_json, make_client

# amazon.jobs rejects the default bot UA with a 403; a browser UA is fine.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 job-scraper-bot/1.0"
    ),
    "Accept": "application/json",
}


def _today() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------

AMAZON_SEARCH_URL = (
    "https://www.amazon.jobs/en/search.json?base_query={query}"
    "&sort=recent&result_limit=100&offset=0&country=USA"
)


def parse_amazon_date(raw: str) -> str:
    """'September  4, 2026' -> '2026-09-04' (date-only)."""
    cleaned = re.sub(r"\s+", " ", (raw or "")).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return "Unknown"


def parse_amazon_jobs(payload: object) -> list[Job]:
    if not isinstance(payload, dict):
        return []
    jobs: list[Job] = []
    for raw in payload.get("jobs") or []:
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        job_id = raw.get("id_icims") or raw.get("id") or raw.get("job_path")
        path = raw.get("job_path") or ""
        jobs.append(
            Job(
                id=f"amazon-{job_id}",
                title=title,
                company="Amazon",
                location=raw.get("normalized_location") or raw.get("location") or "Unknown",
                url=f"https://www.amazon.jobs{path}" if path.startswith("/") else path,
                platform="amazon",
                posted_at=parse_amazon_date(raw.get("posted_date") or ""),
            )
        )
    return jobs


class AmazonScraper(BaseScraper):
    PLATFORM = "amazon"

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        seen: set[str] = set()
        all_jobs: list[Job] = []
        async with make_client(headers=_BROWSER_HEADERS) as client:
            for query in config.AMAZON_QUERIES:
                url = AMAZON_SEARCH_URL.format(query=quote(query))
                payload = await fetch_json(client, url, f"amazon ({query})")
                for job in parse_amazon_jobs(payload):
                    if job.id not in seen:
                        seen.add(job.id)
                        all_jobs.append(job)
        print(f"[OK] amazon: {len(all_jobs)} jobs found across {len(config.AMAZON_QUERIES)} queries")
        return all_jobs


# ---------------------------------------------------------------------------
# Workday (CXS JSON API)
#
# Microsoft was evaluated too (apply.careers.microsoft.com/api/pcsx/search)
# but that endpoint ignores its query and paging parameters and always returns
# the same 10 postings, so it was left out. Microsoft new-grad roles still
# arrive via the gradtracker / speedyapply / zapply feeds.
# ---------------------------------------------------------------------------

WORKDAY_JOBS_URL = "https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
WORKDAY_JOB_PAGE = "https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}"
WORKDAY_PAGE_SIZE = 20
_POSTED_DAYS_RE = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.IGNORECASE)


def parse_workday_posted(raw: str) -> str:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' -> 'YYYY-MM-DD'."""
    text = (raw or "").strip().lower()
    if not text:
        return "Unknown"
    today = _today().date()
    if "today" in text:
        return today.isoformat()
    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
    match = _POSTED_DAYS_RE.search(text)
    if match:
        days = int(match.group(1))
        if "+" in text:
            days += 1
        return (today - timedelta(days=days)).isoformat()
    return "Unknown"


def parse_workday_jobs(payload: object, tenant: dict) -> list[Job]:
    if not isinstance(payload, dict):
        return []
    jobs: list[Job] = []
    for raw in payload.get("jobPostings") or []:
        title = (raw.get("title") or "").strip()
        path = raw.get("externalPath") or ""
        if not title or not path:
            continue
        bullets = raw.get("bulletFields") or []
        req_id = bullets[0] if bullets else path.rsplit("/", 1)[-1]
        jobs.append(
            Job(
                id=f"workday-{tenant['tenant']}-{req_id}",
                title=title,
                company=tenant["label"],
                location=raw.get("locationsText") or "Unknown",
                url=WORKDAY_JOB_PAGE.format(
                    tenant=tenant["tenant"], wd=tenant["wd"], site=tenant["site"], path=path
                ),
                platform="workday",
                posted_at=parse_workday_posted(raw.get("postedOn") or ""),
            )
        )
    return jobs


class WorkdayScraper(BaseScraper):
    PLATFORM = "workday"

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        seen: set[str] = set()
        all_jobs: list[Job] = []
        async with make_client(headers=_BROWSER_HEADERS) as client:
            for tenant in config.WORKDAY_TENANTS:
                url = WORKDAY_JOBS_URL.format(**tenant)
                found = 0
                for query in config.WORKDAY_QUERIES:
                    for page in range(config.WORKDAY_PAGES):
                        body = {
                            "appliedFacets": {},
                            "limit": WORKDAY_PAGE_SIZE,
                            "offset": page * WORKDAY_PAGE_SIZE,
                            "searchText": query,
                        }
                        payload = await fetch_json(
                            client, url, f"workday/{tenant['tenant']} ({query})",
                            method="POST", json_body=body,
                        )
                        jobs = parse_workday_jobs(payload, tenant)
                        for job in jobs:
                            if job.id not in seen:
                                seen.add(job.id)
                                all_jobs.append(job)
                                found += 1
                        if len(jobs) < WORKDAY_PAGE_SIZE:
                            break
                print(f"[OK] workday/{tenant['tenant']}: {found} jobs found")
        return all_jobs
