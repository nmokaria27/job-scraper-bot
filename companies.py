import json
import os
import re
from copy import deepcopy

# Master company list — add or remove slugs here to control which companies are scraped.
#
# How to find a company's slug:
#   - Greenhouse: https://boards.greenhouse.io/<slug>/jobs  → verify at boards-api.greenhouse.io/v1/boards/<slug>/jobs
#   - Lever:      https://jobs.lever.co/<slug>              → verify at api.lever.co/v0/postings/<slug>?mode=json
#   - Ashby:      https://jobs.ashbyhq.com/<slug>           → verify at api.ashbyhq.com/posting-api/job-board/<slug>
#
# To disable a company without deleting it, comment out its slug.
# All slugs in this file were verified as of 2026-09-04.

COMPANIES: dict[str, list[str]] = {
    # -------------------------------------------------------------------------
    # Bulk scrapers — called ONCE per run (not per-company).
    # Keep the list empty; it's a marker so the orchestrator knows to run them.
    # -------------------------------------------------------------------------
    "simplify": [],      # SimplifyJobs + vanshb03 repos (GitHub JSON)
    "hackernews": [],    # HN "Ask HN: Who is Hiring?" monthly thread
    "speedyapply": [],   # speedyapply SWE/AI repos (markdown tables)
    "jobright": [],      # jobright-ai PM + SWE new-grad repos (markdown tables)
    "zapply": [],        # zapplyjobs/New-Grad-Jobs-2027 (markdown table, ~15 min refresh)
    "jsonsource": [],    # ApplyGuy + new-grad-2027-tracker JSON feeds
    "amazon": [],        # amazon.jobs search.json
    "workday": [],       # Workday CXS tenants (NVIDIA, Salesforce, Adobe, Capital One, Intel)

    # -------------------------------------------------------------------------
    # Greenhouse ATS — every slug below returned jobs on 2026-09-04.
    # Verify a slug with: curl -s https://boards-api.greenhouse.io/v1/boards/<slug>/jobs | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))"
    # -------------------------------------------------------------------------
    "greenhouse": [
        # Core tech / fintech
        "stripe",
        "figma",
        "anthropic",
        "robinhood",
        "brex",
        "databricks",
        "deepmind",
        "coinbase",
        "affirm",
        "block",
        "chime",
        "sofi",
        "upstart",
        "carta",
        "gusto",

        # Consumer / product / infra
        "airbnb",
        "reddit",
        "pinterest",
        "discord",
        "cloudflare",
        "datadog",
        "amplitude",
        "vercel",
        "lyft",
        "doordashusa",
        "instacart",
        "roblox",
        "dropbox",
        "twilio",
        "okta",
        "mongodb",
        "elastic",
        "samsara",
        "verkada",
        "duolingo",
        "linkedin",
        "asana",
        "gitlab",
        "klaviyo",
        "rubrik",
        "toast",
        "twitch",
        "zscaler",
        "squarespace",
        "nextdoor",
        "oscar",
        "flexport",
        "checkr",
        "cockroachlabs",

        # AI labs / AI infra
        "xai",
        "scaleai",
        "togetherai",
        "gleanwork",
        "snorkelai",
        "assemblyai",
        "sambanovasystems",
        "tenstorrent",
        "lightmatter",

        # Autonomy / robotics
        "waymo",
        "nuro",
        "wayve",

        # Quant / trading
        "janestreet",
        "jumptrading",
        "imc",
        "optiverus",
        "drweng",
        "akunacapital",
        "point72",

        # Previously in list but currently 404 — moved to other ATS:
        # "notion",          # now on Ashby (see below)
        # "openai",          # now on Ashby (see below)
        # "ramp",            # now on Ashby (see below)
        # "confluent",       # moved to Workday
        # "retool",          # moved to Workday
        # "weights-biases",  # uses their own ATS
        # "huggingface",     # Workable
        # "cohere",          # now on Ashby (see below)

        # --- added 2026-09-17: verified live, job counts at time of check ---
        "airtable",                # 16 jobs
        "andurilindustries",       # 2349 jobs
        "coreweave",               # 295 jobs
        "databento",               # 14 jobs
        "fastly",                  # 43 jobs
        "figure",                  # 11 jobs
        "fivetran",                # 192 jobs
        "grafanalabs",             # 117 jobs
        "imbue",                   # 1 jobs
        "invisibletech",           # 18 jobs
        "knock",                   # 3 jobs
        "labelbox",                # 10 jobs
        "lucidmotors",             # 396 jobs
        "mercury",                 # 63 jobs
        "mixpanel",                # 91 jobs
        "motional",                # 72 jobs
        "netlify",                 # 1 jobs
        "planetscale",             # 13 jobs
        "roku",                    # 254 jobs
        "singlestore",             # 40 jobs
        "webflow",                 # 27 jobs
        "yugabyte",                # 14 jobs
    ],

    # -------------------------------------------------------------------------
    # Lever ATS — verified 2026-09-04.
    # -------------------------------------------------------------------------
    "lever": [
        "anyscale",
        "palantir",
        "zoox",
        "spotify",
        "waabi",
        "wealthfront",

        # Previously in list but currently 404:
        # "netflix",      # Eightfold (explore.jobs.netflix.net)
        # "twitter",      # 404
        # "square",       # now "block" on Greenhouse
        # "lyft",         # now on Greenhouse
        # "doordash",     # now "doordashusa" on Greenhouse
        # "replit",       # now on Ashby
        # "modal",        # now on Ashby
        # "together-ai",  # now "togetherai" on Greenhouse

        # --- added 2026-09-17: verified live, job counts at time of check ---
        "neon",                    # 12 jobs
        "zilliz",                  # 12 jobs
    ],

    # -------------------------------------------------------------------------
    # Ashby ATS — verified 2026-09-04.
    # To add a working company:
    #   1. Visit https://jobs.ashbyhq.com/<slug>
    #   2. Verify the API: curl -s "https://api.ashbyhq.com/posting-api/job-board/<slug>"
    #      should return {"jobs":[...]} not {"error":"Not Found"}
    # -------------------------------------------------------------------------
    "ashby": [
        # AI labs / AI products
        "openai",
        "perplexity",
        "cursor",
        "cohere",
        "cerebras",
        "harvey",
        "sierra",
        "decagon",
        "elevenlabs",
        "etched",
        "cognition",
        "deepgram",
        "fireworks",
        "suno",
        "midjourney",
        "poolside",
        "character",
        "runway",

        # Dev tools / infra
        "Ashby",
        "notion",
        "ramp",
        "plaid",
        "snowflake",
        "replit",
        "supabase",
        "temporal",
        "modal",
        "linear",
        "sentry",
        "render",
        "railway",
        "benchling",

        # Defense / autonomy / consumer
        "shield-ai",
        "applied",        # Applied Intuition
        "handshake",
        "substack",
        "thumbtack",
        "miro",

        # Previously checked slugs (404 on 2026-09-04):
        # "mistral", "anysphere" (use "cursor"), "imbue", "adept", "covariant",
        # "physical-intelligence", "genesis", "exa", "groq", "fireworks-ai"
        # (use "fireworks"), "together-ai" (Greenhouse "togetherai")

        # --- added 2026-09-17: verified live, job counts at time of check ---
        "airbyte",                 # 13 jobs
        "astronomer",              # 21 jobs
        "baseten",                 # 99 jobs
        "clerk",                   # 1 jobs
        "clickhouse",              # 200 jobs
        "confluent",               # 21 jobs
        "docker",                  # 64 jobs
        "inngest",                 # 1 jobs
        "langchain",               # 107 jobs
        "llamaindex",              # 11 jobs
        "lumaai",                  # 42 jobs
        "materialize",             # 2 jobs
        "nousresearch",            # 10 jobs
        "physicalintelligence",    # 36 jobs
        "pinecone",                # 7 jobs
        "posthog",                 # 9 jobs
        "prefect",                 # 8 jobs
        "reflectionai",            # 56 jobs
        "resend",                  # 9 jobs
        "skydio",                  # 133 jobs
        "trychroma",               # 1 jobs
        "weaviate",                # 2 jobs
        "workos",                  # 27 jobs
        "worldlabs",               # 8 jobs
        "zapier",                  # 7 jobs
        "zed",                     # 1 jobs
    ],
}


