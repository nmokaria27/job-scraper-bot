import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
import sys


import jev
import main
from config import ChannelConfig
from scrapers.base import Job


def _job(job_id: str, title: str) -> Job:
    posted_at = (datetime.now(tz=timezone.utc) - timedelta(minutes=5)).isoformat()
    return Job(
        id=job_id,
        title=title,
        company="Example",
        location="Remote",
        url=f"https://example.com/{job_id}",
        platform="greenhouse",
        posted_at=posted_at,
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
        # Distinct titles: identical company/title/location would be collapsed
        # as duplicates within a run.
        jobs_run1 = [
            _job("j1", "Software Engineer, Payments"),
            _job("j2", "Software Engineer, Search"),
            _job("j3", "Software Engineer, Infra"),
        ]

        # Second run: no jobs; should flush queued (up to cap=1)
        jobs_run2: list[Job] = []

        queue_data = {"channels": {"swe-jobs": []}, "last_run": ""}
        seen_data = {"jobs": [], "channels": {"swe-jobs": []}, "last_run": "", "total_notified": 0}

        notify_mock = AsyncMock(side_effect=[[jobs_run1[0]], [jobs_run1[1]]])
        summary_mock = AsyncMock()

        async def unjudged(jobs, *args, **kwargs):
            return [jev.UNJUDGED] * len(jobs)

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
            patch("main.config.JEV_RANKING", False),
            patch("main.config.JEV_PROMOTE", False),
            patch("main.config.JEV_ENFORCE", False),
            patch("main.jev.judge_for_channel", new=unjudged),
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

    async def test_ranking_leaves_overflow_unseen_for_rerank(self) -> None:
        channel = ChannelConfig(
            name="swe-jobs",
            webhook_url="https://discord.com/api/webhooks/test",
            keywords=["software engineer"],
            excluded_keywords=[],
            locations=["remote"],
            jev_profile="swe",
        )
        jobs = [
            _job("j1", "Software Engineer, Payments"),
            _job("j2", "Software Engineer, Search"),
            _job("j3", "Software Engineer, Infra"),
        ]
        queue_data = {"channels": {"swe-jobs": []}, "last_run": ""}
        seen_data = {"jobs": [], "channels": {"swe-jobs": []}, "last_run": "", "total_notified": 0}
        fits = {"j1": 0.2, "j2": 0.4, "j3": 0.95}

        async def fake_judge(unseen, ch, budget, client=None):
            return [
                jev.Verdict(
                    True, True, fits[job.id], "software_engineering",
                    "new_grad_or_entry", "full_time", 0.9, "", "",
                )
                for job in unseen
            ]

        notified_batches: list[list[Job]] = []

        captured_scores: list[dict] = []

        async def fake_notify(batch, *args, **kwargs):
            notified_batches.append(list(batch))
            captured_scores.append(kwargs.get("scores") or {})
            return list(batch)

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
            patch("main.discord_notifier.notify_jobs_batch", new=fake_notify),
            patch("main.discord_notifier.send_summary", new=AsyncMock()),
            patch("main.jev.judge_for_channel", new=fake_judge),
            patch("main.config.JEV_RANKING", True),
            patch("main.config.JEV_PROMOTE", False),
            patch("main.config.JEV_ENFORCE", False),
            patch("main.config.JEV_ENABLED", True),
            patch("main.config.MAX_NOTIFICATIONS_PER_RUN", 1),
            patch("main.scrape_all_raw", new=AsyncMock(return_value=(jobs, len(jobs)))),
        ):
            await main.main(init_mode=False)

        self.assertEqual([job.id for job in notified_batches[0]], ["j3"])
        self.assertAlmostEqual(captured_scores[0]["j3"][0], 0.95)
        seen_ids = {entry["id"] for entry in seen_data["jobs"]}
        self.assertIn("j3", seen_ids)
        self.assertNotIn("j1", seen_ids)
        self.assertNotIn("j2", seen_ids)
        self.assertEqual(queue_data["channels"]["swe-jobs"], [])

    def test_unjudged_overflow_is_not_left_for_rerank(self) -> None:
        judged = jev.Verdict(
            True, True, 0.9, "software_engineering", "new_grad_or_entry",
            "full_time", 0.8, "", "",
        )
        with patch("main.config.JEV_RANKING", True):
            self.assertFalse(main._leave_overflow_unseen([jev.UNJUDGED]))
            self.assertTrue(main._leave_overflow_unseen([judged]))
        with patch("main.config.JEV_RANKING", False):
            self.assertFalse(main._leave_overflow_unseen([judged]))

    def test_swe_channel_is_judged_before_pm(self) -> None:
        pm = ChannelConfig(name="pm-jobs", webhook_url="x", jev_profile="pm")
        swe = ChannelConfig(name="swe-ai-full-time", webhook_url="x", jev_profile="swe")
        with patch("main.config.JEV_ENABLED", True):
            ordered = main._channels_for_judging([pm, swe])
        self.assertEqual([ch.name for ch in ordered], ["swe-ai-full-time", "pm-jobs"])
        with patch("main.config.JEV_ENABLED", False):
            ordered = main._channels_for_judging([pm, swe])
        self.assertEqual([ch.name for ch in ordered], ["pm-jobs", "swe-ai-full-time"])


if __name__ == "__main__":
    unittest.main()
