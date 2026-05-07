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


EARLY_CAREER_KEYWORDS: set[str] = {
    "apm",
    "associate product manager",
    "early career",
    "entry level",
    "entry-level",
    "intern",
    "internship",
    "junior",
    "new grad",
    "new graduate",
    "pm intern",
    "product intern",
    "product manager intern",
    "university grad",
}


SCOPED_EARLY_ROLE_KEYWORDS: set[str] = {
    "apm",
    "associate product manager",
    "pm intern",
    "product intern",
    "product manager intern",
}


def _matched_spans(keyword: str, text: str) -> list[tuple[int, int]]:
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return [match.span() for match in re.finditer(pattern, text, re.IGNORECASE)]


def _span_contains(container: tuple[int, int], inner: tuple[int, int]) -> bool:
    return container[0] <= inner[0] and inner[1] <= container[1]


def _has_blocking_exclusion(
    title: str,
    keywords: list[str],
    excluded_keywords: list[str],
) -> bool:
    positive_spans: list[tuple[int, int]] = []
    for keyword in keywords:
        positive_spans.extend(_matched_spans(keyword, title))

    for excluded in excluded_keywords:
        for excluded_span in _matched_spans(excluded, title):
            if not any(_span_contains(positive_span, excluded_span) for positive_span in positive_spans):
                return True
    return False


def _matches_location_filter(filter_value: str, location: str) -> bool:
    """
    Match long location phrases by substring, but require short abbreviations
    like "US", "CA", or "NY" to appear as standalone tokens.
    """
    needle = filter_value.strip().lower()
    if not needle:
        return False
    location_without_periods = location.replace(".", "")
    needle_without_periods = needle.replace(".", "")
    if len(needle) <= 3:
        return bool(
            re.search(
                r"(?<![a-z0-9])" + re.escape(needle_without_periods) + r"(?![a-z0-9])",
                location_without_periods,
            )
        )
    return needle in location or needle_without_periods in location_without_periods


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
        Returns True when the title is in scope and none of the excluded terms
        match. If a channel mixes early-career keywords with role keywords, the
        title must match both groups; this prevents broad matches like "HR
        Intern" or non-entry "Software Engineer".
        """
        if _has_blocking_exclusion(title, keywords, excluded_keywords):
            return False

        if any(
            keyword.strip().lower() in SCOPED_EARLY_ROLE_KEYWORDS
            and _word_match(keyword, title)
            for keyword in keywords
        ):
            return True

        early_career_keywords = [
            kw for kw in keywords if kw.strip().lower() in EARLY_CAREER_KEYWORDS
        ]
        role_keywords = [
            kw for kw in keywords if kw.strip().lower() not in EARLY_CAREER_KEYWORDS
        ]

        if early_career_keywords and role_keywords:
            has_early_career = any(_word_match(kw, title) for kw in early_career_keywords)
            has_role = any(_word_match(kw, title) for kw in role_keywords)
            return has_early_career and has_role

        return any(_word_match(kw, title) for kw in keywords)

    def matches_location(
        self,
        location: str,
        locations_filter: list[str],
    ) -> bool:
        """
        Returns True if locations_filter is empty, if the job has no structured
        location, or if any configured location matches the job location.
        """
        if not locations_filter:
            return True
        if not location:
            return True
        location_lower = location.lower()
        return any(_matches_location_filter(loc, location_lower) for loc in locations_filter)