ATS_PLATFORMS: tuple[str, ...] = ("greenhouse", "lever", "ashby")


def _load_discovered_companies(path: str) -> dict[str, list[str]]:
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] Failed to load discovered companies from '{path}': {e}")
        return {}

    if not isinstance(raw, dict):
        print(f"[WARN] Discovered companies file '{path}' must be a JSON object")
        return {}

    discovered: dict[str, list[str]] = {}
    for platform in ATS_PLATFORMS:
        values = raw.get(platform, [])
        if not isinstance(values, list):
            print(f"[WARN] Discovered platform '{platform}' in '{path}' must be a list")
            continue
        # Sanitize slugs by removing control characters to prevent log injection
        discovered[platform] = [
            re.sub(r"[\r\n]", "", s)
            for s in (str(slug).strip() for slug in values)
            if s
        ]
    return discovered


def get_companies() -> dict[str, list[str]]:
    """
    Return static COMPANIES merged with optional discovered companies JSON.
    Controlled by:
      - INCLUDE_DISCOVERED_COMPANIES (default: true)
      - DISCOVERED_COMPANIES_PATH (default: discovered_companies.json)
    """
    merged = deepcopy(COMPANIES)
    include_discovered = os.getenv("INCLUDE_DISCOVERED_COMPANIES", "true").strip().lower()
    if include_discovered not in {"1", "true", "yes", "y", "on"}:
        return merged

    path = os.getenv("DISCOVERED_COMPANIES_PATH", "discovered_companies.json").strip()
    if not path:
        return merged

    discovered = _load_discovered_companies(path)
    if not discovered:
        return merged

    for platform in ATS_PLATFORMS:
        base_slugs = merged.get(platform, [])
        new_slugs = discovered.get(platform, [])
        seen = set(base_slugs)
        for slug in new_slugs:
            if slug not in seen:
                base_slugs.append(slug)
                seen.add(slug)
        merged[platform] = base_slugs

    added_total = sum(
        max(0, len(merged.get(platform, [])) - len(COMPANIES.get(platform, [])))
        for platform in ATS_PLATFORMS
    )
    if added_total:
        print(f"[INFO] Added {added_total} discovered ATS slug(s) from '{path}'")

    return merged
