import unittest
from datetime import datetime, timezone
import sys
from unittest.mock import MagicMock


from unittest.mock import AsyncMock, patch

from scrapers.base import Job
from discord_notifier import WebhookPostResult, _build_job_embed, notify_jobs_batch

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

    def test_build_job_embed_includes_jev_scores(self):
        job = Job(
            id="test-6",
            title="Software Engineer",
            company="Test Company",
            location="NYC",
            url="https://example.com/job",
            platform="greenhouse",
            posted_at="Unknown",
        )
        embed = _build_job_embed(job, fit=0.82, confidence=0.9)
        jev_field = next(field for field in embed["fields"] if field["name"] == "Jev")
        self.assertEqual(jev_field["value"], "82% fit · 90% confidence")

    def test_build_job_embed_omits_jev_when_unjudged(self):
        job = Job(
            id="test-7",
            title="Software Engineer",
            company="Test Company",
            location="NYC",
            url="https://example.com/job",
            platform="greenhouse",
            posted_at="Unknown",
        )
        embed = _build_job_embed(job)
        self.assertFalse(any(field["name"] == "Jev" for field in embed["fields"]))


class NotifyBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_at_most_ten_embeds(self) -> None:
        jobs = [
            Job(
                id=f"b{i}",
                title=f"Software Engineer {i}",
                company="Co",
                location="Remote",
                url=f"https://example.com/{i}",
                platform="greenhouse",
                posted_at="Unknown",
            )
            for i in range(12)
        ]
        payloads: list[dict] = []

        async def fake_post(client, payload, webhook_url=None):
            payloads.append(payload)
            return WebhookPostResult(success=True)

        with (
            patch("discord_notifier._post_webhook", new=fake_post),
            patch("discord_notifier.asyncio.sleep", new=AsyncMock()),
        ):
            notified = await notify_jobs_batch(
                jobs,
                "https://example.invalid/webhook",
                scores={"b0": (0.8, 0.7)},
            )

        self.assertEqual(len(notified), 12)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(len(payloads[0]["embeds"]), 10)
        self.assertEqual(len(payloads[1]["embeds"]), 2)
        jev_fields = [f for f in payloads[0]["embeds"][0]["fields"] if f["name"] == "Jev"]
        self.assertEqual(jev_fields[0]["value"], "80% fit · 70% confidence")


if __name__ == "__main__":
    unittest.main()
