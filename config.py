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
    # Locations that veto a match unless a concrete US location also appears.
    # Stops "Remote - Canada" / "Remote in UK" from slipping through "remote".
    excluded_locations: list[str] = field(default_factory=list)
    # Employers whose postings are never notified, matched whole-word against
    # the job's company name ("meta" hits "Meta" / "Meta Platforms", not
    # "Metabase").
    excluded_companies: list[str] = field(default_factory=list)
    # Which Jev question profile judges this channel: "swe", "pm", or "off".
    # Blank falls back to a channel-name map, then "swe". See jev.profile_for.
    jev_profile: str = ""


# ---------------------------------------------------------------------------
# Built-in channel defaults
#
# Two channels are supported out of the box:
#   PM_WEBHOOK_URL        -> "pm-jobs"          entry-level PM / APM / TPM roles
#   FULL_TIME_WEBHOOK_URL -> "swe-ai-full-time" new-grad / entry-level SWE, AI, ML
#
# Keyword matching is whole-word and case-insensitive (see scrapers/base.py).
# Exclusions block a title unless the excluded word sits inside a matched
# positive phrase ("manager" inside "product manager" is fine).
# ---------------------------------------------------------------------------

DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

# Seniority / level terms that mark a role as not entry-level. Shared by both
# channels. "sr" also matches "Sr." because the matcher uses alphanumeric
# boundaries.
_SENIORITY_EXCLUSIONS: list[str] = [
    "senior",
    "sr",
    "snr",
    "staff",
    "lead",
    "principal",
    "distinguished",
    "architect",
    "director",
    "manager",
    "head",
    "head of",
    "chief",
    "vp",
    "svp",
    "evp",
    "avp",
    "president",
    "officer",
    "partner",
    "founding",
    "expert",
    "experienced",
    "all levels",
    "supervisor",
    "specialist",
    "consultant",
    "consulting",
    "technologist",
    "postdoc",
    "postdoctoral",
    "lmts",   # Salesforce Lead / Principal / Senior Member of Technical Staff
    "pmts",
    "smts",
    "mid",
    "mid-level",
    "mid level",
    "ii",
    "iii",
    "iv",
    "v",
    "2",
    "3",
    "4",
    "5",
    "6",
    "l2",
    "l3",
    "l4",
    "l5",
    "l6",
    "e3",
    "e4",
    "e5",
    "e6",
]

# Employment types that are not full-time entry-level positions.
_NON_FULL_TIME_EXCLUSIONS: list[str] = [
    "intern",
    "interns",
    "internship",
    "internships",
    "student",
    "co-op",
    "coop",
    "apprentice",
    "apprenticeship",
    "contractor",
    "contract",
    "temporary",
    "seasonal",
    "part-time",
    "part time",
    "hourly",
]

DEFAULT_PM_KEYWORDS: list[str] = [
    "product manager",
    "product management",
    "product owner",
    "technical product manager",
    "tpm",
    "technical program manager",
    "apm",
    "associate product manager",
    "rotational product manager",
    "product analyst",
    "product operations",
    "product ops",
]

DEFAULT_PM_EXCLUDED_KEYWORDS: list[str] = [
    *_NON_FULL_TIME_EXCLUSIONS,
    *_SENIORITY_EXCLUSIONS,
    "group",              # Group Product Manager
    # Security / customer-facing program management
    "security",
    "cyber",
    "cybersecurity",
    "customer",
    # Retail / merchandising / non-tech "product" roles
    "retail",
    "store",
    "merchandising",
    "apparel",
    "fashion",
    "footwear",
    "educator",
    "seasonal",
    "sales",
    "marketing",
    "clinical",
    "nurse",
    "construction",
    "investor relations",
    "tax",
]

