"""
Phase 0 evaluation harness for Jev (typesafe.ai).

Standalone: nothing in the run path imports this. It answers one question —
does Jev classify job titles better than the regex filter in scrapers/base.py?

Two corpora:

  --golden   The hand-labelled titles already embedded in
             tests/test_filters_and_dedupe.py, extracted via AST so the test
             file stays the single source of truth. Regex was tuned to pass
             this set, so treat it as a floor, not a benchmark.

  --live     A real scrape. Every title the regex currently ACCEPTS is sent to
             Jev. This is where the actual noise lives.

Both `fit_score` (Score, bucketed + confidence) and `fit_noul` (Noul, smooth
0-1) are asked in the same call so Phase 0 can decide which makes the better
ranking signal. Asking both is nearly free: questions in one request are
evaluated in parallel against the state.

Usage:
    python jev_eval.py --golden
    python jev_eval.py --golden --sweep
    python jev_eval.py --live --hours 24 --limit 200
    python jev_eval.py --live --channel swe-ai-full-time
"""

import argparse
import ast
import asyncio
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass
from unittest.mock import patch

from dotenv import load_dotenv

import config
from main import _parse_dt
from scrapers.greenhouse import GreenhouseScraper

load_dotenv()

GOLDEN_PATH = "tests/test_filters_and_dedupe.py"

# Test classes whose accept/reject titles map to a channel profile.
_GOLDEN_CLASS_PROFILES = {
    "FullTimeChannelFilterTests": "swe",
    "PmChannelFilterTests": "pm",
}

# Constant company/location for golden titles, so the eval isolates the title.
GOLDEN_COMPANY = "Example Co"
GOLDEN_LOCATION = "San Francisco, CA"

# Role families that disqualify a posting, per profile.
_REJECT_FAMILIES = {
    "swe": {"other_engineering", "enterprise_or_ops", "it_security_ops",
            "qa_test", "gtm_or_customer_facing", "non_technical"},
    # NOT program_or_project_management: CLAUDE.md defines the pm channel as
    # "entry-level PM/APM/TPM", so technical program managers are in scope.
    "pm": {"product_marketing", "retail_or_physical_product", "engineering"},
}
# mid_level belongs here: "Software Engineer II" / "L3" / "Engineer 2" are exactly the
# titles the bare-token hacks in config._SENIORITY_EXCLUSIONS exist to catch.
_REJECT_SENIORITY = {"internship_or_coop", "mid_level", "senior_or_above"}
_REJECT_EMPLOYMENT = {"internship_or_coop", "contract_or_temp", "part_time_or_hourly"}

# From the Phase 0 sweep: fit_noul < 0.17 drops 32 golden titles with zero false drops.
DEFAULT_FIT_FLOOR = 0.17


# ---------------------------------------------------------------------------
# Question profiles (Phase 1's jev_questions.py will ship the winning variant)
# ---------------------------------------------------------------------------

