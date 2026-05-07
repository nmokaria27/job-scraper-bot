import os
from dotenv import load_dotenv

load_dotenv()


def _parse_list(env_var: str, default: list[str]) -> list[str]:
    """Split a comma-separated env var into a stripped list, or use default."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


# Required — raises ValueError on startup if not set
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

KEYWORDS: list[str] = _parse_list(
    "KEYWORDS",
    default=[
        "intern",
        "internship",
        "new grad",
        "swe",
        "software engineer",
        "ml engineer",
        "ai engineer",
        "research engineer",
        "machine learning",
        "data scientist",
    ],
)

# Jobs whose titles contain any of these are excluded (seniority filter)
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
    ],
)

# If set, only jobs whose location contains one of these strings pass through.
# Leave empty (default) to allow all locations.
LOCATIONS: list[str] = _parse_list("LOCATIONS", default=[])

# Cap notifications per run to avoid Discord rate limits and channel spam
MAX_NOTIFICATIONS_PER_RUN: int = int(os.getenv("MAX_NOTIFICATIONS_PER_RUN", "25"))

SEEN_JOBS_PATH: str = os.getenv("SEEN_JOBS_PATH", "seen_jobs.json")

# How many days before a seen job ID is pruned from seen_jobs.json
SEEN_JOBS_MAX_AGE_DAYS: int = int(os.getenv("SEEN_JOBS_MAX_AGE_DAYS", "30"))

REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "10"))

# Seconds to sleep between successive company API calls (be polite to ATS APIs)
SLEEP_BETWEEN_COMPANIES: float = float(os.getenv("SLEEP_BETWEEN_COMPANIES", "1.5"))


def validate() -> None:
    """Call this at startup to catch missing required config early."""
    if not DISCORD_WEBHOOK_URL:
        raise ValueError(
            "DISCORD_WEBHOOK_URL environment variable is required but not set. "
            "Copy .env.example to .env and fill in your webhook URL."
        )
