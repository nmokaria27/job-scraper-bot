"""
Scrapers for job lists published as markdown tables in GitHub READMEs
(speedyapply, jobright-ai, zapplyjobs).

The parser is header-driven: it reads each table's header row to find the
company / title / location / link / posted columns, so tables with or without
a Salary column (speedyapply "FAANG+" vs. its main table), tables with the
link inside the title cell (jobright), and tables with a plain-text company
cell (zapplyjobs) all parse. The previous fixed-index parser required six
columns and silently dropped ~75% of speedyapply rows.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

import config
from scrapers.base import BaseScraper, Job
from scrapers.fetch import fetch_text, make_client

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')
# Link text may itself contain one level of brackets: "[[2027] APM, Early Career](url)".
_MD_LINK_RE = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(([^)\s]+)\)")
_RELATIVE_AGE_RE = re.compile(r"^(\d+)\s*([mhdw])")

# Cells that mean "same company as the row above".
CONTINUATION_MARKERS: set[str] = {"↳", "└", "└─", "→", "〃", '"', "''", "same"}

COLUMN_ALIASES: dict[str, set[str]] = {
    "company": {"company", "employer", "organization"},
    "title": {"position", "role", "job title", "title", "job"},
    "location": {"location", "locations", "city"},
    "link": {"posting", "apply", "link", "application", "application/link", "url", "apply link"},
    "posted": {"age", "posted", "date posted", "date", "added", "posted on", "date added"},
}


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


def strip_markup(text: str) -> str:
    """Reduce an HTML/markdown table cell to its visible text."""
    text = _HTML_TAG_RE.sub("", text or "")
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", text).strip()


def first_url(cell: str) -> str:
    """Extract the first link target from a markdown/HTML cell."""
    if not cell:
        return ""
    md = _MD_LINK_RE.search(cell)
    if md:
        return md.group(2).strip()
    href = _HREF_RE.search(cell)
    if href:
        return href.group(1).strip()
    return ""


def parse_relative_age(text: str) -> str:
    """'14m', '12h', '3d', '2w' -> ISO timestamp; anything else -> 'Unknown'."""
    match = _RELATIVE_AGE_RE.match((text or "").strip().lower())
    if not match:
        return "Unknown"
    value = int(match.group(1))
    unit = match.group(2)
    delta = {
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
        "w": timedelta(weeks=value),
    }[unit]
    return (datetime.now(tz=timezone.utc) - delta).isoformat()


def parse_month_day(text: str) -> str:
    """
    'Sep 04' / 'September 4' / '2026-09-04' -> 'YYYY-MM-DD' (date-only, so
    the recency filter grants it a day of grace); anything else -> 'Unknown'.
    """
    raw = (text or "").strip()
    if not raw:
        return "Unknown"
    now = datetime.now(tz=timezone.utc)
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%Y %b %d", "%Y %B %d"):
        try:
            parsed = datetime.strptime(f"{now.year} {raw}", fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if parsed > now + timedelta(days=1):
            parsed = parsed.replace(year=now.year - 1)
        return parsed.date().isoformat()
    return "Unknown"


def parse_posted_cell(text: str) -> str:
    """Accept either a relative age ('3d') or a month/day ('Sep 04')."""
    cleaned = strip_markup(text)
    relative = parse_relative_age(cleaned)
    if relative != "Unknown":
        return relative
    return parse_month_day(cleaned)


def map_columns(header_cells: list[str]) -> dict[str, int] | None:
    """Map logical column names to indices. None if this isn't a job table."""
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(header_cells):
        name = strip_markup(raw).lower()
        for logical, aliases in COLUMN_ALIASES.items():
            if logical not in mapping and name in aliases:
                mapping[logical] = idx
                break
    if "company" not in mapping or "title" not in mapping:
        return None
    return mapping


def parse_markdown_tables(text: str, platform: str, id_prefix: str) -> list[Job]:
    """Parse every job table in a README into Job objects."""
    jobs: list[Job] = []
    in_table = False
    colmap: dict[str, int] | None = None
    last_company = ""

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            colmap = None
            last_company = ""
            continue

        cells = _split_table_row(stripped)

        if not in_table:
            in_table = True
            colmap = map_columns(cells)
            continue

        if _is_separator_row(stripped) or colmap is None:
            continue

        job = _row_to_job(cells, colmap, last_company, platform, id_prefix)
        if job:
            last_company = job.company
            jobs.append(job)

    return jobs


def _row_to_job(
    cells: list[str],
    colmap: dict[str, int],
    last_company: str,
    platform: str,
    id_prefix: str,
) -> Job | None:
    def cell(key: str) -> str:
        idx = colmap.get(key)
        if idx is None or idx >= len(cells):
            return ""
        return cells[idx]

    company = strip_markup(cell("company"))
    if not company or company in CONTINUATION_MARKERS:
        company = last_company
    if not company:
        return None

    title_raw = cell("title")
    title = strip_markup(title_raw)
    if not title:
        return None

    url = first_url(cell("link")) or first_url(title_raw) or first_url(cell("company"))
    location = strip_markup(cell("location")) or "Unknown"
    posted_at = parse_posted_cell(cell("posted"))

    digest = hashlib.md5(f"{company}|{title}|{url}".encode()).hexdigest()[:12]
    return Job(
        id=f"{id_prefix}-{digest}",
        title=title,
        company=company,
        location=location,
        url=url,
        platform=platform,
        posted_at=posted_at,
    )


class MarkdownTableScraper(BaseScraper):
    """Base class: fetch each configured README and parse its job tables."""

    PLATFORM = "markdown"
    ID_PREFIX = "markdown"

    def urls(self) -> list[str]:
        raise NotImplementedError

    def _parse_tables(self, text: str) -> list[Job]:
        return parse_markdown_tables(text, self.PLATFORM, self.ID_PREFIX)

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        seen_ids: set[str] = set()
        all_jobs: list[Job] = []

        async with make_client() as client:
            for url in self.urls():
                parts = url.split("/")
                label = f"{self.PLATFORM} ({parts[3]}/{parts[4]})" if len(parts) > 4 else f"{self.PLATFORM} ({url})"
                text = await fetch_text(client, url, label)
                if text is None:
                    continue
                jobs = self._parse_tables(text)
                print(f"[OK] {label}: {len(jobs)} jobs found")
                for job in jobs:
                    if job.id not in seen_ids:
                        seen_ids.add(job.id)
                        all_jobs.append(job)

        return all_jobs


class SpeedyApplyScraper(MarkdownTableScraper):
    """speedyapply/2027-SWE-College-Jobs and 2027-AI-College-Jobs."""

    PLATFORM = "speedyapply"
    ID_PREFIX = "speedyapply"

    def urls(self) -> list[str]:
        return config.SPEEDYAPPLY_URLS

    # Kept for backwards compatibility with older tests/callers.
    @staticmethod
    def _parse_age(age: str) -> str:
        return parse_relative_age(age)


class JobRightScraper(MarkdownTableScraper):
    """jobright-ai/* README tables (PM internship, PM new grad, SWE new grad)."""

    PLATFORM = "jobright"
    ID_PREFIX = "jobright"

    def urls(self) -> list[str]:
        return config.JOBRIGHT_URLS

    @staticmethod
    def _parse_date(date_str: str) -> str:
        return parse_month_day(date_str)


class ZapplyScraper(MarkdownTableScraper):
    """zapplyjobs/New-Grad-Jobs-2027 — regenerated every ~15 minutes."""

    PLATFORM = "zapply"
    ID_PREFIX = "zapply"

    def urls(self) -> list[str]:
        return config.ZAPPLY_URLS
