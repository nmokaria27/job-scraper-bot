"""Pure parsing tests for each scraper — no network."""

import unittest
from datetime import datetime, timedelta, timezone

from scrapers.ashby import AshbyScraper
from scrapers.bigtech import (
    parse_amazon_date,
    parse_amazon_jobs,
    parse_workday_jobs,
    parse_workday_posted,
)
from scrapers.greenhouse import GreenhouseScraper
from scrapers.json_sources import JSON_SOURCE_SPECS, parse_json_source
from scrapers.lever import LeverScraper
from scrapers.simplify import SimplifyScraper


class GreenhouseParsingTests(unittest.TestCase):
    def test_first_published_is_preferred_over_updated_at(self) -> None:
        raw = {
            "id": 1,
            "title": "Software Engineer",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/1",
            "location": {"name": "Seattle, WA"},
            "first_published": "2026-09-03T13:30:34-04:00",
            "updated_at": "2026-09-04T14:12:20-04:00",
        }
        job = GreenhouseScraper.parse_job(raw, "stripe")
        self.assertEqual(job.posted_at, "2026-09-03T13:30:34-04:00")
        self.assertEqual(job.company, "Stripe")
        self.assertEqual(job.location, "Seattle, WA")

    def test_offices_replace_work_model_only_location(self) -> None:
        raw = {
            "id": 2,
            "title": "Systems Engineer",
            "location": {"name": "Hybrid"},
            "offices": [{"name": "Austin, TX"}, {"name": "London, United Kingdom"}],
            "updated_at": "2026-09-04T14:12:20-04:00",
        }
        job = GreenhouseScraper.parse_job(raw, "cloudflare")
        self.assertEqual(job.location, "Austin, TX / London, United Kingdom (Hybrid)")
        self.assertEqual(job.posted_at, "2026-09-04T14:12:20-04:00")

    def test_display_name_overrides(self) -> None:
        raw = {"id": 3, "title": "Research Engineer", "location": {"name": "London"}}
        self.assertEqual(GreenhouseScraper.parse_job(raw, "deepmind").company, "Google DeepMind")


class LeverParsingTests(unittest.TestCase):
    def test_all_locations_and_created_at(self) -> None:
        raw = {
            "id": "abc",
            "text": "Software Engineer, New Grad",
            "hostedUrl": "https://jobs.lever.co/palantir/abc",
            "createdAt": 1788537601000,
            "categories": {"location": "Palo Alto, CA", "allLocations": ["Palo Alto, CA", "New York, NY"]},
        }
        job = LeverScraper.parse_job(raw, "palantir")
        self.assertEqual(job.location, "Palo Alto, CA / New York, NY")
        self.assertEqual(job.company, "Palantir")
        self.assertTrue(job.posted_at.startswith("2026-09-0"))


class AshbyParsingTests(unittest.TestCase):
    def test_relative_job_url_and_secondary_locations(self) -> None:
        raw = {
            "id": "x1",
            "title": "Member of Technical Staff",
            "jobUrl": "/openai/x1",
            "location": "San Francisco",
            "secondaryLocations": [{"location": "New York"}],
            "publishedAt": "2026-09-04T00:00:00.000+00:00",
        }
        job = AshbyScraper.parse_job(raw, "openai")
        self.assertEqual(job.url, "https://jobs.ashbyhq.com/openai/x1")
        self.assertEqual(job.location, "San Francisco / New York")
        self.assertEqual(job.company, "OpenAI")
        self.assertEqual(job.posted_at, "2026-09-04T00:00:00.000+00:00")

    def test_missing_published_at_is_unknown(self) -> None:
        raw = {"id": "x2", "title": "Software Engineer", "jobUrl": "https://jobs.ashbyhq.com/snowflake/x2"}
        self.assertEqual(AshbyScraper.parse_job(raw, "snowflake").posted_at, "Unknown")


class SimplifyParsingTests(unittest.TestCase):
    def test_inactive_and_hidden_entries_are_skipped(self) -> None:
        entries = [
            {"id": "a", "title": "SWE", "company_name": "A", "active": True, "is_visible": True,
             "locations": ["NYC"], "url": "https://a", "date_posted": 1788537601},
            {"id": "b", "title": "SWE", "company_name": "B", "active": False, "is_visible": True},
            {"id": "c", "title": "SWE", "company_name": "C", "active": True, "is_visible": False},
        ]
        jobs = SimplifyScraper.parse_entries(entries)
        self.assertEqual([job.id for job in jobs], ["simplify-a"])
        self.assertEqual(jobs[0].location, "NYC")
        self.assertEqual(SimplifyScraper.parse_entries({"not": "a list"}), [])


