import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx

import main
from companies import COMPANIES
from config import ChannelConfig
from scrapers.base import Job
from scrapers.beauty_finance import (
    COTY,
    CONSULTING_RSS,
    fits_beauty_finance,
    parse_consulting_feed,
    parse_employer_page,
    review_beauty_finance,
)


def make_job(title, description="", platform="jobright-finance", url="https://jobright.ai/jobs/info/test"):
    return Job(
        "test", title, "Example", "New York, NY", url, platform,
        datetime.now(timezone.utc).isoformat(), description,
    )


class BeautyFinanceSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_switch_and_channel_scope(self):
        source_names = ("jobright-finance", "beauty-finance-public")
        companies = {name: COMPANIES[name] for name in source_names if name in COMPANIES}
        job = make_job("Financial Analyst")
        with (
            patch("main.get_companies", return_value=companies),
            patch("main.JobRightFinanceScraper.fetch_jobs", new=AsyncMock(return_value=[job])) as finance,
            patch("main.PublicBeautyFinanceScraper.fetch_jobs", new=AsyncMock(return_value=[])) as public,
        ):
            self.assertEqual(await main.scrape_all_raw(), ([], 0))
            finance.assert_not_awaited()
            public.assert_not_awaited()
            self.assertEqual(await main.scrape_all_raw(include_beauty_finance=True), ([job], 1))
            finance.assert_awaited_once()
            public.assert_awaited_once()
        channel = ChannelConfig(name="pm-jobs", webhook_url="", keywords=["financial analyst"])
        self.assertEqual(main.filter_for_channel([job], channel), [])

    def test_employer_listing_preserves_date_and_uses_desktop_row(self):
        html = """
        <div class="job-row">
          <div class="sub-section-desktop">
            <a class="jobTitle-link" href="/job/Packaging-Coordinator/123/">Packaging Coordinator</a>
            <div class="location"><span>Location</span><div>New York, NY, US</div></div>
            <div class="date"><span>Posted</span><div>Nov 18, 2026</div></div>
          </div>
          <a class="jobTitle-link" href="/job/Packaging-Coordinator/123/">Mobile duplicate</a>
        </div>
        """
        job, = parse_employer_page(html, COTY, "coty")
        self.assertEqual((job.id, job.location, job.posted_at),
                         ("coty-123", "New York, NY, US", "2026-11-18"))

    def test_experience_and_context_are_local_rules(self):
        self.assertTrue(fits_beauty_finance(make_job(
            "Financial Analyst", "Zero (0) to two (2) years of accounting or finance experience."
        )))
        self.assertFalse(fits_beauty_finance(make_job(
            "Associate Consultant", "<li>3+ years of relevant experience required</li><li>MBA preferred.</li>"
        )))
        self.assertTrue(fits_beauty_finance(make_job(
            "Associate Consultant", "<li>3 years of experience preferred</li><li>1 year of experience required</li>"
        )))
        self.assertFalse(fits_beauty_finance(make_job("Business Analyst", "Own logistics operations.")))
        self.assertTrue(fits_beauty_finance(make_job("Business Analyst", "Build financial models and own variance analysis.")))
        self.assertFalse(fits_beauty_finance(make_job("Product Development Associate", "Own BOM for an aircraft.")))
        self.assertTrue(fits_beauty_finance(make_job("Product Development Associate", "Support skincare formulation.")))

    async def test_review_reads_job_posting_and_rejects_expired_or_experienced(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        pages = {
            "/jobs/info/test": ("0-2 years of relevant experience.", ""),
            "/jobs/info/senior": ("3-5 years of relevant experience.", ""),
            "/jobs/info/closed": ("1 year of experience.", yesterday),
        }

        def handler(request):
            description, expires = pages[request.url.path]
            body = (
                '<script type="application/ld+json">'
                f'{{"@type":"JobPosting","description":"{description}","validThrough":"{expires}"}}'
                '</script>'
            )
            return httpx.Response(200, text=body)

        candidates = [
            make_job("Financial Analyst", url=f"https://jobright.ai{path}") for path in pages
        ]
        with patch("scrapers.beauty_finance.make_client", return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )):
            job, = await review_beauty_finance(candidates)
        self.assertEqual(job.url, candidates[0].url)

    async def test_consulting_rss_requires_employer_application_page(self):
        parsed = parse_consulting_feed(f"""
        <rss><channel><item>
          <title>Associate Consultant at Example</title><company>Example</company>
          <guid>123</guid><pubDate>Wed, 18 Nov 2026 10:00:00 GMT</pubDate>
          <location>Boston, MA, US</location><description>0-2 years of experience.</description>
          <link>{CONSULTING_RSS.replace('/rss/jobs', '/jobs/123-associate-consultant')}</link>
        </item></channel></rss>
        """)
        self.assertEqual(parsed[0].title, "Associate Consultant")

        def handler(request):
            self.assertEqual(request.url.path, "/jobs/123/apply")
            return httpx.Response(200, text="<h1>Application on job board</h1>")

        with patch("scrapers.beauty_finance.make_client", return_value=httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )):
            self.assertEqual(await review_beauty_finance(parsed), [])
