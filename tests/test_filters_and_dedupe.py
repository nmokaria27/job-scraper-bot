import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import config
import main
from config import ChannelConfig
from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper


class FullTimeChannelFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = GreenhouseScraper()

    def _matches(self, title: str) -> bool:
        return self.scraper.matches_keywords(
            title,
            config.DEFAULT_SWE_FULL_TIME_KEYWORDS,
            config.DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS,
        )

    def test_accepts_entry_level_software_and_ai_titles(self) -> None:
        for title in (
            "Software Engineer",
            "Software Engineer, New Grad",
            "New Grad Software Engineer (2027)",
            "Entry Level Software Engineer",
            "AI Engineer 1 - Enterprise Technology Services",
            "Member of Technical Staff",
            "Applied Scientist",
            "Machine Learning Engineer",
            "Research Engineer, Pre-training",
            "Data Scientist, Decisions",
            "Software Development Engineer, Early Careers",
            "Forward Deployed Engineer",
        ):
            self.assertTrue(self._matches(title), f"should match {title!r}")

    def test_rejects_interns_seniors_and_level_codes(self) -> None:
        for title in (
            "Software Engineer Intern",
            "Software Engineering Co-op - Fall 2027",
            "Senior Software Engineer",
            "Sr. Security Software Engineer",
            "Staff Machine Learning Engineer",
            "Software Engineer II",
            "Software Engineer 2",
            "Machine Learning Engineer L3",
            "Engineering Manager, Platform",
            "Founding Engineer",
            "Solutions Architect",
        ):
            self.assertFalse(self._matches(title), f"should NOT match {title!r}")

    def test_rejects_non_software_engineering_disciplines(self) -> None:
        for title in (
            "Enterprise Sales Engineer - Rockies",
            "Solutions Engineer",
            "IT Support Engineer",
            "Support Engineer, AI Infrastructure",
            "Hardware Engineer",
            "Mechanical Engineer",
            "Electrical Design Engineer Graduate",
            "FPGA Design Engineer",
            "Analog Design Engineer - Career Accelerator Program",
            "Developer Advocate - Service Management",
            "People Systems Developer",
            "Network Engineer",
            "Cyber Security Engineer",
            "Security Engineer, Incident Response",
            "Physical Design Engineer, Synthesis",
            "Fellow, Software Engineering",
            "Data Scientist Assistant",
        ):
            self.assertFalse(self._matches(title), f"should NOT match {title!r}")


class PmChannelFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = GreenhouseScraper()

    def _matches(self, title: str) -> bool:
        return self.scraper.matches_keywords(
            title,
            config.DEFAULT_PM_KEYWORDS,
            config.DEFAULT_PM_EXCLUDED_KEYWORDS,
        )

    def test_accepts_entry_level_pm_titles(self) -> None:
        for title in (
            "Product Manager",
            "Associate Product Manager",
            "APM, New Grad",
            "Technical Program Manager",
            "Product Manager Intern 2027",
            "Product Management Intern",
            "Product Owner",
            "Product Analyst",
        ):
            self.assertTrue(self._matches(title), f"should match {title!r}")

    def test_rejects_senior_group_and_retail_pm_titles(self) -> None:
        for title in (
            "Group Product Manager, Cloud Security",
            "Sr. Product Manager, Core Saving Experience",
            "Senior Product Manager",
            "Director of Product Management",
            "Seasonal Product Operations Educator",
            "Product Marketing Manager",
            "Product Manager II",
        ):
            self.assertFalse(self._matches(title), f"should NOT match {title!r}")


class LocationFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = GreenhouseScraper()

    def _allowed(self, location: str) -> bool:
        return self.scraper.matches_location(
            location, config.DEFAULT_LOCATIONS, config.DEFAULT_EXCLUDED_LOCATIONS
        )

    def test_default_locations_match_full_state_names_and_hubs(self) -> None:
        for location in (
            "Bellevue, Washington, United States",
            "Austin, Texas, United States",
            "Mountain View, California, United States",
            "SF / NYC",
            "Remote - USA",
            "Remote (U.S.)",
        ):
            self.assertTrue(self._allowed(location), f"should allow {location!r}")

    def test_foreign_locations_are_rejected(self) -> None:
        for location in (
            "London, United Kingdom",
            "Remote - Canada / Montreal / Vancouver / Toronto",
            "Remote in UK",
            "Toronto, ON, CA",
            "United Kingdom, Remote",
            "Bangalore, India",
            "Perth, WA, Australia",
            "Remote, Ontario / Remote, British Columbia",
        ):
            self.assertFalse(self._allowed(location), f"should reject {location!r}")

    def test_multi_location_with_a_us_office_is_kept(self) -> None:
        for location in (
            "REMOTE (US, Canada, Europe)",
            "London, UK / New York, NY",
            "Tel Aviv, Israel; San Francisco, CA",
        ):
            self.assertTrue(self._allowed(location), f"should allow {location!r}")

    def test_excluded_locations_match_whole_words_only(self) -> None:
        # "india" must not hit "Indianapolis, Indiana"
        self.assertTrue(self._allowed("Indianapolis, Indiana, United States"))

    def test_work_model_only_locations_get_benefit_of_the_doubt(self) -> None:
        for location in ("Hybrid", "In-Office", "N/A", "Remote", "", "3 Locations"):
            self.assertTrue(self._allowed(location), f"should allow {location!r}")

    def test_symbol_keywords_match_cleanly(self) -> None:
        self.assertTrue(self.scraper.matches_keywords("C++ Engineer", ["c++"], []))
        self.assertTrue(self.scraper.matches_keywords("C# Developer", ["c#"], []))
        self.assertFalse(self.scraper.matches_keywords("MVP Engineer", ["vp"], []))


class ChannelLoadingTests(unittest.TestCase):
    def test_pm_and_full_time_webhooks_load_built_in_channels(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SWE_WEBHOOK_URL": "https://discord.com/api/webhooks/retired",
                "PM_WEBHOOK_URL": "https://discord.com/api/webhooks/pm",
                "FULL_TIME_WEBHOOK_URL": "https://discord.com/api/webhooks/fulltime-env",
                "CHANNELS_JSON": "",
            },
            clear=False,
        ):
            channels = config.load_channels(require_webhooks=True)

        # The retired SWE_WEBHOOK_URL (internship channel) must be ignored.
        self.assertEqual([channel.name for channel in channels], ["pm-jobs", "swe-ai-full-time"])
        self.assertEqual(channels[1].webhook_url, "https://discord.com/api/webhooks/fulltime-env")
        self.assertEqual(channels[0].excluded_locations, config.DEFAULT_EXCLUDED_LOCATIONS)

    def test_channels_json_can_add_and_override_env_channels(self) -> None:
        raw_channels = json.dumps(
            [
                {
                    "name": "pm-jobs",
                    "webhook_url": "https://discord.com/api/webhooks/override",
                    "keywords": ["product manager"],
                    "excluded_keywords": ["senior"],
                    "locations": ["remote"],
                    "excluded_locations": ["canada"],
                },
                {
                    "name": "quant-jobs",
                    "webhook_url": "https://discord.com/api/webhooks/quant",
                    "keywords": ["quantitative researcher"],
                },
            ]
        )
        with patch.dict(
            os.environ,
            {
                "PM_WEBHOOK_URL": "https://discord.com/api/webhooks/default-pm",
                "FULL_TIME_WEBHOOK_URL": "",
                "CHANNELS_JSON": raw_channels,
            },
            clear=False,
        ):
            channels = config.load_channels(require_webhooks=True)

        self.assertEqual([channel.name for channel in channels], ["pm-jobs", "quant-jobs"])
        self.assertEqual(channels[0].webhook_url, "https://discord.com/api/webhooks/override")
        self.assertEqual(channels[0].keywords, ["product manager"])
        self.assertEqual(channels[0].excluded_locations, ["canada"])
        self.assertEqual(channels[1].excluded_locations, [])