DEFAULT_SWE_FULL_TIME_KEYWORDS: list[str] = [
    # Core software
    "software engineer",
    "software engineering",
    "software developer",
    "software development",
    "software dev",
    # "New Graduate Engineer, Software" / "Backend Engineer Graduate" forms.
    # Bare "engineer" / "developer" are deliberately NOT positives: they let
    # every "Systems Engineer" / "Test Engineer" / "Salesforce Developer" in.
    "engineer, software",
    "engineer - software",
    "engineer graduate",
    "graduate engineer",
    "web developer",
    "programmer",
    "sde",
    "swe",
    "member of technical staff",
    "mts",
    # AI / ML
    "ml engineer",
    "machine learning",
    "ai engineer",
    "ai/ml",
    "artificial intelligence",
    "deep learning",
    "generative ai",
    "genai",
    "llm",
    "agentic",
    "nlp",
    "computer vision",
    "research engineer",
    "research scientist",
    "applied scientist",
    "ai researcher",
    "ai research",
    "applied ai",
    "applied ml",
    "mlops",
    # Data
    "data scientist",
    "data science",
    "data engineer",
    "analytics engineer",
    # Specialisations
    "backend",
    "back-end",
    "frontend",
    "front-end",
    "full stack",
    "fullstack",
    "full-stack",
    "platform engineer",
    "infrastructure engineer",
    "embedded software",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "quantitative developer",
    "quant developer",
    # --- Added 2026-09-17 from the first-pass experiment (jev_eval.py --first-pass).
    # The "no bare engineer/developer" rule above made entry-level titles that lean on
    # a LEVEL SUFFIX invisible: "Software Engineer I", "Mobile Developer 1",
    # "Junior Developer", "AI Compiler Engineer". Bare "engineer i" / "engineer 1" are
    # deliberately absent - they catch "Field Service Engineer 1" and "Product Engineer I".
    # Verified against the golden corpus: 0 must-rejects accepted, 0 must-accepts lost.
    "software engineer i",
    "software engineer 1",
    "software developer i",
    "software developer 1",
    "sde i",
    "sde 1",
    "swe i",
    "swe 1",
    "developer i",
    "developer 1",
    "junior developer",
    "junior software",
    "junior programmer",
    "junior engineer",
    "associate software engineer",
    "associate engineer",
    # AI/ML, retrieval and infra titles matching the target profile (RAG, vLLM,
    # reranking, knowledge graphs, model serving).
    "compiler engineer",
    "inference engineer",
    "ml infrastructure",
    "ml platform",
    "model serving",
    "search engineer",
    "relevance engineer",
    "ranking engineer",
    "search relevance",
    "recommender system",
    "recommendation system",
    "recommendation engine",
    "information retrieval",
    "perception engineer",
    "speech recognition",
    "speech engineer",
    "knowledge graph",
    "foundation model",
    "large language model",
    "distributed systems",
    "api engineer",
    "systems software",
    "forward deployed",
]

DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS: list[str] = [
    *_NON_FULL_TIME_EXCLUSIONS,
    *_SENIORITY_EXCLUSIONS,
    # Customer-facing / go-to-market roles that still say "engineer"
    "sales",
    "solutions engineer",
    "solution engineer",
    "customer engineer",
    "customer success",
    "customer support",
    "customer service",
    "support engineer",
    "support specialist",
    "technical support",
    "product support",
    "it support",
    "help desk",
    "account",
    "accounting",
    "accountant",
    "business development",
    "developer advocate",
    "developer relations",
    "devrel",
    "advocate",
    "evangelist",
    "technical writer",
    "writer",
    "recruiter",
    "recruiting",
    "talent",
    "people systems",
    "people strategy",
    "people operations",
    "people analytics",
    "hr",
    "payroll",
    # Hardware / physical engineering disciplines
    "hardware",
    "mechanical",
    "electrical",
    "civil",
    "structural",
    "chemical",
    "materials",
    "circuit",
    "analog",
    "rf",
    "asic",
    "fpga",
    "silicon",
    "semiconductor",
    "photonics",
    "optical",
    "optics",
    "thermal",
    "packaging",
    "manufacturing engineer",
    "process engineer",
    "quality engineer",
    "industrial engineer",
    "validation engineer",
    "verification engineer",
    "design verification",
    "device engineer",
    "applications engineer",
    "application engineer",
    "field engineer",
    "field applications",
    "hvac",
    "welding",
    "plumbing",
    "construction",
    "facilities",
    "physical design",
    "vlsi",
    "controls engineer",
    "safety engineer",
    # Security / IT / enterprise systems (not SWE / AI / ML)
    "security",
    "security engineer",
    "cyber",
    "cybersecurity",
    "infosec",
    "information security",
    "incident response",
    "soc",
    "red team",
    "red teamer",
    "grc",
    "penetration",
    "pentest",
    "threat",
    "vulnerability",
    "network",
    "networking",
    "network engineer",
    "systems administrator",
    "sysadmin",
    "business systems",
    "business intelligence",
    "business analysis",
    "data center",
    "it",
    "power apps",
    "powerapps",
    "power platform",
    "erp",
    "sap",
    "abap",
    "servicenow",
    "salesforce",
    "workday",
    "sharepoint",
    "genesys",
    "windchill",
    "mainframe",
    "cobol",
    # Defense / clearance-gated roles
    "clearance",
    "cleared",
    "ts/sci",
    "top secret",
    "secret",
    "polygraph",
    "poly",
    # QA / test / analyst titles
    "test",
    "testing",
    "sdet",
    "in test",
    "qa",
    "quality assurance",
    "quality",
    "analyst",
    # Systems / firmware / physical-domain "engineer" titles
    "systems engineer",
    "system engineer",
    "systems engineering",
    "firmware",
    "modem",
    "eda",
    "cad",
    "dft",
    "wireless",
    "radar",
    "bioengineer",
    "statistician",
    "physical infrastructure",
    "manufacturing",
    # Assistant / fellow titles are either non-engineering or very senior
    "assistant",
    "fellow",
    # Non-tech
    "clinical",
    "nurse",
    "physician",
    "pharmacist",
    "therapist",
]

