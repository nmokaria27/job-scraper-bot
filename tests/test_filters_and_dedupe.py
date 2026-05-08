import json
import os
import unittest
from unittest.mock import patch

import config
import main
from scrapers.base import Job
from scrapers.greenhouse import GreenhouseScraper


class FilterBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = GreenhouseScraper()

    def test_swe_defaults_match_broader_intern_titles(self) -> None:
        self.assertTrue(
            self.scraper.matches_keywords(
                "Engineering Intern",
                config.DEFAULT_SWE_KEYWORDS,
                config.DEFAULT_SWE_EXCLUDED_KEYWORDS,
            )
        )
        self.assertTrue(
            self.scraper.matches_keywords(
                "Software Development Intern",
                config.DEFAULT_SWE_KEYWORDS,
                config.DEFAULT_SWE_EXCLUDED_KEYWORDS,
            )
        )

    def test_default_locations_match_full_state_names_and_hubs(self) -> None:
        self.assertTrue(
            self.scraper.matches_location(
                "Bellevue, Washington, United States",
                config.DEFAULT_LOCATIONS,
            )
        )
        self.assertTrue(
            self.scraper.matches_location(
                "Austin, Texas, United States",
                config.DEFAULT_LOCATIONS,
            )
        )
        self.assertTrue(
            self.scraper.matches_location(
                "Mountain View, California, United States",
                config.DEFAULT_LOCATIONS,
            )
        )
        self.assertFalse(
            self.scraper.matches_location(
                "London, United Kingdom",
                config.DEFAULT_LOCATIONS,
            )
        )

    def test_level_codes_are_excluded_when_role_keywords_match(self) -> None:
        self.assertFalse(
            self.scraper.matches_keywords(
                "Software Engineer 2",
                ["software engineer"],
                config.DEFAULT_SWE_EXCLUDED_KEYWORDS,
            )
        )
        self.assertFalse(
            self.scraper.matches_keywords(
                "Machine Learning Engineer L3",
                ["machine learning", "engineer"],
                config.DEFAULT_SWE_EXCLUDED_KEYWORDS,
            )
        )


class ChannelLoadingTests(unittest.TestCase):
    def test_channels_json_can_add_and_override_env_channels(self) -> None:
        raw_channels = json.dumps(
            [
                {
                    "name": "swe-jobs",
                    "webhook_url": "https://discord.com/api/webhooks/override",
                    "keywords": ["software engineer"],
                    "excluded_keywords": ["senior"],
                    "locations": ["remote"],
                },
                {
                    "name": "swe-full-time-ai-ml-jobs",
                    "webhook_url": "https://discord.com/api/webhooks/fulltime",
                    "keywords": ["software engineer", "ml engineer"],
                    "excluded_keywords": ["intern"],
                    "locations": ["remote"],
                },
            ]
        )
        with patch.dict(
            os.environ,
            {
                "SWE_WEBHOOK_URL": "https://discord.com/api/webhooks/default-swe",
                "PM_WEBHOOK_URL": "",
                "CHANNELS_JSON": raw_channels,
            },
            clear=False,
        ):
            channels = config.load_channels(require_webhooks=True)

        self.assertEqual(
            [channel.name for channel in channels],
            ["swe-jobs", "swe-full-time-ai-ml-jobs"],
        )
        self.assertEqual(channels[0].webhook_url, "https://discord.com/api/webhooks/override")
        self.assertEqual(channels[0].keywords, ["software engineer"])


class DedupeBehaviorTests(unittest.TestCase):
    def _job(self, job_id: str, platform: str, url: str) -> Job:
        return Job(
            id=job_id,
            title="Software Engineer Intern",
            company="Example",
            location="Remote",
            url=url,
            platform=platform,
            posted_at="2026-05-08T15:00:00+00:00",
        )

    def test_canonical_dedupe_prefers_direct_ats_posting(self) -> None:
        simplify_job = self._job(
            "simplify-1",
            "simplify",
            "https://jobs.example.com/roles/123?utm_source=simplify",
        )
        greenhouse_job = self._job(
            "greenhouse-example-123",
            "greenhouse",
            "https://jobs.example.com/roles/123",
        )

        deduped = main.dedupe_jobs_for_channel(
            "swe-jobs",
            [simplify_job, greenhouse_job],
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].platform, "greenhouse")

    def test_seen_keys_cover_cross_source_duplicates(self) -> None:
        greenhouse_job = self._job(
            "greenhouse-example-123",
            "greenhouse",
            "https://jobs.example.com/roles/123",
        )
        simplify_job = self._job(
            "simplify-1",
            "simplify",
            "https://jobs.example.com/roles/123?utm_source=simplify",
        )

        seen_data = {"jobs": [], "channels": {"swe-jobs": []}}
        main.mark_job_seen(seen_data, "swe-jobs", greenhouse_job)

        seen_ids = main.get_channel_seen_ids(seen_data, "swe-jobs")
        self.assertTrue(main.job_was_seen(seen_ids, simplify_job))


if __name__ == "__main__":
    unittest.main()