def _build_profiles() -> dict:
    from typesafe_sdk import Choice, Noul, Score

    seniority = Choice(
        instructions=(
            "What experience level does this job title indicate? Answer 'unspecified' when "
            "the title names no level at all. A roman numeral or digit after the role name "
            "(II, 2, III, L4) indicates 'mid_level'. 'I' or '1' indicates 'new_grad_or_entry'. "
            "'Member of Technical Staff' is a standard individual-contributor title at AI labs "
            "and carries NO level signal on its own: answer 'unspecified' unless the title also "
            "says Senior, Staff or Principal."
        ),
        criteria={
            "internship_or_coop": "Internship, co-op, or apprenticeship",
            "new_grad_or_entry": "New graduate, entry level, university graduate, 0-2 years",
            "mid_level": "Roughly 2-5 years of experience",
            "senior_or_above": "Senior, Staff, Principal, Lead, Manager, or L4 and above",
            "unspecified": "The title gives no level signal",
        },
    )
    employment = Choice(
        instructions=(
            "What employment type does this title indicate? Answer 'unspecified' when the "
            "title does not say."
        ),
        criteria={
            "full_time": "A permanent full-time role",
            "internship_or_coop": "Internship or co-op",
            "contract_or_temp": "Contract, temporary, or seasonal",
            "part_time_or_hourly": "Part-time or hourly",
            "unspecified": "The title does not say",
        },
    )

    clearance = Noul(
        instructions=(
            "The job title indicates the role requires a US government security clearance "
            "or is restricted to US persons / cleared personnel. Markers include: Secret, "
            "Top Secret, TS/SCI, Polygraph, Active Clearance, 'must be a US citizen', ITAR. "
            "A commercial compliance mention (FedRAMP, SOC 2) or a public-sector customer "
            "without a clearance or citizenship requirement is NOT a clearance role."
        )
    )

    swe_fit_text = (
        "How well does this posting match a candidate seeking a US-based, full-time, "
        "entry-level or new-graduate software engineering, AI, ML or data role? "
        "Judge only from the title, company and location given."
    )
    pm_fit_text = (
        "How well does this posting match a candidate seeking a US-based, full-time, "
        "entry-level or new-graduate product management role (PM, APM, product owner)? "
        "Judge only from the title, company and location given."
    )
    fit_levels = [
        "Wrong role family, or clearly too senior",
        "Right family but wrong level, or unclear",
        "Plausible entry-level role in the right family",
        "Explicitly a new-grad or entry-level role in the right family",
    ]

    swe = {
        "role_family": Choice(
            instructions="Judging only from the job title, which job family is this posting?",
            criteria={
                "software_engineering": (
                    "Building a company's own software product: backend, frontend, "
                    "full-stack, mobile, distributed systems, compilers, the platform "
                    "a product runs on, a forward deployed engineer who writes production "
                    "code at customer sites, or embedded/firmware engineering whose main "
                    "deliverable is software running on a device"
                ),
                "ml_ai_research": "ML, AI, research engineer or scientist, applied scientist",
                "data": "Data engineer, data scientist, analytics engineer",
                "enterprise_or_ops": (
                    "Configuring, administering or operating software rather than building a "
                    "product: Salesforce, SAP, ABAP, ServiceNow, Workday, Oracle, Sharepoint, "
                    "DevOps, SRE, cloud operations, release engineering, internal business "
                    "applications, or IT-department software"
                ),
                "other_engineering": (
                    "Roles whose main deliverable is physical or circuit-level, not "
                    "software: hardware, mechanical, electrical, aerospace, civil, chemical, "
                    "bioengineering, silicon/ASIC/GPU hardware, manufacturing, test/validation "
                    "or EDA/CAD engineering"
                ),
                "qa_test": (
                    "Quality assurance, software test, test automation, SDET, or "
                    "validation engineering — the deliverable is verifying software "
                    "rather than building the product"
                ),
                "it_security_ops": (
                    "IT support, help desk, sysadmin, information security, security "
                    "operations, GRC, red teaming, or clearance-gated sustainment work"
                ),
                "gtm_or_customer_facing": (
                    "Roles that sell or explain the product rather than build it: sales "
                    "engineer, pre-sales solutions architect, support engineer, developer "
                    "advocate, technical writer, consultant"
                ),
                "non_technical": "Marketing, recruiting, finance, operations",
                "other": None,
            },
        ),
        "seniority": seniority,
        "employment_type": employment,
        "requires_clearance": clearance,
        "fit_score": Score(instructions=swe_fit_text, criteria=fit_levels),
        "fit_noul": Noul(instructions=swe_fit_text),
    }

    pm = {
        "role_family": Choice(
            instructions="Judging only from the job title, which job family is this posting?",
            criteria={
                "product_management": "Product manager, product owner, APM, TPM",
                "program_or_project_management": (
                    "Technical program manager, project manager, or delivery manager that is "
                    "not product management"
                ),
                "product_marketing": "Product marketing or growth marketing",
                "product_ops_analytics": "Product operations or product analyst",
                "retail_or_physical_product": (
                    "Merchandising, store, apparel, or manufactured-goods product roles"
                ),
                "engineering": "Software or hardware engineering",
                "other": None,
            },
        ),
        "seniority": seniority,
        "employment_type": employment,
        "requires_clearance": clearance,
        "fit_score": Score(instructions=pm_fit_text, criteria=fit_levels),
        "fit_noul": Noul(instructions=pm_fit_text),
    }

    return {"swe": swe, "pm": pm}


@dataclass
class Judgement:
    title: str
    company: str
    location: str
    profile: str
    expected_keep: bool          # golden: ground truth. live: always True (regex accepted it).
    jev_keep: bool | None        # None when the call failed
    role_family: str | None
    seniority: str | None
    employment_type: str | None
    confidence: float | None     # min across the three Choice answers
    fit_score: float | None      # 0.0-3.0
    fit_noul: float | None       # 0.0-1.0
    requires_clearance: float | None = None   # 0.0-1.0
    reason: str = ""          # why Jev dropped it
    input_tokens: int | None = None
    error: str | None = None


