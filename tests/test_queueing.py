import unittest
from unittest.mock import AsyncMock, patch

import main
from config import ChannelConfig
from scrapers.base import Job


def _job(job_id: str, title: str) -> Job:
    return Job(
        id=job_id,
        title=title,
        company="Example",
        location="Remote",
        url=f"https://example.com/{job_id}",
        platform="greenhouse",
        posted_at="2026-05-09T20:00:00+00:00",
    )


class QueueingBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_capped_jobs_are_queued_and_flushed_on_empty_run(self) -> None:
        channel = ChannelConfig(
            name="swe-jobs",
            webhook_url="https://discord.com/api/webhooks/test",
            keywords=["software engineer"],
            excluded_keywords=[],
            locations=["remote"],
        )

        # First run: 3 matching jobs but cap=1; 2 should be queued
        jobs_run1 = [_job("j1", "Software Engineer"), _job("j2", "Software Engineer"), _job("j3", "Software Engineer")]

        # Second run: no jobs; should flush queued (up to cap=1)
        jobs_run2: list[Job] = []

        queue_data = {"channels": {"swe-jobs": []}, "last_run": ""}
        seen_data = {"jobs": [], "channels": {"swe-jobs": []}, "last_run": "", "total_notified": 0}

        notify_mock = AsyncMock(side_effect=[[jobs_run1[0]], [jobs_run1[1]]])
        summary_mock = AsyncMock()

        with (
            patch("main.load_channels", return_value=[channel]),
            patch("main.load_seen_jobs", return_value=seen_data),
            patch("main.prune_seen_jobs", side_effect=lambda data: data),
            patch("main.ensure_channel_seen_state", autospec=True),
            patch("main.load_queued_jobs", return_value=queue_data),
            patch("main.prune_queued_jobs", side_effect=lambda data: data),
            patch("main.save_seen_jobs"),
            patch("main.save_queued_jobs"),
            patch("main.filter_recent_jobs", side_effect=lambda jobs: jobs),
            patch("main.discord_notifier.notify_jobs_batch", new=notify_mock),
            patch("main.discord_notifier.send_summary", new=summary_mock),
        ):
            with patch("main.config.MAX_NOTIFICATIONS_PER_RUN", 1):
                with patch("main.scrape_all_raw", new=AsyncMock(return_value=(jobs_run1, len(jobs_run1)))):
                    await main.main(init_mode=False)

                # Two jobs should be queued after cap
                self.assertEqual(len(queue_data["channels"]["swe-jobs"]), 2)

                with patch("main.scrape_all_raw", new=AsyncMock(return_value=(jobs_run2, 0))):
                    await main.main(init_mode=False)

        # One queued job flushed (cap=1), leaving 1 queued
        self.assertEqual(len(queue_data["channels"]["swe-jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