# Default target regions. Short tokens (<= 3 chars) match as standalone
# tokens; longer entries match as substrings.
DEFAULT_LOCATIONS: list[str] = [
    "us",
    "usa",
    "u.s.",
    "united states",
    "remote",
    "san francisco",
    "sf",
    "bay area",
    "california",
    "ca",
    "new york",
    "ny",
    "nyc",
    "brooklyn",
    "seattle",
    "wa",
    "washington",
    "bellevue",
    "redmond",
    "washington d.c.",
    "dc",
    "maryland",
    "md",
    "virginia",
    "va",
    "austin",
    "tx",
    "texas",
    "boston",
    "ma",
    "massachusetts",
    "chicago",
    "il",
    "illinois",
    "palo alto",
    "mountain view",
    "menlo park",
    "sunnyvale",
    "santa clara",
    "san jose",
]

# Foreign locations that veto a match unless a concrete US token also matched.
# Matched as whole words so "india" never hits "Indianapolis".
DEFAULT_EXCLUDED_LOCATIONS: list[str] = [
    "canada", "toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo",
    "ontario", "british columbia", "quebec", "alberta",
    "uk", "united kingdom", "london", "england", "scotland", "wales", "edinburgh",
    "ireland", "dublin",
    "germany", "berlin", "munich", "france", "paris", "netherlands", "amsterdam",
    "spain", "madrid", "barcelona", "portugal", "lisbon", "italy", "milan", "rome",
    "poland", "warsaw", "krakow", "sweden", "stockholm", "denmark", "copenhagen",
    "norway", "oslo", "finland", "helsinki", "switzerland", "zurich", "austria", "vienna",
    "belgium", "brussels", "czech", "prague", "hungary", "budapest", "romania", "bucharest",
    "greece", "athens", "turkey", "istanbul", "europe", "emea",
    "israel", "tel aviv", "uae", "dubai", "egypt", "nigeria", "kenya", "south africa",
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "mumbai",
    "delhi", "gurgaon", "gurugram", "noida", "pakistan", "bangladesh",
    "singapore", "japan", "tokyo", "china", "beijing", "shanghai", "shenzhen",
    "hong kong", "taiwan", "taipei", "korea", "seoul", "philippines", "vietnam",
    "indonesia", "malaysia", "thailand", "apac",
    "australia", "sydney", "melbourne", "new zealand", "auckland",
    "brazil", "sao paulo", "mexico", "argentina", "colombia", "chile", "latam",
]

# Employers never notified on any built-in channel. Whole-word match on the
# company name, so "meta" hits "Meta" / "Meta Platforms" but not "Metabase".
DEFAULT_EXCLUDED_COMPANIES: list[str] = [
    "microsoft",
    "uber",
    "meta",
]