class DedupeBehaviorTests(unittest.TestCase):
    def _job(
        self,
        job_id: str,
        platform: str,
        url: str,
        title: str = "Software Engineer",
        location: str = "Remote",
        company: str = "Example",
    ) -> Job:
        return Job(
            id=job_id,
            title=title,
            company=company,
            location=location,
            url=url,
            platform=platform,
            posted_at="2026-05-20T20:00:00+00:00",
        )

    def test_canonical_dedupe_prefers_direct_ats_posting(self) -> None:
        simplify_job = self._job(
            "simplify-1", "simplify", "https://jobs.example.com/roles/123?utm_source=simplify"
        )
        greenhouse_job = self._job(
            "greenhouse-example-123", "greenhouse", "https://jobs.example.com/roles/123"
        )

        deduped = main.dedupe_jobs_for_channel("swe-ai-full-time", [simplify_job, greenhouse_job])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].platform, "greenhouse")

    def test_same_company_title_location_collapses_across_urls(self) -> None:
        # Aggregators re-list the same posting under fresh IDs/URLs.
        first = self._job("jobright-a", "jobright", "https://jobright.ai/jobs/info/aaa", location="NYC")
        second = self._job("jobright-b", "jobright", "https://jobright.ai/jobs/info/bbb", location="NYC")
        other_city = self._job("jobright-c", "jobright", "https://jobright.ai/jobs/info/ccc", location="Austin, TX")

        deduped = main.dedupe_jobs_for_channel("swe-ai-full-time", [first, second, other_city])

        self.assertEqual(len(deduped), 2)

    def test_seen_keys_cover_cross_source_duplicates(self) -> None:
        greenhouse_job = self._job(
            "greenhouse-example-123", "greenhouse", "https://jobs.example.com/roles/123"
        )
        simplify_job = self._job(
            "simplify-1", "simplify", "https://jobs.example.com/roles/123?utm_source=simplify"
        )
        # Same title/company/location but a different URL is a different req:
        # it must NOT be suppressed by persisted seen-state.
        new_req = self._job("greenhouse-example-456", "greenhouse", "https://jobs.example.com/roles/456")

        seen_data = {"jobs": [], "channels": {"swe-ai-full-time": []}}
        main.mark_job_seen(seen_data, "swe-ai-full-time", greenhouse_job)

        seen_ids = main.get_channel_seen_ids(seen_data, "swe-ai-full-time")
        self.assertTrue(main.job_was_seen(seen_ids, simplify_job))
        self.assertFalse(main.job_was_seen(seen_ids, new_req))

    def test_newest_first_puts_undated_jobs_last(self) -> None:
        old = self._job("a", "greenhouse", "https://x/a")
        old.posted_at = "2026-09-01T00:00:00+00:00"
        new = self._job("b", "greenhouse", "https://x/b")
        new.posted_at = "2026-09-04T00:00:00+00:00"
        undated = self._job("c", "ashby", "https://x/c")
        undated.posted_at = "Unknown"
        ordered = main._newest_first([undated, old, new])
        self.assertEqual([job.id for job in ordered], ["b", "a", "c"])


class SeenStateMaintenanceTests(unittest.TestCase):
    def _channels(self) -> list[ChannelConfig]:
        return [
            ChannelConfig(name="pm-jobs", webhook_url="x", keywords=["product manager"]),
            ChannelConfig(name="swe-ai-full-time", webhook_url="x", keywords=["engineer"]),
        ]

    def test_retired_channel_state_is_dropped_on_normal_runs(self) -> None:
        data = {
            "jobs": [{"id": "a", "seen_at": "2026-09-01T00:00:00+00:00"}],
            "channels": {"swe-jobs": ["a"], "pm-jobs": ["a"]},
        }
        main.ensure_channel_seen_state(data, self._channels(), prune_stale=True)
        self.assertEqual(set(data["channels"]), {"pm-jobs", "swe-ai-full-time"})

    def test_retired_channel_state_is_kept_without_prune(self) -> None:
        data = {
            "jobs": [{"id": "a", "seen_at": "2026-09-01T00:00:00+00:00"}],
            "channels": {"swe-jobs": ["a"], "pm-jobs": ["a"]},
        }
        main.ensure_channel_seen_state(data, self._channels())
        self.assertIn("swe-jobs", data["channels"])

    def test_retired_channel_queue_is_dropped(self) -> None:
        queue = {"channels": {"swe-jobs": [{"id": "x"}], "pm-jobs": []}}
        main.drop_stale_queue_channels(queue, self._channels())
        self.assertEqual(set(queue["channels"]), {"pm-jobs"})


class RecentPostingFilterTests(unittest.TestCase):
    def _job(self, posted_at: str) -> Job:
        return Job(
            id=f"j-{posted_at}",
            title="Software Engineer",
            company="Example",
            location="Remote",
            url="https://example.com/1",
            platform="jobright",
            posted_at=posted_at,
        )

    def test_date_only_timestamps_get_a_day_of_grace(self) -> None:
        now = datetime.now(tz=timezone.utc)
        thirty_hours_ago = (now - timedelta(hours=30))
        sixty_hours_ago = (now - timedelta(hours=60))
        jobs = [
            self._job(thirty_hours_ago.isoformat()),          # precise: outside 24h -> dropped
            self._job(thirty_hours_ago.date().isoformat()),   # date-only: within grace -> kept
            self._job(sixty_hours_ago.date().isoformat()),    # date-only: too old -> dropped
            self._job("Unknown"),                             # unparseable -> kept
        ]
        with patch("main.config.RECENT_POSTING_MAX_AGE_HOURS", 24):
            recent = main.filter_recent_jobs(jobs)
        self.assertEqual(
            [job.posted_at for job in recent],
            [thirty_hours_ago.date().isoformat(), "Unknown"],
        )


if __name__ == "__main__":
    unittest.main()
