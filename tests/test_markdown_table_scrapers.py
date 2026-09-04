import unittest
from datetime import datetime, timedelta, timezone

from scrapers.markdown_table import (
    JobRightScraper,
    SpeedyApplyScraper,
    ZapplyScraper,
    parse_month_day,
    parse_relative_age,
)


SPEEDYAPPLY_SIX_COLUMN_TABLE = """
Some intro text before the table.

| Company | Position | Location | Salary | Posting | Age |
| --- | --- | --- | --- | --- | --- |
| <a href="https://acme.com"><strong>Acme Corp</strong></a> | Software Engineer Intern | Remote | $50/hr | <a href="https://apply.com/123"><img src="apply.svg"></a> | 3d |
| <a href="https://widgets.com"><strong>Widgets Inc</strong></a> | Backend Engineer Intern | New York, NY | $45/hr | <a href="https://apply.com/456"><img src="apply.svg"></a> | 12h |
"""

# speedyapply's main table has no Salary column; the old parser dropped every row.
SPEEDYAPPLY_FIVE_COLUMN_TABLE = """
<!-- TABLE_START -->
| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://www.cfins.com"><strong>Crum & Forster</strong></a> | Data Science Intern - Summer 2027 | Morristown, NJ | <a href="https://careers.example.com/1"><img src="apply.png"></a> | 2d |
<!-- TABLE_END -->
"""

JOBRIGHT_TABLE = """
Some intro text before the table.

| Company | Job Title | Location | Work Model | Date Posted |
| ----- | --------- |  --------- | ---- | ------- |
| **[Databricks](https://databricks.com)** | **[Product Manager Intern](https://databricks.com/careers/123)** | San Francisco, CA | Hybrid | Jun 15 |
| ↳ | **[APM, New Grad](https://databricks.com/careers/456)** | Remote | Remote | Jun 16 |
| **[Roblox](https://roblox.com)** | **[[2027] Associate Product Manager, Early Career](https://jobright.ai/jobs/info/abc)** | San Mateo, CA, United States | On Site | Sep 04 |
"""

ZAPPLY_TABLE = """
| Company | Role | Location | Posted | Visa | **Apply** |
|---------|------|----------|--------|------|----------|
| **Western Digital** | Software Engineer | San Jose, CA | 14m | 🏛 H-1B Company | [<img src="images/apply.png" width="80" alt="Apply">](https://jobs.smartrecruiters.com/WesternDigital/744000138717897) |
"""

NON_JOB_TABLE = """
| Stat | Value |
|---|---|
| Total openings | 42 |
"""


class SpeedyApplyParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = SpeedyApplyScraper()

    def test_first_data_row_is_not_dropped(self) -> None:
        jobs = self.scraper._parse_tables(SPEEDYAPPLY_SIX_COLUMN_TABLE)
        self.assertEqual(len(jobs), 2)

    def test_row_fields_are_extracted_correctly(self) -> None:
        jobs = self.scraper._parse_tables(SPEEDYAPPLY_SIX_COLUMN_TABLE)
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

    def test_five_column_table_without_salary_is_parsed(self) -> None:
        jobs = self.scraper._parse_tables(SPEEDYAPPLY_FIVE_COLUMN_TABLE)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Crum & Forster")
        self.assertEqual(jobs[0].title, "Data Science Intern - Summer 2027")
        self.assertEqual(jobs[0].url, "https://careers.example.com/1")
        self.assertEqual(jobs[0].location, "Morristown, NJ")

    def test_both_table_shapes_parse_in_one_document(self) -> None:
        jobs = self.scraper._parse_tables(SPEEDYAPPLY_SIX_COLUMN_TABLE + SPEEDYAPPLY_FIVE_COLUMN_TABLE)
        self.assertEqual(len(jobs), 3)

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

    def test_non_job_tables_are_ignored(self) -> None:
        self.assertEqual(self.scraper._parse_tables(NON_JOB_TABLE), [])


class JobRightParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = JobRightScraper()

    def test_first_data_row_is_not_dropped(self) -> None:
        jobs = self.scraper._parse_tables(JOBRIGHT_TABLE)
        self.assertEqual(len(jobs), 3)

    def test_nested_brackets_in_link_text_are_handled(self) -> None:
        jobs = self.scraper._parse_tables(JOBRIGHT_TABLE)
        roblox = jobs[2]
        self.assertEqual(roblox.company, "Roblox")
        self.assertEqual(roblox.title, "[2027] Associate Product Manager, Early Career")
        self.assertEqual(roblox.url, "https://jobright.ai/jobs/info/abc")
        self.assertEqual(roblox.posted_at[5:], "09-04")

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

    def test_date_parsing_emits_date_only_with_year_rollover(self) -> None:
        parsed = JobRightScraper._parse_date("Jun 15")
        self.assertRegex(parsed, r"^\d{4}-06-15$")
        as_date = datetime.fromisoformat(parsed).date()
        self.assertLessEqual(as_date, datetime.now(tz=timezone.utc).date())

    def test_unparseable_date_returns_unknown(self) -> None:
        self.assertEqual(JobRightScraper._parse_date("not a date"), "Unknown")
        self.assertEqual(JobRightScraper._parse_date(""), "Unknown")


class ZapplyParsingTests(unittest.TestCase):
    def test_plain_company_and_markdown_image_link_parse(self) -> None:
        jobs = ZapplyScraper()._parse_tables(ZAPPLY_TABLE)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.company, "Western Digital")
        self.assertEqual(job.title, "Software Engineer")
        self.assertEqual(job.location, "San Jose, CA")
        self.assertEqual(job.url, "https://jobs.smartrecruiters.com/WesternDigital/744000138717897")
        self.assertEqual(job.platform, "zapply")
        posted = datetime.fromisoformat(job.posted_at)
        self.assertAlmostEqual(
            (datetime.now(tz=timezone.utc) - posted).total_seconds(),
            timedelta(minutes=14).total_seconds(),
            delta=5,
        )


class PostedCellHelpersTests(unittest.TestCase):
    def test_relative_age_supports_minutes(self) -> None:
        self.assertNotEqual(parse_relative_age("5m"), "Unknown")
        self.assertEqual(parse_relative_age(""), "Unknown")

    def test_month_day_accepts_iso_and_long_month(self) -> None:
        self.assertEqual(parse_month_day("2026-09-04"), "2026-09-04")
        self.assertEqual(parse_month_day("September 4, 2026"), "2026-09-04")


if __name__ == "__main__":
    unittest.main()
