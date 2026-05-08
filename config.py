import json
import os
import re
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _strip_control_chars(raw: str) -> str:
    """Remove raw ASCII control characters that can break JSON secrets."""
    return "".join(ch for ch in raw if ch >= " " or ch in "\n\r\t")


def _parse_int(env_var: str, default: int) -> int:
    """Parse an int env var, treating missing/blank as default."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    return int(raw)


def _parse_float(env_var: str, default: float) -> float:
    """Parse a float env var, treating missing/blank as default."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    return float(raw)


def _parse_bool(env_var: str, default: bool) -> bool:
    """Parse a boolean env var, treating missing/blank as default."""
    raw = os.getenv(env_var, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _parse_list(env_var: str, default: list[str]) -> list[str]:
    """Split a comma-separated env var into a stripped list, or use default."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_json_secret(raw: str, source: str) -> object:
    """
    Parse JSON from a secret value.

    GitHub secrets sometimes end up with pasted control characters inside long
    string values. If strict parsing fails for that reason, retry after
    stripping raw control characters.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        if "Invalid control character" not in str(e):
            raise ValueError(f"{source} is invalid JSON: {e}") from e

    sanitized = _strip_control_chars(raw).replace("\r", "").replace("\n", "").replace("\t", "")
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError as e:
        raise ValueError(f"{source} is invalid JSON: {e}") from e


def _normalize_channel_name(name: object) -> str:
    collapsed = re.sub(r"\s+", " ", str(name)).strip()
    return re.sub(r"\s*-\s*", "-", collapsed)


def _normalize_webhook_url(url: object) -> str:
    return "".join(str(url).split())


# ---------------------------------------------------------------------------
# Channel configuration
# ---------------------------------------------------------------------------

@dataclass
class ChannelConfig:
    """One Discord channel with its own webhook, keywords, and filters."""
    name: str
    webhook_url: str
    keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Single-channel fallback defaults  (used when no channels.json exists)
# ---------------------------------------------------------------------------

DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

DEFAULT_SWE_KEYWORDS: list[str] = [
    "intern",
    "internship",
    "new grad",
    "new graduate",
    "university grad",
    "early career",
    "entry level",
    "software engineer",
    "swe",
    "ml engineer",
    "ai engineer",
    "research engineer",
    "machine learning",
    "data scientist",
    "data engineer",
    "backend",
    "frontend",
    "full stack",
    "fullstack",
    "platform engineer",
    "computer vision",
    "nlp",
]

DEFAULT_PM_KEYWORDS: list[str] = [
    "product manager",
    "product management",
    "tpm",
    "technical program manager",
    "apm",
    "associate product manager",
    "pm intern",
    "product intern",
    "product manager intern",
    "product analyst",
    "product operations",
    "intern",
    "internship",
    "new grad",
    "new graduate",
    "early career",
]

DEFAULT_SWE_EXCLUDED_KEYWORDS: list[str] = [
    "senior",
    "staff",
    "lead",
    "director",
    "principal",
    "manager",
    "mid-level",
    "mid level",
    "vp",
    "head of",
    "president",
    "officer",
    "distinguished",
    "partner",
    "ii",
    "iii",
    "iv",
    "v",
    "experienced",
]

DEFAULT_PM_EXCLUDED_KEYWORDS: list[str] = [
    "senior",
    "staff",
    "lead",
    "director",
    "principal",
    "manager",
    "mid-level",
    "mid level",
    "vp",
    "head of",
    "president",
    "officer",
    "distinguished",
    "ii",
    "iii",
    "iv",
    "v",
    "experienced",
]

DEFAULT_LOCATIONS: list[str] = [
    "us",
    "usa",
    "united states",
    "remote",
    "san francisco",
    "bay area",
    "california",
    "ca",
    "new york",
    "ny",
    "seattle",
    "wa",
    "washington d.c.",
    "dc",
    "maryland",
    "md",
    "virginia",
    "va",
    "austin",
    "tx",
    "boston",
    "ma",
    "chicago",
    "il",
]

KEYWORDS: list[str] = _parse_list("KEYWORDS", default=DEFAULT_SWE_KEYWORDS)
EXCLUDED_KEYWORDS: list[str] = _parse_list(
    "EXCLUDED_KEYWORDS",
    default=DEFAULT_SWE_EXCLUDED_KEYWORDS,
)
LOCATIONS: list[str] = _parse_list("LOCATIONS", default=[])

# ---------------------------------------------------------------------------
# Rate limiting / run behaviour
# ---------------------------------------------------------------------------

MAX_NOTIFICATIONS_PER_RUN: int = _parse_int("MAX_NOTIFICATIONS_PER_RUN", 25)
SLEEP_BETWEEN_COMPANIES: float = _parse_float("SLEEP_BETWEEN_COMPANIES", 1.5)
REQUEST_TIMEOUT: int = _parse_int("REQUEST_TIMEOUT", 10)
RECENT_POSTING_MAX_AGE_HOURS: int = _parse_int("RECENT_POSTING_MAX_AGE_HOURS", 24)
ATS_CONCURRENCY: int = _parse_int("ATS_CONCURRENCY", 5)
SEND_NO_NEW_SUMMARY: bool = _parse_bool("SEND_NO_NEW_SUMMARY", False)

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SEEN_JOBS_PATH: str = os.getenv("SEEN_JOBS_PATH", "seen_jobs.json")
SEEN_JOBS_MAX_AGE_DAYS: int = _parse_int("SEEN_JOBS_MAX_AGE_DAYS", 30)

# ---------------------------------------------------------------------------
# SimplifyJobs scraper
# ---------------------------------------------------------------------------

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

HN_MAX_COMMENTS: int = _parse_int("HN_MAX_COMMENTS", 500)
HN_SEMAPHORE_LIMIT: int = _parse_int("HN_SEMAPHORE_LIMIT", 10)

# ---------------------------------------------------------------------------
# Channel file path
# ---------------------------------------------------------------------------

CHANNELS_PATH: str = os.getenv("CHANNELS_PATH", "channels.json")


# ---------------------------------------------------------------------------
# Channel loader
# ---------------------------------------------------------------------------

def load_channels(require_webhooks: bool = True) -> list[ChannelConfig]:
    """
    Load channel configurations.  Checked in priority order:

    1. SWE_WEBHOOK_URL / PM_WEBHOOK_URL — simple two-channel mode
    2. CHANNELS_JSON env var            — raw JSON string
    3. channels.json file               — local development
    4. Single-channel fallback          — uses DISCORD_WEBHOOK_URL + filters

    Each channel gets its own webhook URL, keyword list, and exclusion list so
    the scraper can fan out one scrape run to many Discord channels.
    """
    channels: list[ChannelConfig] = []

    def _load_default_channels_from_env() -> list[ChannelConfig]:
        default_channels: list[ChannelConfig] = []
        swe_webhook_url = _normalize_webhook_url(os.getenv("SWE_WEBHOOK_URL", ""))
        pm_webhook_url = _normalize_webhook_url(os.getenv("PM_WEBHOOK_URL", ""))

        if swe_webhook_url:
            default_channels.append(
                ChannelConfig(
                    name="swe-jobs",
                    webhook_url=swe_webhook_url,
                    keywords=DEFAULT_SWE_KEYWORDS,
                    excluded_keywords=DEFAULT_SWE_EXCLUDED_KEYWORDS,
                    locations=DEFAULT_LOCATIONS,
                )
            )
        if pm_webhook_url:
            default_channels.append(
                ChannelConfig(
                    name="pm-jobs",
                    webhook_url=pm_webhook_url,
                    keywords=DEFAULT_PM_KEYWORDS,
                    excluded_keywords=DEFAULT_PM_EXCLUDED_KEYWORDS,
                    locations=DEFAULT_LOCATIONS,
                )
            )
        return default_channels

    def _coerce_channels(data: object, source: str) -> list[ChannelConfig]:
        if isinstance(data, dict) and "channels" in data:
            data = data["channels"]
        if not isinstance(data, list):
            raise ValueError(f"{source} must be a JSON list or an object with a 'channels' list")

        parsed: list[ChannelConfig] = []
        for idx, entry in enumerate(data, 1):
            if not isinstance(entry, dict):
                raise ValueError(f"{source} channel #{idx} must be an object")
            try:
                channel = ChannelConfig(**entry)
            except TypeError as e:
                raise ValueError(f"{source} channel #{idx} is invalid: {e}") from e

            channel.name = _normalize_channel_name(channel.name)
            channel.webhook_url = _normalize_webhook_url(channel.webhook_url)
            if not channel.name:
                raise ValueError(f"{source} channel #{idx} is missing name")
            if not isinstance(channel.keywords, list):
                raise ValueError(f"{source} channel '{channel.name}' keywords must be a list")
            if not isinstance(channel.excluded_keywords, list):
                raise ValueError(
                    f"{source} channel '{channel.name}' excluded_keywords must be a list"
                )
            if not isinstance(channel.locations, list):
                raise ValueError(f"{source} channel '{channel.name}' locations must be a list")

            channel.keywords = [str(item).strip() for item in channel.keywords if str(item).strip()]
            channel.excluded_keywords = [
                str(item).strip() for item in channel.excluded_keywords if str(item).strip()
            ]
            channel.locations = [
                str(item).strip() for item in channel.locations if str(item).strip()
            ]
            if not channel.keywords:
                raise ValueError(f"{source} channel '{channel.name}' must include at least one keyword")

            parsed.append(channel)

        names = [ch.name for ch in parsed]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"{source} has duplicate channel name(s): {duplicates}")

        return parsed

    # --- 1. Simple two-channel env var mode ---
    channels = _load_default_channels_from_env()
    if channels:
        print(f"[INFO] Loaded {len(channels)} channel(s) from SWE_WEBHOOK_URL/PM_WEBHOOK_URL")

    # --- 2. CHANNELS_JSON env var ---
    raw = os.getenv("CHANNELS_JSON", "").strip()
    if not channels and raw:
        try:
            data = _load_json_secret(raw, "CHANNELS_JSON env var")
            channels = _coerce_channels(data, "CHANNELS_JSON")
            print(f"[INFO] Loaded {len(channels)} channel(s) from CHANNELS_JSON env var")
        except ValueError:
            raise

    # --- 3. channels.json file ---
    if not channels:
        try:
            with open(CHANNELS_PATH, "r") as f:
                data = json.load(f)
            channels = _coerce_channels(data, CHANNELS_PATH)
            print(f"[INFO] Loaded {len(channels)} channel(s) from {CHANNELS_PATH}")
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as e:
            raise ValueError(f"{CHANNELS_PATH} is invalid: {e}")

    # --- 4. Fallback: single channel from env vars ---
    if not channels:
        if require_webhooks and not DISCORD_WEBHOOK_URL:
            raise ValueError(
                "No channel configuration found. Provide one of:\n"
                "  1. CHANNELS_JSON env var  (raw JSON string)\n"
                "  2. channels.json file\n"
                "  3. DISCORD_WEBHOOK_URL env var  (single-channel mode)"
            )
        channels = [
            ChannelConfig(
                name="default",
                webhook_url=DISCORD_WEBHOOK_URL,
                keywords=KEYWORDS,
                excluded_keywords=EXCLUDED_KEYWORDS,
                locations=LOCATIONS,
            )
        ]
        print("[INFO] Using single-channel mode (DISCORD_WEBHOOK_URL)")

    # Validate webhook URLs when needed
    if require_webhooks:
        for ch in channels:
            if not ch.webhook_url:
                raise ValueError(f"Channel '{ch.name}' is missing webhook_url")

    return channels


def validate() -> None:
    """Validate that normal-run notification configuration is available."""
    load_channels(require_webhooks=True)
