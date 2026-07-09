import hashlib
import re
from datetime import datetime, timedelta, timezone

import httpx

import config
from scrapers.base import BaseScraper, Job


def _split_table_row(row: str) -> list[str]:
    """Split a markdown table row by pipe, removing empty edge cells."""
    cells = [c.strip() for c in row.split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_separator_row(row: str) -> bool:
    """Return True if the row is a markdown table separator (|---|---|)."""
    return bool(re.match(r"^\|[\s\-:|]+\|$", row.strip()))


class SpeedyApplyScraper(BaseScraper):
    """Parse markdown job tables from speedyapply GitHub repos."""

    PLATFORM = "speedyapply"

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> list[Job]:
        try:
            response = await client.get(url)
            response.raise_for_status()
            text = response.text
        except httpx.HTTPStatusError as e:
            print(f"[ERROR] speedyapply ({url}): HTTP {e.response.status_code} — {e}")
            return []
        except httpx.RequestError as e:
            print(f"[ERROR] speedyapply ({url}): connection error — {e}")
            return []

        label = url.split("/")[4] if len(url.split("/")) > 4 else url
        jobs = self._parse_tables(text)
        print(f"[OK] speedyapply ({label}): {len(jobs)} jobs found")
        return jobs

    def _parse_tables(self, text: str) -> list[Job]:
        jobs: list[Job] = []
        lines = text.split("\n")
        in_table = False
        skip_next = 0  # skip header + separator

        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("|"):
                in_table = False
                skip_next = 0
                continue

            if not in_table:
                in_table = True
                skip_next = 2  # header + separator
                continue

            if skip_next > 0:
                skip_next -= 1
                continue

            if _is_separator_row(stripped):
                continue

            job = self._parse_row(stripped)
            if job:
                jobs.append(job)

        return jobs

    def _parse_row(self, row: str) -> Job | None:
        cells = _split_table_row(row)
        if len(cells) < 6:
            return None

        company_cell = cells[0]
        position = cells[1]
        location = cells[2]
        posting_cell = cells[4]
        age_cell = cells[5]

        company_match = re.search(
            r'<a href="([^"]*)"><strong>([^<]+)</strong></a>', company_cell
        )
        if not company_match:
            return None
        company = company_match.group(2).strip()

        url_match = re.search(r'<a href="([^"]*)"><img', posting_cell)
        url = url_match.group(1).strip() if url_match else ""

        if not position:
            return None

        posted_at = self._parse_age(age_cell)
        job_id = f"speedyapply-{hashlib.md5(f'{company}|{position}|{url}'.encode()).hexdigest()[:12]}"

        return Job(
            id=job_id,
            title=position,
            company=company,
            location=location or "Unknown",
            url=url,
            platform=self.PLATFORM,
            posted_at=posted_at,
        )

    @staticmethod
    def _parse_age(age: str) -> str:
        match = re.match(r"(\d+)([hdw])", age.strip())
        if not match:
            return "Unknown"
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            delta = timedelta(hours=value)
        elif unit == "d":
            delta = timedelta(days=value)
        elif unit == "w":
            delta = timedelta(weeks=value)
        else:
            return "Unknown"
        return (datetime.now(tz=timezone.utc) - delta).isoformat()

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        seen_ids: set[str] = set()
        all_jobs: list[Job] = []

        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            for url in config.SPEEDYAPPLY_URLS:
                jobs = await self._fetch_one(client, url)
                for job in jobs:
                    if job.id not in seen_ids:
                        seen_ids.add(job.id)
                        all_jobs.append(job)

        return all_jobs


class JobRightScraper(BaseScraper):
    """Parse markdown job tables from jobright-ai GitHub repos."""

    PLATFORM = "jobright"

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> list[Job]:
        try:
            response = await client.get(url)
            response.raise_for_status()
            text = response.text
        except httpx.HTTPStatusError as e:
            print(f"[ERROR] jobright ({url}): HTTP {e.response.status_code} — {e}")
            return []
        except httpx.RequestError as e:
            print(f"[ERROR] jobright ({url}): connection error — {e}")
            return []

        label = url.split("/")[4] if len(url.split("/")) > 4 else url
        jobs = self._parse_tables(text)
        print(f"[OK] jobright ({label}): {len(jobs)} jobs found")
        return jobs

    def _parse_tables(self, text: str) -> list[Job]:
        jobs: list[Job] = []
        lines = text.split("\n")
        in_table = False
        skip_next = 0
        last_company = ""

        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("|"):
                in_table = False
                skip_next = 0
                last_company = ""
                continue

            if not in_table:
                in_table = True
                skip_next = 2
                continue

            if skip_next > 0:
                skip_next -= 1
                continue

            if _is_separator_row(stripped):
                continue

            job = self._parse_row(stripped, last_company)
            if job:
                last_company = job.company
                jobs.append(job)

        return jobs

    def _parse_row(self, row: str, fallback_company: str) -> Job | None:
        cells = _split_table_row(row)
        if len(cells) < 5:
            return None

        company_cell = cells[0]
        position_cell = cells[1]
        location = cells[2]
        date_cell = cells[4]

        company_match = re.search(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*", company_cell)
        if company_match:
            company = company_match.group(1).strip()
        elif company_cell.strip() == "↳" and fallback_company:
            company = fallback_company
        else:
            return None

        position_match = re.search(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*", position_cell)
        if not position_match:
            return None

        title = position_match.group(1).strip()
        url = position_match.group(2).strip()

        if not title:
            return None

        posted_at = self._parse_date(date_cell)
        job_id = f"jobright-{hashlib.md5(f'{company}|{title}|{url}'.encode()).hexdigest()[:12]}"

        return Job(
            id=job_id,
            title=title,
            company=company,
            location=location or "Unknown",
            url=url,
            platform=self.PLATFORM,
            posted_at=posted_at,
        )

    @staticmethod
    def _parse_date(date_str: str) -> str:
        date_str = date_str.strip()
        if not date_str:
            return "Unknown"
        try:
            now = datetime.now(tz=timezone.utc)
            parsed = datetime.strptime(f"{now.year} {date_str}", "%Y %b %d")
            parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed > now:
                parsed = parsed.replace(year=now.year - 1)
            return parsed.isoformat()
        except ValueError:
            return "Unknown"

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        seen_ids: set[str] = set()
        all_jobs: list[Job] = []

        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            for url in config.JOBRIGHT_URLS:
                jobs = await self._fetch_one(client, url)
                for job in jobs:
                    if job.id not in seen_ids:
                        seen_ids.add(job.id)
                        all_jobs.append(job)

        return all_jobs
