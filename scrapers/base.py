import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Job:
    id: str          # Unique identifier used for deduplication
    title: str
    company: str
    location: str
    url: str
    platform: str    # "greenhouse", "lever", "ashby", "simplify", "hackernews"
    posted_at: str   # ISO string or "Unknown"


def _word_match(keyword: str, text: str) -> bool:
    """
    Return True if `keyword` appears as a complete word in `text`.

    Uses regex word boundaries so short terms like "vp", "lead", "swe"
    don't accidentally match substrings inside longer words:
      "intern"  → matches "Intern", "INTERN,"  but NOT "Internal"
      "vp"      → matches "VP Engineering"      but NOT "MVP"
      "lead"    → matches "Tech Lead"           but NOT "Leadership Program"
      "swe"     → matches "SWE Intern"          but NOT "Sweepstakes"
    """
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


class BaseScraper(ABC):
    @abstractmethod
    async def fetch_jobs(self, company_slug: str) -> list[Job]:
        """Fetch all open jobs for a given company slug."""
        pass

    def matches_keywords(
        self,
        title: str,
        keywords: list[str],
        excluded_keywords: list[str],
    ) -> bool:
        """
        Returns True if ANY keyword matches the title as a whole word AND
        NONE of the excluded_keywords match the title as a whole word.
        Case-insensitive. Uses word-boundary matching to prevent substring
        false positives (e.g. 'intern' inside 'internal', 'vp' inside 'mvp').
        """
        has_keyword = any(_word_match(kw, title) for kw in keywords)
        has_excluded = any(_word_match(ex, title) for ex in excluded_keywords)
        return has_keyword and not has_excluded

    def matches_location(
        self,
        location: str,
        locations_filter: list[str],
    ) -> bool:
        """
        Returns True if locations_filter is empty (no filter active) or
        if any of the filter strings is a substring of the job's location.
        Case-insensitive.
        """
        if not locations_filter:
            return True
        location_lower = (location or "").lower()
        return any(loc.lower() in location_lower for loc in locations_filter)
