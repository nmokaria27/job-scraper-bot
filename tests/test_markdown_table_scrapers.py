import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

# Mock httpx so this file doesn't require the real package to run — consistent
# with the rest of the suite. config is left un-mocked (real import): none of
# the methods under test touch it, and mocking it here would leak into other
# test files collected in the same pytest process (see test_discord_notifier.py).
sys.modules.setdefault("httpx", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())

from scrapers.markdown_table import SpeedyApplyScraper, JobRightScraper


SPEEDYAPPLY_TABLE = """
Some intro text before the table.

| Company | Position | Location | Salary | Posting | Age |
| --- | --- | --- | --- | --- | --- |
| <a href="https://acme.com"><strong>Acme Corp</strong></a> | Software Engineer Intern | Remote | $50/hr | <a href="https://apply.com/123"><img src="apply.svg"></a> | 3d |
| <a href="https://widgets.com"><strong>Widgets Inc</strong></a> | Backend Engineer Intern | New York, NY | $45/hr | <a href="https://apply.com/456"><img src="apply.svg"></a> | 12h |
"""

JOBRIGHT_TABLE = """
Some intro text before the table.

| Company | Position | Location | Work Model | Date |
| --- | --- | --- | --- | --- |
| **[Databricks](https://databricks.com)** | **[Product Manager Intern](https://databricks.com/careers/123)** | San Francisco, CA | Hybrid | Jun 15 |
| ↳ | **[APM, New Grad](https://databricks.com/careers/456)** | Remote | Remote | Jun 16 |
"""


class SpeedyApplyParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = SpeedyApplyScraper()

    def test_first_data_row_is_not_dropped(self) -> None:
        # Regression test: skip_next used to be initialized to 2 ("header +
        # separator"), but the header row is already consumed by the
        # `not in_table` branch's own `continue`, so skip_next=2 ended up
        # skipping the separator row AND the first real data row. Both rows
        # in the fixture must come through.
        jobs = self.scraper._parse_tables(SPEEDYAPPLY_TABLE)
        self.assertEqual(len(jobs), 2)

    def test_row_fields_are_extracted_correctly(self) -> None:
        jobs = self.scraper._parse_tables(SPEEDYAPPLY_TABLE)
        first = jobs[0]
        self.assertTrue(first.id.startswith("speedyapply-"))
        self.assertEqual(first.title, "Software Engineer Intern")
        self.assertEqual(first.company, "Acme Corp")
        self.assertEqual(first.location, "Remote")
        self.assertEqual(first.url, "https://apply.com/123")
        self.assertEqual(first.platform, "speedyapply")

        second = jobs[1]
        self.assertEqual(second.company, "Widgets Inc")
        self.assertEqual(second.url, "https://apply.com/456")

    def test_age_conversion_hours_days_weeks(self) -> None:
        now = datetime.now(tz=timezone.utc)

        three_days = datetime.fromisoformat(SpeedyApplyScraper._parse_age("3d"))
        self.assertAlmostEqual(
            (now - three_days).total_seconds(), timedelta(days=3).total_seconds(), delta=5
        )

        twelve_hours = datetime.fromisoformat(SpeedyApplyScraper._parse_age("12h"))
        self.assertAlmostEqual(
            (now - twelve_hours).total_seconds(), timedelta(hours=12).total_seconds(), delta=5
        )

        two_weeks = datetime.fromisoformat(SpeedyApplyScraper._parse_age("2w"))
        self.assertAlmostEqual(
            (now - two_weeks).total_seconds(), timedelta(weeks=2).total_seconds(), delta=5
        )

    def test_unparseable_age_returns_unknown(self) -> None:
        self.assertEqual(SpeedyApplyScraper._parse_age("recently"), "Unknown")

    def test_row_without_company_link_is_skipped(self) -> None:
        table = (
            "| Company | Position | Location | Salary | Posting | Age |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Plain text, no link | Some Role | Remote | - | - | 1d |\n"
        )
        self.assertEqual(self.scraper._parse_tables(table), [])


class JobRightParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = JobRightScraper()

    def test_first_data_row_is_not_dropped(self) -> None:
        jobs = self.scraper._parse_tables(JOBRIGHT_TABLE)
        self.assertEqual(len(jobs), 2)

    def test_row_fields_are_extracted_correctly(self) -> None:
        jobs = self.scraper._parse_tables(JOBRIGHT_TABLE)
        first = jobs[0]
        self.assertTrue(first.id.startswith("jobright-"))
        self.assertEqual(first.company, "Databricks")
        self.assertEqual(first.title, "Product Manager Intern")
        self.assertEqual(first.url, "https://databricks.com/careers/123")
        self.assertEqual(first.location, "San Francisco, CA")
        self.assertEqual(first.platform, "jobright")

    def test_continuation_row_inherits_company_from_previous_row(self) -> None:
        jobs = self.scraper._parse_tables(JOBRIGHT_TABLE)
        second = jobs[1]
        self.assertEqual(second.company, "Databricks")
        self.assertEqual(second.title, "APM, New Grad")
        self.assertEqual(second.location, "Remote")

    def test_continuation_row_without_prior_company_is_skipped(self) -> None:
        table = (
            "| Company | Position | Location | Work Model | Date |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| ↳ | **[Orphan Role](https://x.com/1)** | Remote | Remote | Jun 1 |\n"
        )
        self.assertEqual(self.scraper._parse_tables(table), [])

    def test_date_parsing_handles_month_day_and_year_rollover(self) -> None:
        parsed = datetime.fromisoformat(JobRightScraper._parse_date("Jun 15"))
        self.assertEqual((parsed.month, parsed.day), (6, 15))
        now = datetime.now(tz=timezone.utc)
        self.assertLessEqual(parsed.year, now.year)
        self.assertLessEqual(parsed, now)

    def test_unparseable_date_returns_unknown(self) -> None:
        self.assertEqual(JobRightScraper._parse_date("not a date"), "Unknown")
        self.assertEqual(JobRightScraper._parse_date(""), "Unknown")


if __name__ == "__main__":
    unittest.main()