# Single-channel fallback (DISCORD_WEBHOOK_URL) uses the full-time defaults.
KEYWORDS: list[str] = _parse_list("KEYWORDS", default=DEFAULT_SWE_FULL_TIME_KEYWORDS)
EXCLUDED_KEYWORDS: list[str] = _parse_list(
    "EXCLUDED_KEYWORDS",
    default=DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS,
)
LOCATIONS: list[str] = _parse_list("LOCATIONS", default=[])
EXCLUDED_LOCATIONS: list[str] = _parse_list(
    "EXCLUDED_LOCATIONS",
    default=DEFAULT_EXCLUDED_LOCATIONS,
)
EXCLUDED_COMPANIES: list[str] = _parse_list(
    "EXCLUDED_COMPANIES",
    default=DEFAULT_EXCLUDED_COMPANIES,
)

# ---------------------------------------------------------------------------
# Rate limiting / run behaviour
# ---------------------------------------------------------------------------

MAX_NOTIFICATIONS_PER_RUN: int = _parse_int("MAX_NOTIFICATIONS_PER_RUN", 25)
# ~110 ATS boards now; 8 concurrent with a 0.5s pause keeps a run under a minute.
SLEEP_BETWEEN_COMPANIES: float = _parse_float("SLEEP_BETWEEN_COMPANIES", 0.5)
# 10s was too short for large Greenhouse boards (Databricks, Figma timed out
# on every run). Payloads are gzip-compressed so 25s is plenty.
REQUEST_TIMEOUT: int = _parse_int("REQUEST_TIMEOUT", 25)
REQUEST_RETRY_ATTEMPTS: int = _parse_int("REQUEST_RETRY_ATTEMPTS", 2)
RECENT_POSTING_MAX_AGE_HOURS: int = _parse_int("RECENT_POSTING_MAX_AGE_HOURS", 24)
ATS_CONCURRENCY: int = _parse_int("ATS_CONCURRENCY", 8)
SEND_NO_NEW_SUMMARY: bool = _parse_bool("SEND_NO_NEW_SUMMARY", False)

# ---------------------------------------------------------------------------
# Jev second-pass judge (typesafe.ai)
#
# Off by default. The regex filters stay the source of truth and every Jev
# failure falls back to them (see jev.UNJUDGED). Three independent switches,
# because they are the phase ladder: shadow -> rank -> enforce, each revertable
# by flipping a GitHub variable with no deploy.
#
# Defaults for the thresholds come from the Phase 0 harness (jev_eval.py),
# measured over 12h/24h/48h scrape windows on 2026-09-17:
#   - 0/28 of regex-accepted golden titles wrongly dropped
#   - explicit new-grad roles filling the 25-cap: 2-3 -> 25 under fit ranking
# Re-run `python jev_eval.py --golden --sweep` before changing them.
# ---------------------------------------------------------------------------

JEV_ENABLED: bool = _parse_bool("JEV_ENABLED", False)     # master kill switch
JEV_RANKING: bool = _parse_bool("JEV_RANKING", False)     # let fit reorder the cap
JEV_ENFORCE: bool = _parse_bool("JEV_ENFORCE", False)     # let verdicts actually drop
# The user's key lives in .env as JEV_API_KEY. The SDK would auto-read
# TYPESAFE_API_KEY, so jev.py passes this explicitly instead.
JEV_API_KEY: str = os.getenv("JEV_API_KEY", "").strip()
# Pinned, not "jev-latest": aliases move between versions and the thresholds
# above are tuned against this one.
JEV_MODEL: str = os.getenv("JEV_MODEL", "").strip() or "jev-1.13.0"
# ~0.25s per call, so 5 concurrent is ~20 rps against a 1200 rpm (20 rps) ceiling.
JEV_CONCURRENCY: int = _parse_int("JEV_CONCURRENCY", 5)
# Shared across all channels in a run, not per channel.
JEV_MAX_CALLS_PER_RUN: int = _parse_int("JEV_MAX_CALLS_PER_RUN", 400)
# The real safety property: a call budget alone still permits
# max_calls x (timeout + retries) of wall clock. Runs are ~100s against a
# 15-minute workflow timeout.
JEV_RUN_DEADLINE_SECONDS: float = _parse_float("JEV_RUN_DEADLINE_SECONDS", 90.0)
JEV_TIMEOUT_SECONDS: float = _parse_float("JEV_TIMEOUT_SECONDS", 8.0)
JEV_MAX_RETRIES: int = _parse_int("JEV_MAX_RETRIES", 1)
# Applied per dimension, never as a min() across answers.
JEV_MIN_CONFIDENCE: float = _parse_float("JEV_MIN_CONFIDENCE", 0.5)
# From the Phase 0 sweep: below 0.17 drops 32 golden titles with zero false drops.
JEV_FIT_FLOOR: float = _parse_float("JEV_FIT_FLOOR", 0.17)
JEV_CLEARANCE_THRESHOLD: float = _parse_float("JEV_CLEARANCE_THRESHOLD", 0.5)
# Consecutive errors before the judge gives up for the rest of the run.
JEV_ERROR_CIRCUIT: int = _parse_int("JEV_ERROR_CIRCUIT", 5)
# Fit is bucketed to this width before sorting so that near-equal fits stay in
# recency order — a raw fit sort would let a marginally better older job
# outrank a fresher one and undo _newest_first.
JEV_FIT_BUCKET: float = _parse_float("JEV_FIT_BUCKET", 0.1)

