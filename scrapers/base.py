from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Job:
    id: str          # Unique identifier used for deduplication
    title: str
    company: str
    location: str
    url: str
    platform: str    # "greenhouse", "lever", or "ashby"
    posted_at: str   # ISO string or "Unknown"


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
        Returns True if ANY keyword is found in the title AND
        NONE of the excluded_keywords are found in the title.
        Case-insensitive.
        """
        title_lower = title.lower()
        has_keyword = any(kw.lower() in title_lower for kw in keywords)
        has_excluded = any(ex.lower() in title_lower for ex in excluded_keywords)
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
