import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import sys

# Mock dependencies
sys.modules["httpx"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

import main
from config import ChannelConfig
from scrapers.base import Job


def _job(job_id: str, title: str, location: str = "Remote") -> Job:
    return Job(
        id=job_id,
        title=title,
        company="Example Co",
        location=location,
        url=f"https://example.com/{job_id}",
        platform="greenhouse",
        posted_at="2026-05-20T20:00:00+00:00",
    )


class NotificationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_filtered_jobs_are_sent_to_discord(self) -> None:
        channel = ChannelConfig(
            name="swe-jobs",
            webhook_url="https://discord.com/api/webhooks/test",
            keywords=["software engineer", "machine learning"],
            excluded_keywords=["senior", "intern"],
            locations=["remote", "san francisco"],
        )

        matching = _job("match-1", "Software Engineer")
        filtered_out_keyword = _job("skip-1", "Product Manager")
        filtered_out_location = _job("skip-2", "Machine Learning Engineer", "London, UK")

        mock_notify = AsyncMock(return_value=[matching])
        mock_summary = AsyncMock()

        with (
            patch("main.load_channels", return_value=[channel]),
            patch(
                "main.load_seen_jobs",
                return_value={"jobs": [], "channels": {"swe-jobs": []}, "last_run": "", "total_notified": 0},
            ),
            patch("main.prune_seen_jobs", side_effect=lambda data: data),
            patch("main.ensure_channel_seen_state", autospec=True),
            patch("main.scrape_all_raw", new=AsyncMock(return_value=([
                matching,
                filtered_out_keyword,
                filtered_out_location,
            ], 3))),
            patch("main.filter_recent_jobs", side_effect=lambda jobs: jobs),
            patch("main.discord_notifier.notify_jobs_batch", new=mock_notify),
            patch("main.discord_notifier.send_summary", new=mock_summary),
            patch("main.save_seen_jobs"),
        ):
            await main.main(init_mode=False)

        mock_notify.assert_awaited_once()
        sent_jobs = mock_notify.await_args.args[0]
        self.assertEqual([job.id for job in sent_jobs], ["match-1"])

        summary_args = mock_summary.await_args.kwargs
        self.assertEqual(summary_args["new_count"], 1)


if __name__ == "__main__":
    unittest.main()