def _decide(fam, sen, emp, clearance, fit_noul, profile,
            fit_floor: float, min_confidence: float) -> tuple[bool, str]:
    """The keep/drop rule Phase 1 will ship. Returns (keep, reason).

    Confidence is applied PER DIMENSION, not as a min() across all of them. A
    confident "this is a program manager" must still be able to reject even when
    the employment-type answer happens to be a coin flip — taking the minimum let
    a single uncertain answer veto every other confident rejection.
    """
    if fam.choice in _REJECT_FAMILIES.get(profile, set()) and fam.confidence >= min_confidence:
        return False, f"family={fam.choice}"
    if sen.choice in _REJECT_SENIORITY and sen.confidence >= min_confidence:
        return False, f"seniority={sen.choice}"
    if emp.choice in _REJECT_EMPLOYMENT and emp.confidence >= min_confidence:
        return False, f"employment={emp.choice}"
    if clearance is not None and clearance >= 0.5:
        return False, "clearance"
    if fit_noul is not None and fit_noul < fit_floor:
        return False, f"fit<{fit_floor:.2f}"
    return True, ""


def _build_state(title: str, company: str, location: str, platform: str) -> dict:
    """Dict, not prose: keeps attacker-controlled title text in a labelled value slot.
    Deliberately omits url (no signal, more injection surface) and posted_at
    (Jev reads dates as text; recency belongs to filter_recent_jobs)."""
    return {
        "job_title": " ".join((title or "").split())[:200],
        "company": " ".join((company or "").split())[:80],
        "location": " ".join((location or "").split())[:120],
        "source": platform,
    }


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def load_golden(path: str = GOLDEN_PATH) -> list[tuple[str, str, bool]]:
    """Extract (title, profile, expected_keep) from the test file via AST.

    The expected label IS the method name: test_accepts_* -> keep,
    test_rejects_* -> drop. Parsing rather than copying keeps one source of
    truth, so titles added to the tests show up here automatically.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    out: list[tuple[str, str, bool]] = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        profile = _GOLDEN_CLASS_PROFILES.get(cls.name)
        if not profile:
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name.startswith("test_accepts"):
                expected = True
            elif fn.name.startswith("test_rejects"):
                expected = False
            else:
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
                    for elt in node.iter.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            out.append((elt.value, profile, expected))
    return out


def save_corpus(per_profile: dict, path: str) -> None:
    """Persist the regex-accepted titles so re-judging needs no fresh scrape."""
    import json
    payload = {
        profile: [{"title": j.title, "company": j.company, "location": j.location,
                   "platform": j.platform, "posted_at": j.posted_at} for j in jobs]
        for profile, jobs in per_profile.items()
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"[INFO] corpus saved to {path} "
          f"({sum(len(v) for v in payload.values())} titles)")


def load_corpus(path: str) -> dict:
    import json
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    total = sum(len(v) for v in payload.values())
    print(f"[INFO] corpus loaded from {path} ({total} titles, no scrape)")
    return payload


async def load_first_pass(hours: int, channel_filter: str | None, limit: int | None) -> dict:
    """Corpus for the FIRST-PASS experiment: everything that survives recency plus the
    company and location filters, with the keyword filter DELIBERATELY skipped.

    Location stays regex because it is geographic fact, not judgement, and Jev is
    documented as weak at that class of question. Company exclusion stays because it
    is a stated user preference, not a classification.

    Each row carries `regex_keyword_ok` so the report can compare Jev's verdict
    against what the keyword filter would have decided.
    """
    import main
    import test_run
    from scrapers.base import company_is_excluded, location_is_allowed

    config.RECENT_POSTING_MAX_AGE_HOURS = hours
    channels = test_run._dry_run_channels()
    if channel_filter:
        channels = [c for c in channels if c.name == channel_filter]
        if not channels:
            sys.exit(f"Unknown channel: {channel_filter}")

    print(f"[INFO] Scraping (window {hours}h)...")
    all_jobs, total = await main.scrape_all_raw()
    print(f"[INFO] Total postings fetched: {total}")
    recent = main.filter_recent_jobs(all_jobs)

    matcher = GreenhouseScraper()
    per_profile: dict[str, list[dict]] = {}
    for ch in channels:
        profile = "pm" if ch.name == "pm-jobs" else "swe"
        survivors = [
            job for job in recent
            if job.title
            and not company_is_excluded(job.company, ch.excluded_companies)
            and location_is_allowed(job.location, ch.locations, ch.excluded_locations)
        ]
        # Dedupe the same way production would, so counts are comparable.
        survivors = main.dedupe_jobs_for_channel(ch.name, survivors)
        if limit:
            survivors = survivors[:limit]

        rows = []
        for job in survivors:
            rows.append({
                "title": job.title, "company": job.company, "location": job.location,
                "platform": job.platform, "posted_at": job.posted_at,
                "regex_keyword_ok": matcher.matches_keywords(
                    job.title, ch.keywords, ch.excluded_keywords),
            })
        kept = sum(1 for r in rows if r["regex_keyword_ok"])
        print(f"[INFO] {ch.name}: {len(rows)} job(s) past company+location; "
              f"regex keywords would accept {kept}, reject {len(rows) - kept}")
        per_profile[profile] = rows
    return per_profile


async def load_live(hours: int, channel_filter: str | None, limit: int | None) -> dict:
    """Scrape for real; return {profile: [Job]} of what each channel's regex ACCEPTS."""
    import main
    import test_run

    config.RECENT_POSTING_MAX_AGE_HOURS = hours
    channels = test_run._dry_run_channels()
    if channel_filter:
        channels = [c for c in channels if c.name == channel_filter]
        if not channels:
            sys.exit(f"Unknown channel: {channel_filter}")

    print(f"[INFO] Scraping (window {hours}h)...")
    all_jobs, total = await main.scrape_all_raw()
    print(f"[INFO] Total postings fetched: {total}")
    recent = main.filter_recent_jobs(all_jobs)

    per_profile: dict[str, list] = {}
    for ch in channels:
        matches = main.dedupe_jobs_for_channel(ch.name, main.filter_for_channel(recent, ch))
        if limit:
            matches = matches[:limit]
        profile = "pm" if ch.name == "pm-jobs" else "swe"
        per_profile[profile] = matches
        print(f"[INFO] {ch.name}: regex accepts {len(matches)} job(s)")
    return per_profile


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

