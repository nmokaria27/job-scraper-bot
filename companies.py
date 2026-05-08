# Master company list — add or remove slugs here to control which companies are scraped.
#
# How to find a company's slug:
#   - Greenhouse: https://boards.greenhouse.io/<slug>/jobs  → verify at boards-api.greenhouse.io/v1/boards/<slug>/jobs
#   - Lever:      https://jobs.lever.co/<slug>              → verify at api.lever.co/v0/postings/<slug>?mode=json
#   - Ashby:      https://jobs.ashbyhq.com/<slug>           → verify at api.ashbyhq.com/posting-api/job-board/<slug>
#
# To disable a company without deleting it, comment out its slug.
# All slugs in this file were verified as of 2026-05-07.

COMPANIES: dict[str, list[str]] = {
    # -------------------------------------------------------------------------
    # Bulk scrapers — called ONCE per run (not per-company).
    # Keep the list empty; it's a marker so the orchestrator knows to run them.
    # -------------------------------------------------------------------------
    "simplify": [],    # SimplifyJobs Summer 2026 internships (single GitHub JSON)
    "hackernews": [],  # HN "Ask HN: Who is Hiring?" monthly thread

    # -------------------------------------------------------------------------
    # Greenhouse ATS — verified working slugs (as of 2026-05-07)
    # Many big-name companies have migrated to Workday/custom ATS.
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

        # Product / infra
        "airbnb",
        "reddit",
        "pinterest",
        "discord",
        "cloudflare",
        "datadog",
        "amplitude",
        "vercel",
        "lyft",

        # Previously in list but currently 404 — moved to other ATS:
        # "notion",          # moved to Workday
        # "openai",          # uses custom careers page
        # "ramp",            # uses custom careers page
        # "confluent",       # moved to Workday
        # "retool",          # moved to Workday
        # "scale",           # uses scale.com/careers (custom)
        # "weights-biases",  # uses their own ATS
        # "huggingface",     # uses their own careers page
        # "cohere",          # uses their own careers page
    ],

    # -------------------------------------------------------------------------
    # Lever ATS — verified working slugs (as of 2026-05-07)
    # Most big companies have migrated off Lever; list is intentionally small.
    # -------------------------------------------------------------------------
    "lever": [
        "anyscale",   # 1 job (verified)

        # Previously in list but currently 404:
        # "netflix",      # migrated off Lever (returns 0 jobs / 404)
        # "twitter",      # 404
        # "square",       # 404 (now Block)
        # "lyft",         # now on Greenhouse
        # "doordash",     # 404
        # "replit",       # 404
        # "modal",        # 404
        # "together-ai",  # 404
    ],

    # -------------------------------------------------------------------------
    # Ashby ATS — verified Ashby-hosted boards.
    # To add a working company:
    #   1. Visit https://jobs.ashbyhq.com/<slug>
    #   2. Verify the API: curl -s "https://api.ashbyhq.com/posting-api/job-board/<slug>"
    #      should return {"jobs":[...]} not {"error":"Not Found"}
    # -------------------------------------------------------------------------
    "ashby": [
        "openai",
        "perplexity",
        "Ashby",

        # Previously checked slugs:
        # "mistral",              # 404
        # "anysphere",            # 404 (Cursor)
        # "cognition",            # 404
        # "imbue",                # 404
        # "adept",                # 404
        # "covariant",            # 404
        # "physical-intelligence",# 404
        # "genesis",              # 404
        # "exa",                  # 404
        # "groq",                 # 404
        # "fireworks-ai",         # 404
        # "together-ai",          # 404
    ],
}