# Bounded promotion: the only path where Jev may ADMIT a job the keyword filter
# rejected. Runs only when a channel is under its notification cap, judges at most
# JEV_PROMOTE_MAX_CALLS regex-rejected jobs, and requires a positive high-confidence
# match (see jev.should_promote). Promoted jobs are flagged in Discord.
JEV_PROMOTE: bool = _parse_bool("JEV_PROMOTE", False)
JEV_PROMOTE_MIN_FIT: float = _parse_float("JEV_PROMOTE_MIN_FIT", 0.75)
JEV_PROMOTE_MAX_CALLS: int = _parse_int("JEV_PROMOTE_MAX_CALLS", 150)

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

SEEN_JOBS_PATH: str = os.getenv("SEEN_JOBS_PATH", "seen_jobs.json")
SEEN_JOBS_MAX_AGE_DAYS: int = _parse_int("SEEN_JOBS_MAX_AGE_DAYS", 30)

QUEUED_JOBS_PATH: str = os.getenv("QUEUED_JOBS_PATH", "queued_jobs.json")
QUEUED_JOBS_MAX_AGE_HOURS: int = _parse_int("QUEUED_JOBS_MAX_AGE_HOURS", 6)

# ---------------------------------------------------------------------------
# SimplifyJobs-format scrapers (GitHub listings.json)
#
# NOTE: SimplifyJobs/Summer2026-Internships and vanshb03/*2026* were RENAMED
# to the 2027 repos. raw.githubusercontent.com silently serves the old names,
# so listing both fetched the same 12 MB file twice per run. Only canonical
# names are listed here — update the year when a new cycle starts.
# ---------------------------------------------------------------------------

SIMPLIFY_URLS: list[str] = _parse_list(
    "SIMPLIFY_URLS",
    default=[
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/.github/scripts/listings.json",
    ],
)

# ---------------------------------------------------------------------------
# SpeedyApply scraper (markdown tables in GitHub READMEs)
# ---------------------------------------------------------------------------

SPEEDYAPPLY_URLS: list[str] = _parse_list(
    "SPEEDYAPPLY_URLS",
    default=[
        "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
        "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
    ],
)

# ---------------------------------------------------------------------------
# JobRight scraper (markdown tables in GitHub READMEs)
# jobright-ai is the data behind intern-list.com; these repos are the
# machine-readable feed. Updated several times per day.
# ---------------------------------------------------------------------------

JOBRIGHT_URLS: list[str] = _parse_list(
    "JOBRIGHT_URLS",
    default=[
        "https://raw.githubusercontent.com/jobright-ai/2026-Product-Management-Internship/master/README.md",
        "https://raw.githubusercontent.com/jobright-ai/2026-Product-Management-New-Grad/master/README.md",
        "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/master/README.md",
    ],
)

# ---------------------------------------------------------------------------
# zapplyjobs scraper (markdown table, regenerated every ~15 minutes)
# ---------------------------------------------------------------------------

ZAPPLY_URLS: list[str] = _parse_list(
    "ZAPPLY_URLS",
    default=[
        "https://raw.githubusercontent.com/zapplyjobs/New-Grad-Jobs-2027/main/README.md",
    ],
)

# ---------------------------------------------------------------------------
# JSON new-grad trackers (field specs live in scrapers/json_sources.py)
#   applyguy    — refreshed every ~15 min, links straight to the ATS posting
#   gradtracker — pulls Workday/Eightfold/Amazon boards directly (NVIDIA,
#                 Qualcomm, Microsoft, Jane Street, ...)
# Format: "name=url,name=url". Set to "none" to disable.
# ---------------------------------------------------------------------------

