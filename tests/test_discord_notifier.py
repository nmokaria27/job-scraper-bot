import unittest
from datetime import datetime, timezone
import sys
from unittest.mock import MagicMock

# Mock httpx because it's not installed and we don't need it for testing _build_job_embed
sys.modules['httpx'] = MagicMock()
# Mock config as well if needed
sys.modules['config'] = MagicMock()

from scrapers.base import Job
from discord_notifier import _build_job_embed

class TestDiscordNotifier(unittest.TestCase):
    def test_build_job_embed_happy_path(self):
        job = Job(
            id="test-1",
            title="Software Engineer",
            company="Test Company",
            location="San Francisco",
            url="https://example.com/job",
            platform="greenhouse",
            posted_at="2024-03-20T10:00:00Z"
        )
        embed = _build_job_embed(job)

        self.assertEqual(embed["title"], "\U0001f680 Software Engineer")
        self.assertEqual(embed["description"], "**Test Company**")
        self.assertEqual(embed["color"], 0x3CB371)
        self.assertEqual(embed["fields"][0]["value"], "\U0001f33f Greenhouse")
        self.assertEqual(embed["fields"][1]["value"], "San Francisco")
        self.assertEqual(embed["fields"][2]["value"], "2024-03-20T10:00:00Z")
        self.assertEqual(embed["fields"][3]["value"], "[Click Here](https://example.com/job)")
        self.assertEqual(embed["footer"]["text"], "Job Scraper Bot")
        self.assertIn("timestamp", embed)

    def test_build_job_embed_unknown_platform(self):
        job = Job(
            id="test-2",
            title="Data Scientist",
            company="Data Co",
            location="Remote",
            url="https://example.com/ds",
            platform="unknown_platform",
            posted_at="Unknown"
        )
        embed = _build_job_embed(job)
        self.assertEqual(embed["fields"][0]["value"], "Unknown_platform")
        self.assertEqual(embed["color"], 5814783) # Default color

    def test_build_job_embed_missing_location(self):
        job = Job(
            id="test-3",
            title="Designer",
            company="Design Studio",
            location="",
            url="https://example.com/design",
            platform="lever",
            posted_at="Unknown"
        )
        embed = _build_job_embed(job)
        self.assertEqual(embed["fields"][1]["value"], "Remote / Not Specified")

    def test_build_job_embed_missing_url(self):
        job = Job(
            id="test-4",
            title="Manager",
            company="Management Inc",
            location="New York",
            url="",
            platform="ashby",
            posted_at="Unknown"
        )
        embed = _build_job_embed(job)
        self.assertEqual(embed["fields"][3]["value"], "No link available")

    def test_build_job_embed_long_title_truncation(self):
        long_title = "A" * 300
        job = Job(
            id="test-5",
            title=long_title,
            company="Big Co",
            location="Earth",
            url="https://example.com/big",
            platform="simplify",
            posted_at="Unknown"
        )
        embed = _build_job_embed(job)
        # Discord title limit is 256.
        self.assertTrue(len(embed["title"]) <= 256, f"Title length {len(embed['title'])} is too long")
        self.assertTrue(embed["title"].endswith("..."))

if __name__ == "__main__":
    unittest.main()