async def judge_all(items, profiles, concurrency, model, min_confidence,
                    fit_floor=DEFAULT_FIT_FLOOR):
    """items: (title, company, location, platform, profile, expected_keep).
    Returns (list[Judgement], stats)."""
    from typesafe_sdk import AsyncTypeSafeClient

    key = os.getenv("JEV_API_KEY", "").strip()
    if not key:
        sys.exit("JEV_API_KEY is not set (expected in .env)")

    sem = asyncio.Semaphore(concurrency)
    total = len(items)
    done = 0
    started = time.monotonic()

    async with AsyncTypeSafeClient(api_key=key, timeout=20.0) as client:

        async def one(title, company, location, platform, profile, expected_keep):
            nonlocal done
            async with sem:
                try:
                    r = await client.system_one(
                        state=_build_state(title, company, location, platform),
                        questions=profiles[profile],
                        model=model,
                    )
                    fam = r.answers["role_family"]
                    sen = r.answers["seniority"]
                    emp = r.answers["employment_type"]
                    conf = min(fam.confidence, sen.confidence, emp.confidence)
                    clearance = r.answers["requires_clearance"].noul
                    fit_noul = r.answers["fit_noul"].noul
                    keep, reason = _decide(fam, sen, emp, clearance, fit_noul,
                                           profile, fit_floor, min_confidence)
                    j = Judgement(
                        title=title, company=company, location=location, profile=profile,
                        expected_keep=expected_keep, jev_keep=keep,
                        role_family=fam.choice, seniority=sen.choice,
                        employment_type=emp.choice, confidence=conf,
                        fit_score=r.answers["fit_score"].score,
                        fit_noul=fit_noul,
                        requires_clearance=clearance,
                        reason=reason,
                        input_tokens=r.usage.input_tokens,
                    )
                except Exception as exc:  # noqa: BLE001 - harness must survive the corpus
                    j = Judgement(
                        title=title, company=company, location=location, profile=profile,
                        expected_keep=expected_keep, jev_keep=None,
                        role_family=None, seniority=None, employment_type=None,
                        confidence=None, fit_score=None, fit_noul=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                done += 1
                if done % 25 == 0 or done == total:
                    print(f"[INFO] judged {done}/{total}", flush=True)
                return j

        results = list(await asyncio.gather(*(one(*it) for it in items)))

    elapsed = time.monotonic() - started
    tokens = sum(j.input_tokens or 0 for j in results)
    errors = sum(1 for j in results if j.error)
    stats = {"calls": total, "tokens": tokens, "seconds": elapsed, "errors": errors}
    return results, stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _trunc(s, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_usage(stats: dict) -> None:
    cost = stats["tokens"] * 0.042 / 1_000_000  # $0.042 per Mtok input, output free
    print(f"\nusage: {stats['calls']} calls, {stats['tokens']:,} input tokens, "
          f"{stats['seconds']:.1f}s wall, {stats['errors']} error(s), ~${cost:.4f}")


def report_golden(judgements: list[Judgement]) -> None:
    ok = [j for j in judgements if j.error is None]

    print("\n" + "=" * 92)
    print("GOLDEN CORPUS — ground-truth labels from tests/test_filters_and_dedupe.py")
    print("=" * 92)

    tp = sum(1 for j in ok if j.expected_keep and j.jev_keep)
    fn = sum(1 for j in ok if j.expected_keep and not j.jev_keep)
    fp = sum(1 for j in ok if not j.expected_keep and j.jev_keep)
    tn = sum(1 for j in ok if not j.expected_keep and not j.jev_keep)

    # THE metric for the shipped design. Jev is a second pass: it only ever sees
    # titles the regex already accepted, so the regex-dropped rows below are
    # decisions Jev will never be asked to make. Scoring them would flatter or
    # punish Jev for nothing. What ships is regex AND Jev, so the only number
    # that can regress the bot is a false drop among the regex accepts.
    accepts = [j for j in ok if j.expected_keep]
    false_drops = [j for j in accepts if not j.jev_keep]
    risk = 100.0 * len(false_drops) / len(accepts) if accepts else 0.0

    print(f"\n>>> SECOND-PASS REGRESSION RISK: {len(false_drops)}/{len(accepts)} "
          f"({risk:.1f}%) of regex-accepted jobs would be wrongly dropped.")
    print("    This is the number that gates Phase 4 (enforcement). Target: 0.")
    print("    Benefit CANNOT be measured here — every golden accept is a good job by")
    print("    construction. Run --live to measure how much junk Jev removes.")

    print(f"\nFull matrix (context only; the bottom row is unreachable in production):")
    print(f"{'':>14}{'jev keep':>12}{'jev drop':>12}")
    print(f"{'regex keep':>14}{tp:>12}{fn:>12}   <- fn = REGRESSION, the only real risk")
    print(f"{'regex drop':>14}{fp:>12}{tn:>12}   <- never reaches Jev in the 2nd-pass design")

    wrong = [j for j in ok if j.jev_keep != j.expected_keep]
    if wrong:
        print(f"\nDISAGREEMENTS ({len(wrong)}) — read these; they decide whether Phase 1 ships.\n")
        print(f"{'label':>6} {'jev':>5} {'conf':>5} {'noul':>5}  {'reason':<22} "
              f"{'family':<22} title")
        print("-" * 92)
        for j in sorted(wrong, key=lambda x: (x.expected_keep, -(x.confidence or 0))):
            print(f"{'KEEP' if j.expected_keep else 'DROP':>6} "
                  f"{'KEEP' if j.jev_keep else 'DROP':>5} "
                  f"{(j.confidence or 0):5.2f} {(j.fit_noul or 0):5.2f}  "
                  f"{_trunc(j.reason, 22):<22} "
                  f"{_trunc(j.role_family, 22):<22} "
                  f"{_trunc(j.title, 34)}")

    for j in (x for x in judgements if x.error):
        print(f"[ERROR] {_trunc(j.title, 50)}: {j.error}")


def report_sweep(judgements: list[Judgement]) -> None:
    """How many golden labels each fit threshold would get right. Picks the defaults."""
    ok = [j for j in judgements if j.error is None]
    if not ok:
        return

    for field, label, hi in (("fit_score", "fit_score (Score 0-3)", 3.0),
                             ("fit_noul", "fit_noul (Noul 0-1)", 1.0)):
        print("\n" + "=" * 92)
        print(f"THRESHOLD SWEEP — {label}")
        print("Drop when value < threshold. Pick the largest threshold with wrongly_dropped == 0.")
        print("=" * 92)
        print(f"{'thresh':>8}{'would_drop':>12}{'wrongly_dropped':>18}{'correctly_dropped':>20}")
        print("-" * 92)
        steps = 13
        for i in range(steps):
            thresh = hi * i / (steps - 1)
            dropped = [j for j in ok if (getattr(j, field) or 0.0) < thresh]
            wrongly = sum(1 for j in dropped if j.expected_keep)
            correctly = sum(1 for j in dropped if not j.expected_keep)
            print(f"{thresh:8.2f}{len(dropped):12}{wrongly:18}{correctly:20}")


def report_live(judgements: list[Judgement], profile: str) -> None:
    ok = [j for j in judgements if j.error is None]

    print("\n" + "=" * 92)
    print(f"LIVE CORPUS [{profile}] — every title the regex currently ACCEPTS")
    print("=" * 92)

    would_drop = [j for j in ok if not j.jev_keep]
    pct = 100.0 * len(would_drop) / len(ok) if ok else 0.0
    print(f"\nJev would DROP {len(would_drop)}/{len(ok)} ({pct:.1f}%) of what regex accepts.")

    if would_drop:
        print("\nWOULD-BE DROPS — the noise claim. Verify by eye before enabling enforcement.\n")
        print(f"{'conf':>5} {'sc':>5} {'noul':>5}  {'family':<24} {'seniority':<18} "
              f"{'company':<18} title")
        print("-" * 92)
        for j in sorted(would_drop, key=lambda x: x.fit_noul or 0):
            print(f"{(j.confidence or 0):5.2f} {(j.fit_score or 0):5.2f} {(j.fit_noul or 0):5.2f}  "
                  f"{_trunc(j.role_family, 24):<24} {_trunc(j.seniority, 18):<18} "
                  f"{_trunc(j.company, 18):<18} {_trunc(j.title, 40)}")

    kept = [j for j in ok if j.jev_keep]
    if kept:
        print(f"\nKEPT, RANKED BY fit_noul ({len(kept)}) — under Phase 3 ranking the top of this")
        print(f"list is what the {config.MAX_NOTIFICATIONS_PER_RUN}-cap posts; the tail is deferred.\n")
        print(f"{'noul':>5} {'sc':>5}  {'family':<24} {'seniority':<18} {'company':<18} title")
        print("-" * 92)
        for j in sorted(kept, key=lambda x: -(x.fit_noul or 0)):
            marker = "  <-- cap" if kept.index(j) == config.MAX_NOTIFICATIONS_PER_RUN else ""
            print(f"{(j.fit_noul or 0):5.2f} {(j.fit_score or 0):5.2f}  "
                  f"{_trunc(j.role_family, 24):<24} {_trunc(j.seniority, 18):<18} "
                  f"{_trunc(j.company, 18):<18} {_trunc(j.title, 40)}{marker}")

    print("\nRole family :", dict(sorted(Counter(j.role_family for j in ok).items(),
                                         key=lambda kv: -kv[1])))
    print("Seniority   :", dict(sorted(Counter(j.seniority for j in ok).items(),
                                       key=lambda kv: -kv[1])))
    print("Employment  :", dict(sorted(Counter(j.employment_type for j in ok).items(),
                                       key=lambda kv: -kv[1])))

    for j in (x for x in judgements if x.error):
        print(f"[ERROR] {_trunc(j.title, 50)}: {j.error}")


def report_ranking(judgements: list[Judgement], rows: list[dict], profile: str) -> None:
    """Head-to-head: which 25 jobs the cap posts under recency (today) vs fit (Phase 3).

    This is the benefit number for ranking. It needs no enforcement and carries no
    risk of dropping anything — it only changes ORDER.
    """
    cap = config.MAX_NOTIFICATIONS_PER_RUN
    ok = [j for j in judgements if j.error is None]
    if len(ok) <= cap:
        print(f"\n[{profile}] only {len(ok)} job(s) vs a cap of {cap} — "
              f"ranking changes nothing on this run. Re-run with a wider --hours.")
        return

    # Rank through the SHIPPED code path (main._rank_for_notification with real
    # jev.Verdict objects) rather than a local sort, so this report can never
    # drift from what production actually does.
    import jev
    import main as _main
    from scrapers.base import Job as _Job

    posted = {r["title"]: r.get("posted_at", "Unknown") for r in rows}

    fake_jobs, verdicts = [], []
    for j in ok:
        fake_jobs.append(_Job(id=j.title, title=j.title, company=j.company,
                              location=j.location, url="", platform="eval",
                              posted_at=posted.get(j.title, "Unknown")))
        verdicts.append(jev.Verdict(
            judged=True, keep=bool(j.jev_keep), fit=float(j.fit_noul or 0.0),
            role_family=j.role_family or "", seniority=j.seniority or "",
            employment_type=j.employment_type or "", confidence=j.confidence or 0.0,
            reason=j.reason, error=""))

    by_title = {j.title: j for j in ok}
    with patch.object(config, "JEV_RANKING", False), \
         patch.object(config, "JEV_ENFORCE", False):
        recency_order, _ = _main._rank_for_notification(fake_jobs, verdicts)
    with patch.object(config, "JEV_RANKING", True), \
         patch.object(config, "JEV_ENFORCE", False):
        fit_order, _ = _main._rank_for_notification(fake_jobs, verdicts)

    by_recency = [by_title[j.title] for j in recency_order[:cap]]
    by_fit = [by_title[j.title] for j in fit_order[:cap]]

    def score(bucket):
        entry = sum(1 for j in bucket if j.seniority == "new_grad_or_entry")
        bad = sum(1 for j in bucket if not j.jev_keep)
        mean = sum(j.fit_noul or 0 for j in bucket) / len(bucket)
        return entry, bad, mean

    r_entry, r_bad, r_mean = score(by_recency)
    f_entry, f_bad, f_mean = score(by_fit)

    print("\n" + "=" * 92)
    print(f"RANKING HEAD-TO-HEAD [{profile}] — which {cap} jobs the cap actually posts")
    print("=" * 92)
    print(f"{len(ok)} qualify, only {cap} fit in the cap, so {len(ok) - cap} are deferred.\n")
    print(f"{'':<26}{'recency (today)':>18}{'fit (Phase 3)':>18}{'delta':>10}")
    print("-" * 92)
    print(f"{'explicit new-grad/entry':<26}{r_entry:>18}{f_entry:>18}{f_entry - r_entry:>+10}")
    print(f"{'Jev would reject':<26}{r_bad:>18}{f_bad:>18}{f_bad - r_bad:>+10}")
    print(f"{'mean fit':<26}{r_mean:>18.3f}{f_mean:>18.3f}{f_mean - r_mean:>+10.3f}")

    gained = [j for j in by_fit if j not in by_recency]
    lost = [j for j in by_recency if j not in by_fit]
    print(f"\nRanking swaps {len(lost)} job(s) out of the posted set and {len(gained)} in.")
    if gained:
        print("\nPROMOTED INTO THE CAP (posted under fit, deferred under recency):")
        for j in sorted(gained, key=lambda x: -(x.fit_noul or 0))[:12]:
            print(f"  {(j.fit_noul or 0):4.2f}  {_trunc(j.seniority, 18):<18} "
                  f"{_trunc(j.company, 20):<20} {_trunc(j.title, 40)}")
    if lost:
        print("\nDEMOTED OUT OF THE CAP (posted today, deferred under fit):")
        for j in sorted(lost, key=lambda x: x.fit_noul or 0)[:12]:
            print(f"  {(j.fit_noul or 0):4.2f}  {_trunc(j.seniority, 18):<18} "
                  f"{_trunc(j.company, 20):<20} {_trunc(j.title, 40)}")


def report_first_pass(judgements: list[Judgement], rows: list[dict], profile: str) -> None:
    """Jev as the FIRST pass: does replacing the keyword filter find jobs you are missing?"""
    ok = [j for j in judgements if j.error is None]
    keyword_ok = {r["title"]: r["regex_keyword_ok"] for r in rows}

    both = [j for j in ok if keyword_ok.get(j.title) and j.jev_keep]
    only_regex = [j for j in ok if keyword_ok.get(j.title) and not j.jev_keep]
    only_jev = [j for j in ok if not keyword_ok.get(j.title) and j.jev_keep]
    neither = [j for j in ok if not keyword_ok.get(j.title) and not j.jev_keep]

    print("\n" + "=" * 92)
    print(f"FIRST-PASS EXPERIMENT [{profile}] — Jev REPLACES the keyword filter")
    print("(company + location filters still applied; only keywords are replaced)")
    print("=" * 92)
    print(f"\n{len(ok)} job(s) judged after company+location filtering.\n")
    print(f"{'':>16}{'jev keep':>12}{'jev drop':>12}")
    print(f"{'regex keep':>16}{len(both):>12}{len(only_regex):>12}")
    print(f"{'regex drop':>16}{len(only_jev):>12}{len(neither):>12}")

    regex_total = len(both) + len(only_regex)
    jev_total = len(both) + len(only_jev)
    print(f"\nregex keyword filter would notify : {regex_total}")
    print(f"Jev as first pass would notify    : {jev_total}")
    print(f"\n>>> NEWLY ADMITTED (regex rejects, Jev keeps): {len(only_jev)}")
    print("    These are the jobs a first pass would find that you never see today.")
    print("    Read them: every one is either a real miss or a new source of noise.")
    print(f">>> NEWLY EXCLUDED (regex keeps, Jev drops): {len(only_regex)}")

    if only_jev:
        print(f"\nNEWLY ADMITTED, best fit first (showing up to 40 of {len(only_jev)}):\n")
        print(f"{'fit':>5}  {'family':<24} {'seniority':<18} {'company':<20} title")
        print("-" * 92)
        for j in sorted(only_jev, key=lambda x: -(x.fit_noul or 0))[:40]:
            print(f"{(j.fit_noul or 0):5.2f}  {_trunc(j.role_family, 24):<24} "
                  f"{_trunc(j.seniority, 18):<18} {_trunc(j.company, 20):<20} "
                  f"{_trunc(j.title, 36)}")
        strong = [j for j in only_jev if (j.fit_noul or 0) >= 0.7]
        print(f"\n    of those, {len(strong)} scored fit >= 0.70 "
              f"({100.0 * len(strong) / len(only_jev):.0f}%)")

    print("\nCost of running Jev as a first pass, per run at this volume:")
    tokens = sum(j.input_tokens or 0 for j in ok)
    print(f"    {len(ok)} calls, {tokens:,} input tokens, ~${tokens * 0.042 / 1e6:.3f}")
    print(f"    vs second pass, which judges only unseen jobs (typically 2-115).")


# ---------------------------------------------------------------------------

async def run(args) -> None:
    profiles = _build_profiles()

    if args.golden:
        golden = load_golden()
        if args.limit:
            golden = golden[: args.limit]
        print(f"[INFO] Golden corpus: {len(golden)} labelled titles")

        # Sanity check: the regex should reproduce its own labels exactly.
        scraper = GreenhouseScraper()
        mismatch = 0
        for title, profile, expected in golden:
            kw = (config.DEFAULT_PM_KEYWORDS if profile == "pm"
                  else config.DEFAULT_SWE_FULL_TIME_KEYWORDS)
            ex = (config.DEFAULT_PM_EXCLUDED_KEYWORDS if profile == "pm"
                  else config.DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS)
            if scraper.matches_keywords(title, kw, ex) != expected:
                mismatch += 1
        if mismatch:
            print(f"[WARN] regex disagrees with {mismatch} of its own test labels")

        items = [(t, GOLDEN_COMPANY, GOLDEN_LOCATION, "golden", p, e) for t, p, e in golden]
        judgements, stats = await judge_all(
            items, profiles, args.concurrency, args.model, args.min_confidence,
            args.fit_floor,
        )
        report_golden(judgements)
        if args.sweep:
            report_sweep(judgements)
        _print_usage(stats)

    if args.first_pass:
        if args.from_file:
            rows_by_profile = load_corpus(args.from_file)
        else:
            rows_by_profile = await load_first_pass(args.hours, args.channel, args.limit)
            if args.save_corpus:
                import json
                with open(args.save_corpus, "w", encoding="utf-8") as fh:
                    json.dump(rows_by_profile, fh, indent=2)
                print(f"[INFO] corpus saved to {args.save_corpus}")
        for profile, rows in rows_by_profile.items():
            if not rows:
                continue
            items = [(r["title"], r["company"], r["location"], r["platform"], profile,
                      bool(r.get("regex_keyword_ok"))) for r in rows]
            judgements, stats = await judge_all(
                items, profiles, args.concurrency, args.model, args.min_confidence,
                args.fit_floor,
            )
            report_first_pass(judgements, rows, profile)
            _print_usage(stats)

    if args.live:
        if args.from_file:
            rows_by_profile = load_corpus(args.from_file)
            if args.channel:
                want = "pm" if args.channel == "pm-jobs" else "swe"
                rows_by_profile = {k: v for k, v in rows_by_profile.items() if k == want}
            if args.limit:
                rows_by_profile = {k: v[: args.limit] for k, v in rows_by_profile.items()}
        else:
            per_profile = await load_live(args.hours, args.channel, args.limit)
            if args.save_corpus:
                save_corpus(per_profile, args.save_corpus)
            rows_by_profile = {
                profile: [{"title": j.title, "company": j.company,
                           "location": j.location, "platform": j.platform,
                           "posted_at": j.posted_at} for j in jobs]
                for profile, jobs in per_profile.items()
            }

        for profile, rows in rows_by_profile.items():
            if not rows:
                continue
            items = [(r["title"], r["company"], r["location"], r["platform"], profile, True)
                     for r in rows]
            judgements, stats = await judge_all(
                items, profiles, args.concurrency, args.model, args.min_confidence
            )
            report_live(judgements, profile)
            report_ranking(judgements, rows, profile)
            _print_usage(stats)


def main_cli() -> None:
    p = argparse.ArgumentParser(description="Phase 0 Jev evaluation harness")
    p.add_argument("--golden", action="store_true", help="judge the labelled test corpus")
    p.add_argument("--live", action="store_true", help="scrape and judge what regex accepts")
    p.add_argument("--first-pass", action="store_true", dest="first_pass",
                   help="judge everything past company+location, skipping the keyword filter")
    p.add_argument("--sweep", action="store_true", help="print the fit threshold sweep")
    p.add_argument("--hours", type=int, default=24, help="recency window for --live")
    p.add_argument("--channel", help="limit --live to one channel name")
    p.add_argument("--limit", type=int, help="cap the number of titles judged")
    p.add_argument("--save-corpus", dest="save_corpus",
                   help="write the scraped regex-accepted titles to this JSON file")
    p.add_argument("--from-file", dest="from_file",
                   help="re-judge a saved corpus instead of scraping again")
    p.add_argument("--concurrency", type=int, default=5, help="~4 rps each; 1200 rpm ceiling")
    p.add_argument("--model", default="jev-1.13.0")
    p.add_argument("--min-confidence", type=float, default=0.5, dest="min_confidence")
    p.add_argument("--fit-floor", type=float, default=DEFAULT_FIT_FLOOR, dest="fit_floor",
                   help="drop when fit_noul is below this")
    args = p.parse_args()

    if not args.golden and not args.live and not args.first_pass:
        p.error("pick at least one of --golden / --live / --first-pass")

    asyncio.run(run(args))


if __name__ == "__main__":
    main_cli()