_DEFAULT_JSON_SOURCES: dict[str, str] = {
    "applyguy": "https://raw.githubusercontent.com/ApplyGuy/2027-New-Grad-Jobs/main/data/new-grad-jobs.json",
    "gradtracker": "https://raw.githubusercontent.com/harrycodingnow/new-grad-2027-tracker/main/data/active_jobs.json",
}


def _parse_named_urls(env_var: str, default: dict[str, str]) -> dict[str, str]:
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return dict(default)
    if raw.lower() == "none":
        return {}
    parsed: dict[str, str] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        name, url = item.split("=", 1)
        if name.strip() and url.strip():
            parsed[name.strip()] = url.strip()
    return parsed


JSON_SOURCE_URLS: dict[str, str] = _parse_named_urls("JSON_SOURCE_URLS", _DEFAULT_JSON_SOURCES)

# ---------------------------------------------------------------------------
# Direct big-tech feeds (see scrapers/bigtech.py)
# ---------------------------------------------------------------------------

AMAZON_QUERIES: list[str] = _parse_list(
    "AMAZON_QUERIES",
    default=[
        "software development engineer",
        "software engineer",
        "applied scientist",
        "machine learning",
        "data scientist",
        "product manager",
    ],
)

# Workday tenants: "tenant:wdN:site:Display Name" entries, comma-separated.
_DEFAULT_WORKDAY_TENANTS: list[str] = [
    "nvidia:wd5:NVIDIAExternalCareerSite:NVIDIA",
    "salesforce:wd12:External_Career_Site:Salesforce",
    "adobe:wd5:external_experienced:Adobe",
    "capitalone:wd12:Capital_One:Capital One",
    "intel:wd1:External:Intel",
]


def _parse_workday_tenants(env_var: str, default: list[str]) -> list[dict[str, str]]:
    raw = os.getenv(env_var, "").strip()
    entries = default if not raw else [item.strip() for item in raw.split(",") if item.strip()]
    if raw.lower() == "none":
        return []
    tenants: list[dict[str, str]] = []
    for entry in entries:
        parts = entry.split(":")
        if len(parts) < 3:
            print(f"[WARN] Ignoring malformed WORKDAY_TENANTS entry: {entry!r}")
            continue
        tenant, wd, site = parts[0].strip(), parts[1].strip(), parts[2].strip()
        label = parts[3].strip() if len(parts) > 3 and parts[3].strip() else tenant.title()
        tenants.append({"tenant": tenant, "wd": wd, "site": site, "label": label})
    return tenants


