"""Public HTML and RSS sources for beauty, CPG and consulting jobs."""

import asyncio
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

import config
from scrapers.base import BaseScraper, Job
from scrapers.fetch import fetch_response, fetch_text, make_client
from scrapers.markdown_table import parse_month_day, parse_relative_age

CHANNEL = "beauty-finance"
PLATFORMS = frozenset({"jobright-finance", "shiseido", "coty", "manifest", "management-consulted"})
COTY = "https://careers.coty.com/search/"
SHISEIDO = "https://careers.shiseido.com/go/Americas/3846901/"
MANIFEST = "https://jobs.getmanifest.ai/industries/beauty"
CONSULTING_RSS = "https://jobs.managementconsulted.com/rss/jobs"


def _text(node, selector: str) -> str:
    value = node.select_one(selector)
    return value.get_text(" ", strip=True) if value else ""


def _job_url(page_url: str, href: str) -> str:
    if not href:
        return ""
    url = urljoin(page_url, href)
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.netloc == urlsplit(page_url).netloc else ""


def parse_employer_page(html: str, url: str, platform: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    if platform == "shiseido":
        company, rows = "Shiseido", "tr.data-row"
        location, posted = ".colFacility .jobFacility", ".colDate .jobDate"
    else:
        company, rows = "Coty", ".job-row .sub-section-desktop"
        location, posted = ".location > div", ".date > div"

    jobs = []
    for row in soup.select(rows):
        link = row.select_one("a.jobTitle-link[href]")
        if not link:
            continue
        job_url = _job_url(url, link["href"])
        date = parse_month_day(_text(row, posted))
        if job_url and date != "Unknown":
            req = urlsplit(job_url).path.rstrip("/").rsplit("/", 1)[-1]
            jobs.append(Job(
                f"{platform}-{req}", link.get_text(" ", strip=True), company,
                _text(row, location) or "Unknown", job_url, platform, date,
            ))
    return jobs


def parse_manifest_page(html: str, url: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("article.job-card"):
        link = card.select_one("a.job-title[href]")
        if not link:
            continue
        job_url = _job_url(url, link["href"])
        company = _text(card, ".job-brand")
        age = _text(card, ".job-posted")
        posted = (
            parse_relative_age(age)
            if re.fullmatch(r"\d+\s*[mhdw]\s+ago", age, re.IGNORECASE)
            else "Unknown"
        )
        if job_url and company and posted != "Unknown":
            jobs.append(Job(
                f"manifest-{urlsplit(job_url).path.rsplit('/', 1)[-1]}",
                link.get_text(" ", strip=True), company,
                _text(card, ".job-meta > span:not(.dot)") or "Unknown",
                job_url, "manifest", posted,
            ))
    return jobs


def parse_consulting_feed(xml: str) -> list[Job]:
    try:
        items = ElementTree.fromstring(xml).findall("./channel/item")
    except ElementTree.ParseError:
        print("[WARN] management-consulted: invalid RSS")
        return []
    jobs = []
    for item in items:
        company = item.findtext("company", "").strip()
        title = item.findtext("title", "").strip().removesuffix(f" at {company}")
        url = _job_url(CONSULTING_RSS, item.findtext("link", ""))
        req = item.findtext("guid", "").strip()
        raw_date = item.findtext("pubDate", "")
        try:
            posted = datetime.fromisoformat(raw_date)
        except ValueError:
            try:
                posted = parsedate_to_datetime(raw_date)
            except ValueError:
                continue
        date = (posted if posted.tzinfo else posted.date()).isoformat()
        if title and company and req.isdigit() and "/jobs/" in url:
            jobs.append(Job(
                f"management-consulted-{req}", title, company,
                item.findtext("location", "").strip() or "Unknown", url,
                "management-consulted", date, item.findtext("description", ""),
            ))
    return jobs


class PublicBeautyFinanceScraper(BaseScraper):
    """Read career pages and a consulting discovery feed in the shared run."""

    PLATFORM = "beauty-finance-public"

    async def fetch_jobs(self, company_slug: str = "") -> list[Job]:
        async with make_client() as client:
            async def collect(platform, url, parser, page_urls):
                jobs = {}
                visited = set()
                while url and url not in visited and len(visited) < 10:
                    visited.add(url)
                    html = await fetch_text(client, url, platform)
                    if html is None:
                        break
                    page = parser(html, url)
                    added = [job for job in page if job.id not in jobs]
                    if not added:
                        break
                    jobs.update((job.id, job) for job in added)
                    url = page_urls(html, url, len(visited))
                print(f"[OK] {platform}: {len(jobs)} jobs from {len(visited)} page(s)")
                return list(jobs.values())

            def coty_next(html, url, page):
                soup = BeautifulSoup(html, "html.parser")
                count = re.search(r"of ([\d,]+) Jobs", _text(soup, "#tile-search-results-label"))
                return f"{COTY}?startrow={page * 50}" if count and page * 50 < int(count[1].replace(",", "")) else ""

            def shiseido_next(html, url, page):
                link = BeautifulSoup(html, "html.parser").select_one(f'a[title="Page {page + 1}"][href]')
                return _job_url(url, link["href"]) if link else ""

            def manifest_next(html, url, page):
                link = BeautifulSoup(html, "html.parser").select_one(f'a[href$="?page={page + 1}"]')
                return _job_url(url, link["href"]) if link else ""

            async def consulting():
                html = await fetch_text(client, CONSULTING_RSS, "management-consulted")
                jobs = parse_consulting_feed(html) if html else []
                print(f"[OK] management-consulted: {len(jobs)} discovery rows")
                return jobs

            groups = await asyncio.gather(
                collect("coty", COTY, lambda html, url: parse_employer_page(html, url, "coty"), coty_next),
                collect("shiseido", SHISEIDO, lambda html, url: parse_employer_page(html, url, "shiseido"), shiseido_next),
                *(collect("manifest", f"{MANIFEST}/{section}", parse_manifest_page, manifest_next)
                  for section in ("product", "operations", "finance")),
                consulting(),
            )
        return list({job.id: job for group in groups for job in group}.values())


_APPLY_URL = re.compile(r'"applyUrl"\s*:\s*("(?:[^"\\]|\\.)*")')
_EXPERIENCE = re.compile(
    r"(?<![\w.])(?P<minimum>\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"(?:[ \t]*\(\d+\))?(?:[ \t]*(?:[-–—]|to)[ \t]*(?:\d{1,2}|one|two|three|four|five)"
    r"(?:[ \t]*\(\d+\))?)?[ \t]*\+?[ \t]*(?:years?|yrs?)\b"
    r"(?:['’]|[ \t]+of)?(?:[ \t]+[\w/-]+){0,7}?[ \t]+experience\b",
    re.I,
)
_YEAR_WORDS = dict(zip("zero one two three four five six seven eight nine ten".split(), range(11)))
_BEAUTY_TITLE = re.compile(
    r"\b(?:product (?:design[/ ]+)?develop(?:ment|er)|npd|pd coordinator|cosmetics?|beauty coordinator|"
    r"skin\s?care development|formulation|packag(?:e|ing) (?:development|coordinator|engineer|artwork)|"
    r"technical packaging|associate researcher)\b", re.I,
)
_BEAUTY_CONTEXT = re.compile(
    r"\b(?:beauty|cosmetics?|skincare|skin care|hair care|personal care|fragrance|"
    r"consumer packaged goods|cpg|inci)\b", re.I,
)
_BEAUTY_EMPLOYER = re.compile(
    r"\b(?:Coty|Shiseido|Tarte|Club Kuma|Glossier|Sol de Janeiro|Supergoop|Tatcha|"
    r"Harry.?s|Flamingo|Dollar Shave Club|Grove Collaborative|e\.l\.f|ELF Beauty|"
    r"P&G|Procter|Unilever|Kraft Heinz|Revlon|Haus Labs|Hourglass|Nutrafol|Morphe)\b", re.I,
)
_NON_BEAUTY_TITLE = re.compile(r"\b(?:robots?|robotics|apparel|footwear|aerospace|automotive)\b", re.I)
_FINANCE_CONTEXT_REQUIRED = re.compile(r"\b(?:business analyst|financial consultant)\b", re.I)
_FINANCE_CONTEXT = re.compile(
    r"\b(?:fp&a|financial (?:planning|model(?:ing|ling)|analys[ie]s)|variance analysis|"
    r"budget(?:ing| forecasts?)|management consulting|strategy consulting|commercial due diligence|"
    r"corporate strategy|business strategy|strategic planning|strategy (?:&|and) analytics)\b", re.I,
)


def fits_beauty_finance(job: Job) -> bool:
    """Check role context and explicit minimum years in the posting."""
    soup = BeautifulSoup(unescape(job.description), "html.parser")
    for separator in soup.select("br"):
        separator.replace_with("\n")
    for block in soup.select("li, p, h2, h3, h4"):
        block.insert_after("\n")
    text = soup.get_text().strip()
    if _FINANCE_CONTEXT_REQUIRED.search(job.title) and not _FINANCE_CONTEXT.search(f"{job.title} {text}"):
        return False
    if _BEAUTY_TITLE.search(job.title):
        if _NON_BEAUTY_TITLE.search(job.title):
            return False
        if not (job.platform == "manifest" or _BEAUTY_EMPLOYER.search(job.company)
                or _BEAUTY_CONTEXT.search(f"{job.title} {text}")):
            return False

    for match in _EXPERIENCE.finditer(text):
        minimum = match["minimum"].lower()
        years = int(minimum) if minimum.isdigit() else _YEAR_WORDS[minimum]
        after = re.split(r"[.\n;]", text[match.end():match.end() + 55], maxsplit=1)[0]
        before = text[max(0, match.start() - 20):match.start()].rsplit("\n", 1)[-1]
        preferred = re.search(r"\b(?:preferred|desirable|a plus)\b", f"{before} {after}", re.I)
        if years > 2 and not preferred:
            return False
    return True


def _posting(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def _manifest_apply_url(html: str) -> str:
    match = _APPLY_URL.search(html.replace(r'\"', '"'))
    if match:
        url = json.loads(match[1])
        if urlsplit(url).scheme == "https" and urlsplit(url).netloc != urlsplit(MANIFEST).netloc:
            return url
    return ""


def _expired(posting: dict) -> bool:
    try:
        expires = datetime.fromisoformat(posting.get("validThrough") or "")
    except (TypeError, ValueError):
        return False
    return expires.replace(tzinfo=expires.tzinfo or timezone.utc) < datetime.now(timezone.utc)


async def review_beauty_finance(jobs: list[Job]) -> list[Job]:
    """Enrich title matches with public posting text before eligibility checks."""
    limit = asyncio.Semaphore(6)
    async with make_client(timeout=min(config.REQUEST_TIMEOUT, 12)) as client:
        async def review(job):
            if job.platform == "management-consulted":
                if not fits_beauty_finance(job):
                    return None
                req = job.id.rsplit("-", 1)[-1]
                apply_page = urljoin(CONSULTING_RSS, f"/jobs/{req}/apply")
                async with limit:
                    response = await fetch_response(client, apply_page, job.platform)
                if response is None or response.url.host == urlsplit(CONSULTING_RSS).hostname:
                    return None
                soup = BeautifulSoup(response.text, "html.parser")
                posting = _posting(soup)
                if not posting or _expired(posting):
                    return None
                job = replace(
                    job, url=str(response.url), description=posting.get("description") or "",
                    posted_at=posting.get("datePosted") or job.posted_at,
                )

            host = urlsplit(job.url).hostname or ""
            if not job.description and (
                job.platform in PLATFORMS
                or host in {"jobright.ai", "www.amazon.jobs", "amazon.jobs"}
                or host.endswith(".myworkdayjobs.com")
            ):
                async with limit:
                    response = await fetch_response(client, job.url, job.platform)
                if response is None:
                    return None
                soup = BeautifulSoup(response.text, "html.parser")
                posting = _posting(soup)
                if _expired(posting):
                    return None
                description = posting.get("description") or _text(soup, ".jobdescription, [data-automation-id='jobPostingDescription']")
                if job.platform in {"jobright-finance", "manifest", "coty", "shiseido"} and not description:
                    return None
                if job.platform == "manifest":
                    apply_url = _manifest_apply_url(response.text)
                    if not apply_url:
                        return None
                    job = replace(job, url=apply_url, posted_at=posting.get("datePosted") or job.posted_at)
                job = replace(job, description=description)

            return job if fits_beauty_finance(job) else None

        reviewed = await asyncio.gather(*(review(job) for job in jobs))
    kept = [job for job in reviewed if job is not None]
    print(f"[INFO] {CHANNEL}: kept {len(kept)} of {len(jobs)} title matches after posting checks")
    return kept