class JsonSourceParsingTests(unittest.TestCase):
    def test_applyguy_spec(self) -> None:
        payload = {
            "updatedAt": "2026-09-04T18:00:00Z",
            "jobs": [
                {
                    "id": "46addecf",
                    "company": "Barrywehmiller",
                    "title": "Entry Level Software Engineer",
                    "location": "Dallas, TX",
                    "posted": "2026-09-04",
                    "url": "https://applyguy.ai/jobs?x=1",
                    "listingUrl": "https://example.wd1.myworkdayjobs.com/job/1",
                }
            ],
        }
        jobs = parse_json_source(payload, JSON_SOURCE_SPECS["applyguy"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, "applyguy-46addecf")
        self.assertEqual(jobs[0].url, "https://example.wd1.myworkdayjobs.com/job/1")
        self.assertEqual(jobs[0].posted_at, "2026-09-04")
        self.assertEqual(jobs[0].platform, "applyguy")

    def test_gradtracker_spec_joins_location_and_country(self) -> None:
        payload = {
            "jobs": [
                {
                    "company": "Adobe",
                    "title": "Applied Scientist",
                    "location": "San Jose",
                    "country": "United States of America",
                    "application_url": "https://adobe.wd5.myworkdayjobs.com/x",
                    "date_posted": "2026-09-04",
                    "first_seen": "2026-09-04T17:44:48Z",
                }
            ]
        }
        jobs = parse_json_source(payload, JSON_SOURCE_SPECS["gradtracker"])
        self.assertEqual(jobs[0].location, "San Jose, United States of America")
        self.assertEqual(jobs[0].posted_at, "2026-09-04")
        self.assertTrue(jobs[0].id.startswith("gradtracker-"))

    def test_first_seen_fallback_is_normalised(self) -> None:
        payload = {"jobs": [{"company": "A", "title": "B", "first_seen": "2026-09-04T17:44:48Z"}]}
        jobs = parse_json_source(payload, JSON_SOURCE_SPECS["gradtracker"])
        self.assertEqual(jobs[0].posted_at, "2026-09-04T17:44:48+00:00")


class BigTechParsingTests(unittest.TestCase):
    def test_amazon(self) -> None:
        self.assertEqual(parse_amazon_date("September  4, 2026"), "2026-09-04")
        self.assertEqual(parse_amazon_date("garbage"), "Unknown")
        payload = {
            "jobs": [
                {
                    "title": "Software Development Engineer, Early Careers ",
                    "normalized_location": "Cambridge, Massachusetts, USA",
                    "posted_date": "September  4, 2026",
                    "job_path": "/en/jobs/10530257/sde-early-careers",
                    "id_icims": "10530257",
                }
            ]
        }
        jobs = parse_amazon_jobs(payload)
        self.assertEqual(jobs[0].id, "amazon-10530257")
        self.assertEqual(jobs[0].title, "Software Development Engineer, Early Careers")
        self.assertEqual(jobs[0].url, "https://www.amazon.jobs/en/jobs/10530257/sde-early-careers")
        self.assertEqual(parse_amazon_jobs(None), [])

    def test_workday(self) -> None:
        today = datetime.now(tz=timezone.utc).date()
        self.assertEqual(parse_workday_posted("Posted Today"), today.isoformat())
        self.assertEqual(parse_workday_posted("Posted Yesterday"), (today - timedelta(days=1)).isoformat())
        self.assertEqual(parse_workday_posted("Posted 3 Days Ago"), (today - timedelta(days=3)).isoformat())
        self.assertEqual(parse_workday_posted("Posted 30+ Days Ago"), (today - timedelta(days=31)).isoformat())
        self.assertEqual(parse_workday_posted(""), "Unknown")

        tenant = {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite", "label": "NVIDIA"}
        payload = {
            "jobPostings": [
                {
                    "title": "Software Engineer - New College Grad",
                    "externalPath": "/job/US-CA-Santa-Clara/SWE_JR2016865",
                    "locationsText": "US, CA, Santa Clara",
                    "postedOn": "Posted Today",
                    "bulletFields": ["JR2016865"],
                }
            ]
        }
        jobs = parse_workday_jobs(payload, tenant)
        self.assertEqual(jobs[0].id, "workday-nvidia-JR2016865")
        self.assertEqual(jobs[0].company, "NVIDIA")
        self.assertEqual(
            jobs[0].url,
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/SWE_JR2016865",
        )


if __name__ == "__main__":
    unittest.main()
