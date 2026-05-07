import os
from dotenv import load_dotenv

load_dotenv()


def _parse_list(env_var: str, default: list[str]) -> list[str]:
    """Split a comma-separated env var into a stripped list, or use default."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Required
# ---------------------------------------------------------------------------

# Raises ValueError on startup if not set
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# Job filtering
# ---------------------------------------------------------------------------

KEYWORDS: list[str] = _parse_list(
    "KEYWORDS",
    default=[
        # Internship
        "intern",
        "internship",
        # New grad / entry level
        "new grad",
        "new graduate",
        "entry level",
        "entry-level",
        "junior",
        # Role types
        "software engineer",
        "swe",
        "ml engineer",
        "ai engineer",
        "research engineer",
        "machine learning",
        "data scientist",
        "data engineer",
        "backend engineer",
        "frontend engineer",
        "full stack",
        "fullstack",
        "platform engineer",
        "infrastructure engineer",
        "site reliability",
        "sre",
        "devops",
        "research scientist",
    ],
)

# Jobs whose titles contain ANY of these whole words are excluded.
# Uses word-boundary matching — "lead" won't match "leadership", "vp" won't match "mvp".
EXCLUDED_KEYWORDS: list[str] = _parse_list(
    "EXCLUDED_KEYWORDS",
    default=[
        "senior",
        "staff",
        "lead",
        "director",
        "principal",
        "manager",
        "vp",
        "head of",
        "president",
        "officer",
        "distinguished",
        "partner",
    ],
)

# Optional — only show jobs whose location contains one of these strings.
# Leave empty (default) to allow all locations.
LOCATIONS: list[str] = _parse_list("LOCATIONS", default=[])

# ---------------------------------------------------------------------------
# Rate limiting / run behaviour
# ---------------------------------------------------------------------------

# Cap Discord notifications per run (prevent spam if a company bulk-posts)
MAX_NOTIFICATIONS_PER_RUN: int = int(os.getenv("MAX_NOTIFICATIONS_PER_RUN", "25"))

# Seconds to sleep between successive ATS company API calls
SLEEP_BETWEEN_COMPANIES: float = float(os.getenv("SLEEP_BETWEEN_COMPANIES", "1.5"))

REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SEEN_JOBS_PATH: str = os.getenv("SEEN_JOBS_PATH", "seen_jobs.json")

# Days before a seen job ID is pruned from seen_jobs.json
SEEN_JOBS_MAX_AGE_DAYS: int = int(os.getenv("SEEN_JOBS_MAX_AGE_DAYS", "30"))

# ---------------------------------------------------------------------------
# SimplifyJobs scraper
# ---------------------------------------------------------------------------

# Comma-separated list of raw JSON URLs to fetch.
# Default includes both Summer internships AND New Grad positions.
# Update the year in the Summer URL when a new internship cycle begins
# (e.g. Summer2026 → Summer2027) or override via SIMPLIFY_URLS secret.
SIMPLIFY_URLS: list[str] = _parse_list(
    "SIMPLIFY_URLS",
    default=[
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    ],
)

# ---------------------------------------------------------------------------
# HackerNews scraper
# ---------------------------------------------------------------------------

# Max top-level comments to fetch from the monthly "Who is Hiring?" thread.
# HN threads typically have 400-800 comments; 200 (old default) missed 50%+.
HN_MAX_COMMENTS: int = int(os.getenv("HN_MAX_COMMENTS", "500"))

# Max concurrent HN API requests (semaphore limit)
HN_SEMAPHORE_LIMIT: int = int(os.getenv("HN_SEMAPHORE_LIMIT", "10"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate() -> None:
    """Call this at startup to catch missing required config early."""
    if not DISCORD_WEBHOOK_URL:
        raise ValueError(
            "DISCORD_WEBHOOK_URL environment variable is required but not set. "
            "Copy .env.example to .env and fill in your webhook URL."
        )
