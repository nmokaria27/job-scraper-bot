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
    platform: str    # "greenhouse", "lever", "ashby", "simplify", "speedyapply", "jobright", "hackernews"
    posted_at: str   # ISO string or "Unknown"


# Display names for ATS slugs that don't title-case cleanly.
COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "openai": "OpenAI",
    "deepmind": "Google DeepMind",
    "ashby": "Ashby",
    "anyscale": "Anyscale",
}


def company_name_from_slug(slug: str) -> str:
    """Turn an ATS board slug into a readable company name."""
    key = slug.strip().lower()
    if key in COMPANY_DISPLAY_NAMES:
        return COMPANY_DISPLAY_NAMES[key]
    return slug.replace("-", " ").replace("_", " ").title()


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
    pattern = _keyword_pattern(keyword)
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
    pattern = _keyword_pattern(keyword)
    return [match.span() for match in re.finditer(pattern, text, re.IGNORECASE)]


def _keyword_pattern(keyword: str) -> str:
    escaped = re.escape(keyword)
    # Use alphanumeric boundaries instead of \b so symbol terms like "c++"
    # and "c#" match correctly while still avoiding substring matches.
    return r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"


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
    # Decide token-vs-substring on the period-stripped form so "u.s." behaves
    # like "us" (a whole token) instead of matching inside "a-us-tralia".
    if len(needle_without_periods) <= 3:
        return bool(
            re.search(
                r"(?<![a-z0-9])" + re.escape(needle_without_periods) + r"(?![a-z0-9])",
                location_without_periods,
            )
        )
    return needle in location or needle_without_periods in location_without_periods


def _location_token_match(needle: str, location: str) -> bool:
    """Whole-word match for excluded locations ("india" must not hit "Indianapolis")."""
    needle = needle.strip().lower().replace(".", "")
    if not needle:
        return False
    return bool(
        re.search(
            r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])",
            location.replace(".", ""),
        )
    )


# Location strings that carry a work model but no place. Treated like a blank
# location (benefit of the doubt) instead of failing every location filter.
WORK_MODEL_ONLY_LOCATIONS: set[str] = {
    "remote",
    "hybrid",
    "in-office",
    "in office",
    "on-site",
    "onsite",
    "on site",
    "n/a",
    "na",
    "tbd",
    "unknown",
    "see posting",
    "multiple locations",
    "multiple",
    "various",
    "flexible",
    "not specified",
    "remote / not specified",
}

# Positive location tokens that are too ambiguous to override a foreign
# location on their own: "remote" (Remote - Canada), "ca" (Canada),
# "wa" (Western Australia).
WEAK_LOCATION_TOKENS: set[str] = {"remote", "ca", "wa"}


_COUNT_ONLY_LOCATION_RE = re.compile(r"^\d+\s+locations?$")


def has_real_location(location: str) -> bool:
    """True when the location names an actual place, not just a work model."""
    normalized = re.sub(r"\s+", " ", (location or "")).strip().lower()
    if not normalized or normalized in WORK_MODEL_ONLY_LOCATIONS:
        return False
    return not _COUNT_ONLY_LOCATION_RE.match(normalized)


def location_is_allowed(
    location: str,
    locations_filter: list[str],
    excluded_locations: list[str] | None = None,
) -> bool:
    """
    Location filter used by every channel.

    - No filter, blank location, or work-model-only location -> allowed.
    - Otherwise at least one positive entry must match.
    - If an excluded (foreign) location also matches, keep the job only when a
      concrete positive token matched too. "Remote - US or Canada" passes,
      "Remote - Canada" and "Toronto, ON, CA" do not.
    """
    if not locations_filter:
        return True
    if not has_real_location(location):
        return True

    location_lower = location.lower()
    positives = [loc for loc in locations_filter if _matches_location_filter(loc, location_lower)]
    if not positives:
        return False

    if excluded_locations and any(
        _location_token_match(loc, location_lower) for loc in excluded_locations
    ):
        strong = [loc for loc in positives if loc.strip().lower() not in WEAK_LOCATION_TOKENS]
        return bool(strong)

    return True


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
            kw
            for kw in keywords
            if kw.strip().lower() in EARLY_CAREER_KEYWORDS
            and kw.strip().lower() not in SCOPED_EARLY_ROLE_KEYWORDS
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
        excluded_locations: list[str] | None = None,
    ) -> bool:
        """
        Returns True if locations_filter is empty, if the job has no real
        location, or if a configured location matches and no excluded
        location vetoes it. See `location_is_allowed`.
        """
        return location_is_allowed(location, locations_filter, excluded_locations)
