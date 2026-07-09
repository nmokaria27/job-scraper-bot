# New Job Source Integration — Changes Summary

## Overview

Integrated 3 new GitHub job listing sources into the scraper bot, adding ~4,100 new job postings per run.

## New Sources

| Source | Format | Jobs Found | Platform Tag |
|--------|--------|------------|--------------|
| vanshb03/Summer2027-Internships | JSON (SimplifyJobs format) | 133 | `simplify` |
| vanshb03/New-Grad-2027 | JSON (SimplifyJobs format) | 647 | `simplify` |
| speedyapply/2026-SWE-College-Jobs | Markdown tables | 16 | `speedyapply` |
| speedyapply/2026-AI-College-Jobs | Markdown tables | 37 | `speedyapply` |
| jobright-ai/2026-Product-Management-Internship | Markdown tables | 56 | `jobright` |
| jobright-ai/2026-Product-Management-New-Grad | Markdown tables | 109 | `jobright` |

## Files Modified

### `config.py`
- Added vanshb03 JSON URLs to `SIMPLIFY_URLS` default list (same JSON format as SimplifyJobs, no new scraper needed)
- Added `SPEEDYAPPLY_URLS` config section with default URLs for both speedyapply repos
- Added `JOBRIGHT_URLS` config section with default URLs for both jobright-ai repos

### `companies.py`
- Added `"speedyapply": []` and `"jobright": []` as bulk scraper markers in `COMPANIES` dict
- Updated `"simplify"` comment to mention vanshb03 repos

### `scrapers/markdown_table.py` (new file)
- `SpeedyApplyScraper`: Parses markdown tables from speedyapply GitHub READMEs
  - Columns: Company (HTML `<a>` tag), Position, Location, Salary, Posting (apply link), Age (relative like "7d")
  - Converts relative age to approximate ISO timestamp
- `JobRightScraper`: Parses markdown tables from jobright-ai GitHub READMEs
  - Columns: Company (markdown link), Position (markdown link = job URL), Location, Work Model, Date ("Jun 28")
  - Handles `↳` continuation rows that inherit company from previous row
  - Parses month/day dates to ISO timestamps (assumes current year, falls back to previous year if future)

### `scrapers/__init__.py`
- Added imports for `SpeedyApplyScraper` and `JobRightScraper`
- Added both to `__all__`

### `main.py`
- Added import for `SpeedyApplyScraper` and `JobRightScraper`
- Added both to `bulk_scrapers` dict in `scrape_all_raw()` so they run once per run
- Added `speedyapply` and `jobright` to `PLATFORM_DEDUPE_PRIORITY` (priority 2, between simplify and hackernews)

### `test_run.py`
- Added imports for new scrapers
- Added `test_speedyapply()` and `test_jobright()` test functions
- Added `--speedyapply` and `--jobright` CLI flags to dispatch to new test functions

### `.env.example`
- Updated `SIMPLIFY_URLS` to include vanshb03 repos
- Added `SPEEDYAPPLY_URLS` and `JOBRIGHT_URLS` sections with default URLs

## Sources Not Integrated

- **nemani/Rotational-PM-Opportunities**: Old static list, not regularly updated. Low value.
- **apmlist.com**: SEO/content site, not a structured job board. Not suitable for automated scraping.
- **intern-list.com**: Same as above — not a structured job board.

## Testing

All three scraper types verified working:
```
python test_run.py --simplify       # 3,884 jobs (SimplifyJobs + vanshb03)
python test_run.py --speedyapply    # 53 jobs (speedyapply SWE + AI)
python test_run.py --jobright       # 165 jobs (jobright-ai PM intern + new grad)
```