WORKDAY_TENANTS: list[dict[str, str]] = _parse_workday_tenants("WORKDAY_TENANTS", _DEFAULT_WORKDAY_TENANTS)
WORKDAY_QUERIES: list[str] = _parse_list(
    "WORKDAY_QUERIES",
    default=["software engineer", "new grad", "machine learning", "product manager"],
)
WORKDAY_PAGES: int = _parse_int("WORKDAY_PAGES", 2)

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

    1. PM_WEBHOOK_URL / FULL_TIME_WEBHOOK_URL — built-in channels
    2. CHANNELS_JSON env var                  — raw JSON string (adds/overrides)
    3. channels.json file                     — local development
    4. Single-channel fallback                — uses DISCORD_WEBHOOK_URL + filters

    Each channel gets its own webhook URL, keyword list, and exclusion list so
    the scraper can fan out one scrape run to many Discord channels.
    """
    channels: list[ChannelConfig] = []

    def _load_default_channels_from_env() -> list[ChannelConfig]:
        default_channels: list[ChannelConfig] = []
        pm_webhook_url = _normalize_webhook_url(os.getenv("PM_WEBHOOK_URL", ""))
        full_time_webhook_url = _normalize_webhook_url(os.getenv("FULL_TIME_WEBHOOK_URL", ""))

        if os.getenv("SWE_WEBHOOK_URL", "").strip():
            print(
                "[WARN] SWE_WEBHOOK_URL is set but the internship channel was retired; "
                "ignoring it. Delete the secret to silence this warning."
            )

        if pm_webhook_url:
            default_channels.append(
                ChannelConfig(
                    name="pm-jobs",
                    webhook_url=pm_webhook_url,
                    keywords=list(DEFAULT_PM_KEYWORDS),
                    excluded_keywords=list(DEFAULT_PM_EXCLUDED_KEYWORDS),
                    locations=list(DEFAULT_LOCATIONS),
                    excluded_locations=list(DEFAULT_EXCLUDED_LOCATIONS),
                    excluded_companies=list(DEFAULT_EXCLUDED_COMPANIES),
                )
            )
        if full_time_webhook_url:
            default_channels.append(
                ChannelConfig(
                    name="swe-ai-full-time",
                    webhook_url=full_time_webhook_url,
                    keywords=list(DEFAULT_SWE_FULL_TIME_KEYWORDS),
                    excluded_keywords=list(DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS),
                    locations=list(DEFAULT_LOCATIONS),
                    excluded_locations=list(DEFAULT_EXCLUDED_LOCATIONS),
                    excluded_companies=list(DEFAULT_EXCLUDED_COMPANIES),
                )
            )
        return default_channels

    def _clean_list(values: object, channel_name: str, field_name: str, source: str) -> list[str]:
        if not isinstance(values, list):
            raise ValueError(f"{source} channel '{channel_name}' {field_name} must be a list")
        return [str(item).strip() for item in values if str(item).strip()]

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

            channel.keywords = _clean_list(channel.keywords, channel.name, "keywords", source)
            channel.excluded_keywords = _clean_list(
                channel.excluded_keywords, channel.name, "excluded_keywords", source
            )
            channel.locations = _clean_list(channel.locations, channel.name, "locations", source)
            channel.excluded_locations = _clean_list(
                channel.excluded_locations, channel.name, "excluded_locations", source
            )
            channel.excluded_companies = _clean_list(
                channel.excluded_companies, channel.name, "excluded_companies", source
            )
            if not channel.keywords:
                raise ValueError(f"{source} channel '{channel.name}' must include at least one keyword")

            parsed.append(channel)

        names = [ch.name for ch in parsed]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"{source} has duplicate channel name(s): {duplicates}")

        return parsed

    def _merge_channels(
        base: list[ChannelConfig],
        extra: list[ChannelConfig],
        source: str,
    ) -> list[ChannelConfig]:
        if not extra:
            return base

        merged_by_name = {channel.name: channel for channel in base}
        merged_order = [channel.name for channel in base]
        overridden: list[str] = []
        added: list[str] = []

        for channel in extra:
            if channel.name in merged_by_name:
                overridden.append(channel.name)
            else:
                added.append(channel.name)
                merged_order.append(channel.name)
            merged_by_name[channel.name] = channel

        print(f"[INFO] Loaded {len(extra)} channel(s) from {source}")
        if overridden:
            print(f"[INFO] {source} overrides channel(s): {overridden}")
        if added:
            print(f"[INFO] {source} adds channel(s): {added}")

        return [merged_by_name[name] for name in merged_order]

    # --- 1. Built-in channels from webhook env vars ---
    channels = _load_default_channels_from_env()
    if channels:
        print(f"[INFO] Loaded {len(channels)} channel(s) from PM_WEBHOOK_URL/FULL_TIME_WEBHOOK_URL")

    # --- 2. CHANNELS_JSON env var ---
    raw = os.getenv("CHANNELS_JSON", "").strip()
    if raw:
        data = _load_json_secret(raw, "CHANNELS_JSON env var")
        parsed_channels = _coerce_channels(data, "CHANNELS_JSON")
        channels = _merge_channels(channels, parsed_channels, "CHANNELS_JSON env var")

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
                "  1. PM_WEBHOOK_URL and/or FULL_TIME_WEBHOOK_URL env vars\n"
                "  2. CHANNELS_JSON env var  (raw JSON string)\n"
                "  3. channels.json file\n"
                "  4. DISCORD_WEBHOOK_URL env var  (single-channel mode)"
            )
        channels = [
            ChannelConfig(
                name="default",
                webhook_url=DISCORD_WEBHOOK_URL,
                keywords=KEYWORDS,
                excluded_keywords=EXCLUDED_KEYWORDS,
                locations=LOCATIONS,
                excluded_locations=EXCLUDED_LOCATIONS,
                excluded_companies=EXCLUDED_COMPANIES,
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
