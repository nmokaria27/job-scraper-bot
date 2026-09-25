"""
Jev (typesafe.ai) second-pass judge.

The regex filters in scrapers/base.py stay the source of truth. This module is a
SECOND pass: it only ever sees jobs the regex already accepted, and it can only
reject or reorder them — it can never promote a job the regex rejected. That
bound is deliberate. Job titles from HN and the aggregator lists are attacker
controlled, and Jev does not treat state as hostile, so the worst a malicious
title can do is rank itself slightly higher inside an already-qualifying set.

Everything here fails open. Any error, any missing key, any budget or deadline
overrun yields UNJUDGED, whose `keep` is True — so a Jev outage degrades to
exactly today's behaviour rather than silencing the bot. That mirrors the
source-isolation convention in main.run_bulk / main.fetch_company, where a
crashing source returns [] instead of killing the run.

Phase 1 ships ranking only: config.JEV_ENFORCE is False, so `keep` is never
acted on. See the plan file for the phase ladder.

Tuning constants came from the Phase 0 harness (jev_eval.py) measured over
three scrape windows on 2026-09-17. Re-run it before changing them.
"""

import asyncio
from dataclasses import dataclass, replace

import config
from config import ChannelConfig
from scrapers.base import Job

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """One job's judgement. Index-aligned with the jobs list it came from.

    Deliberately not keyed by job.id: an index-aligned list has no key scheme to
    get wrong and no collision with main.get_job_seen_key.
    """
    judged: bool          # False = Jev never answered; `keep` and `fit` are defaults
    keep: bool            # True unless Jev judged it and rejected it
    fit: float            # 0.0-1.0 relevance, for ranking. 0.0 when unjudged.
    role_family: str
    seniority: str
    employment_type: str
    confidence: float     # min across the Choice answers, for logging only
    reason: str           # why it was rejected, e.g. "seniority=mid_level"
    error: str            # "" when the call succeeded


UNJUDGED = Verdict(
    judged=False, keep=True, fit=0.0, role_family="", seniority="",
    employment_type="", confidence=0.0, reason="", error="",
)


# ---------------------------------------------------------------------------
# Question profiles
#
# Prompts live in git, never in CHANNELS_JSON: a secret is unreviewable and
# undiffable, and a typo there would be a silent production behaviour change.
# ---------------------------------------------------------------------------

# Role families that disqualify a posting, per profile.
REJECT_FAMILIES: dict[str, set[str]] = {
    "swe": {"other_engineering", "enterprise_or_ops", "it_security_ops",
            "qa_test", "gtm_or_customer_facing", "non_technical"},
    # NOT program_or_project_management: the pm channel is "entry-level PM/APM/TPM",
    # so technical program managers are in scope.
    "pm": {"product_marketing", "retail_or_physical_product", "engineering"},
}
# mid_level belongs here: "Software Engineer II" / "L3" / "Engineer - E2" are exactly
# the titles the bare-token hacks in config._SENIORITY_EXCLUSIONS exist to catch, and
# Phase 0 showed Jev catches the ones the regex misses (Lockheed "E2", Thales
# "Intermediate").
REJECT_SENIORITY = {"internship_or_coop", "mid_level", "senior_or_above"}
REJECT_EMPLOYMENT = {"internship_or_coop", "contract_or_temp", "part_time_or_hourly"}

# Promotion (JEV_PROMOTE) is the ONLY path where Jev can admit a job the regex
# rejected, so it is gated far harder than an ordinary keep. A keep is "no reason
# to reject"; a promotion must be a positive, confident match in a named family.
# It runs only when a channel is UNDER its notification cap — which is exactly
# when extra candidates help, and keeps the call volume small.
#
# Why this is bounded rather than a full first pass: titles from HN and the
# aggregator lists are attacker controlled. Requiring an accepted family AND high
# confidence AND a high fit means an injected string has to satisfy several
# independent constrained classifiers, not just one. Promoted jobs are also
# flagged in Discord so an admission is never silent.
ACCEPT_FAMILIES: dict[str, set[str]] = {
    "swe": {"software_engineering", "ml_ai_research", "data"},
    "pm": {"product_management", "program_or_project_management", "product_ops_analytics"},
}
# A promoted job must not look senior. "unspecified" is allowed because most entry
# roles carry no level tag at all (the same reason the keyword filter is role-only).
PROMOTE_SENIORITY = {"new_grad_or_entry", "unspecified"}


def should_promote(verdict: "Verdict", profile: str) -> bool:
    """Stricter than `keep`: a positive match, not merely the absence of a reason."""
    if not verdict.judged or not verdict.keep:
        return False
    if verdict.role_family not in ACCEPT_FAMILIES.get(profile, set()):
        return False
    if verdict.seniority not in PROMOTE_SENIORITY:
        return False
    if verdict.confidence < config.JEV_MIN_CONFIDENCE:
        return False
    return verdict.fit >= config.JEV_PROMOTE_MIN_FIT

_PROFILE_BY_CHANNEL = {"pm-jobs": "pm"}
_DEFAULT_PROFILE = "swe"

_QUESTIONS_CACHE: dict | None = None


def profile_for(channel: ChannelConfig) -> str:
    """Explicit jev_profile wins, then a channel-name map, then the swe default."""
    explicit = (getattr(channel, "jev_profile", "") or "").strip().lower()
    if explicit:
        return explicit
    return _PROFILE_BY_CHANNEL.get(channel.name, _DEFAULT_PROFILE)


def _build_questions() -> dict:
    """Built once, lazily — importing the SDK at module scope would make
    `import main` fail on a machine without typesafe-sdk installed."""
    global _QUESTIONS_CACHE
    if _QUESTIONS_CACHE is not None:
        return _QUESTIONS_CACHE

    sdk = _load_sdk()
    if sdk is None:
        return {}
    Choice, Noul = sdk.Choice, sdk.Noul

    seniority = Choice(
        instructions=(
            "What experience level does this job title indicate? Answer 'unspecified' when "
            "the title names no level at all. A roman numeral or digit after the role name "
            "(II, 2, III, L4, E2) indicates 'mid_level'. 'I' or '1' indicates "
            "'new_grad_or_entry'. 'Member of Technical Staff' is a standard "
            "individual-contributor title at AI labs and carries NO level signal on its "
            "own: answer 'unspecified' unless the title also says Senior, Staff or Principal."
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

    swe_fit = (
        "How well does this posting match a candidate seeking a US-based, full-time, "
        "entry-level or new-graduate software engineering, AI, ML or data role? "
        "Judge only from the title, company and location given."
    )
    pm_fit = (
        "How well does this posting match a candidate seeking a US-based, full-time, "
        "entry-level or new-graduate product management role (PM, APM, product owner)? "
        "Judge only from the title, company and location given."
    )

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
        "fit": Noul(instructions=swe_fit),
    }

    pm = {
        "role_family": Choice(
            instructions="Judging only from the job title, which job family is this posting?",
            criteria={
                "product_management": "Product manager, product owner, APM, TPM",
                "program_or_project_management": (
                    "Technical program manager, project manager, or delivery manager"
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
        "fit": Noul(instructions=pm_fit),
    }

    _QUESTIONS_CACHE = {"swe": swe, "pm": pm}
    return _QUESTIONS_CACHE


def build_state(job: Job) -> dict:
    """A dict, not a prose sentence: prose reads like an instruction and would hand
    attacker-controlled title text the same register as the question.

    Omits url (no classification signal, pure tokens, more injection surface) and
    posted_at (Jev reads dates as text, and recency belongs to filter_recent_jobs).
    """
    return {
        "job_title": " ".join((job.title or "").split())[:200],
        "company": " ".join((job.company or "").split())[:80],
        "location": " ".join((job.location or "").split())[:120],
        "source": job.platform,
    }


# ---------------------------------------------------------------------------
# SDK loading (lazy, so the repo imports fine without typesafe-sdk installed)
# ---------------------------------------------------------------------------

_SDK: object | None = None
_SDK_LOADED = False


def _load_sdk():
    global _SDK, _SDK_LOADED
    if not _SDK_LOADED:
        _SDK_LOADED = True
        try:
            import typesafe_sdk
            _SDK = typesafe_sdk
        except ImportError:
            _SDK = None
            print("[JEV] typesafe-sdk not installed — judge disabled")
    return _SDK


def is_enabled() -> bool:
    """Config is read at call time, never bound at import: config values are module
    constants and test_run.py already mutates them in place."""
    if not config.JEV_ENABLED:
        return False
    if not config.JEV_API_KEY:
        print("[JEV] JEV_API_KEY is not set — judge disabled")
        return False
    return _load_sdk() is not None


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass
class Budget:
    """Shared across every channel in a run, so two channels cannot each spend the
    full allowance. The deadline, not the call count, is the real safety property:
    max_calls alone still permits max_calls x (timeout + retries) of wall clock."""
    max_calls: int
    deadline: float          # absolute asyncio loop time
    calls_used: int = 0
    consecutive_errors: int = 0
    disabled: bool = False


def new_budget(now: float | None = None) -> Budget:
    base = now if now is not None else _loop_time()
    return Budget(
        max_calls=config.JEV_MAX_CALLS_PER_RUN,
        deadline=base + config.JEV_RUN_DEADLINE_SECONDS,
    )


def _loop_time() -> float:
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return 0.0


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------


def to_verdict(response, profile: str) -> Verdict:
    """Map one SystemOneResponse to a Verdict.

    Confidence is applied PER DIMENSION, not as a min() across all of them. Phase 0
    showed that taking the minimum let a single uncertain answer veto every other
    confident rejection — a confident "this is a program manager" must still be able
    to reject when the employment-type answer happens to be a coin flip.
    """
    fam = response.answers["role_family"]
    sen = response.answers["seniority"]
    emp = response.answers["employment_type"]
    clearance = response.answers["requires_clearance"].noul
    fit = float(response.answers["fit"].noul)
    conf = min(fam.confidence, sen.confidence, emp.confidence)

    min_conf = config.JEV_MIN_CONFIDENCE
    reason = ""
    if fam.choice in REJECT_FAMILIES.get(profile, set()) and fam.confidence >= min_conf:
        reason = f"family={fam.choice}"
    elif sen.choice in REJECT_SENIORITY and sen.confidence >= min_conf:
        reason = f"seniority={sen.choice}"
    elif emp.choice in REJECT_EMPLOYMENT and emp.confidence >= min_conf:
        reason = f"employment={emp.choice}"
    elif clearance >= config.JEV_CLEARANCE_THRESHOLD:
        reason = "clearance"
    elif fit < config.JEV_FIT_FLOOR:
        reason = f"fit<{config.JEV_FIT_FLOOR:.2f}"

    return Verdict(
        judged=True,
        keep=not reason,
        fit=fit,
        role_family=fam.choice,
        seniority=sen.choice,
        employment_type=emp.choice,
        confidence=conf,
        reason=reason,
        error="",
    )


def _describe(exc: BaseException) -> str:
    """str(ReadTimeout()) is empty, and the same trap applies to the SDK's timeout
    types — always name the exception class."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


_FATAL_ERRORS = {"TypeSafeAuthenticationError", "TypeSafePermissionDeniedError"}


def _on_error(budget: Budget, exc: BaseException) -> Verdict:
    name = type(exc).__name__
    budget.consecutive_errors += 1
    if name in _FATAL_ERRORS:
        budget.disabled = True
        print(f"[JEV] disabled for this run: {_describe(exc)}")
    elif budget.consecutive_errors >= config.JEV_ERROR_CIRCUIT:
        budget.disabled = True
        print(f"[JEV] circuit tripped after {budget.consecutive_errors} errors: {name}")
    return replace(UNJUDGED, error=_describe(exc))


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------


def _retry_policy():
    """The SDK default (2 retries, 5s max backoff, 10s timeout) is a 30s+ worst case
    per job, incompatible with a 90s run deadline. Keep respect_retry_after."""
    sdk = _load_sdk()
    if sdk is None:
        return None
    return sdk.RetryPolicy(
        max_retries=config.JEV_MAX_RETRIES,
        backoff_initial=0.5,
        backoff_max=2.0,
        respect_retry_after=True,
    )


async def judge_jobs(
    jobs: list[Job],
    profile: str,
    budget: Budget,
    *,
    client=None,
) -> list[Verdict]:
    """Judge `jobs`, returning verdicts index-aligned with them. Never raises."""
    if not jobs:
        return []
    if profile == "off" or (client is None and not is_enabled()):
        return [UNJUDGED] * len(jobs)

    questions = _build_questions().get(profile)
    if not questions:
        print(f"[JEV] unknown profile '{profile}' — skipping {len(jobs)} job(s)")
        return [UNJUDGED] * len(jobs)

    sem = asyncio.Semaphore(max(1, config.JEV_CONCURRENCY))
    model = config.JEV_MODEL
    retry = _retry_policy()
    timeout = config.JEV_TIMEOUT_SECONDS

    async def judge_one(job: Job, active) -> Verdict:
        if budget.disabled:
            return replace(UNJUDGED, error="disabled")
        if budget.calls_used >= budget.max_calls:
            return replace(UNJUDGED, error="budget_exhausted")
        if _loop_time() >= budget.deadline:
            return replace(UNJUDGED, error="deadline")
        async with sem:
            # Re-check inside the semaphore: the breaker may have tripped while queued.
            if budget.disabled or budget.calls_used >= budget.max_calls:
                return replace(UNJUDGED, error="budget_exhausted")
            budget.calls_used += 1
            try:
                response = await active.system_one(
                    state=build_state(job),
                    questions=questions,
                    model=model,
                    retry=retry,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - a judge failure must never kill a run
                return _on_error(budget, exc)
            budget.consecutive_errors = 0
            try:
                return to_verdict(response, profile)
            except Exception as exc:  # noqa: BLE001 - malformed response is still fail-open
                return replace(UNJUDGED, error=_describe(exc))

    async def gather_with(active) -> list[Verdict]:
        results = await asyncio.gather(
            *(judge_one(job, active) for job in jobs), return_exceptions=True
        )
        return [r if isinstance(r, Verdict) else UNJUDGED for r in results]

    if client is not None:
        return await gather_with(client)

    sdk = _load_sdk()
    try:
        async with sdk.AsyncTypeSafeClient(
            api_key=config.JEV_API_KEY, timeout=timeout
        ) as active:
            return await gather_with(active)
    except Exception as exc:  # noqa: BLE001 - client construction must also fail open
        print(f"[JEV] client unavailable: {_describe(exc)}")
        return [UNJUDGED] * len(jobs)


async def judge_for_channel(
    jobs: list[Job],
    channel: ChannelConfig,
    budget: Budget,
    *,
    client=None,
) -> list[Verdict]:
    """Resolve the channel's profile, then judge."""
    return await judge_jobs(jobs, profile_for(channel), budget, client=client)


async def promote_jobs(
    candidates: list[Job],
    channel: ChannelConfig,
    budget: Budget,
    limit: int,
    *,
    client=None,
) -> list[tuple[Job, Verdict]]:
    """Judge regex-REJECTED jobs and return the few that clear the promotion bar.

    `limit` is how many promotions the caller can actually use (the free space under
    its cap), so we never pay to judge more than could possibly be posted. Returns
    (job, verdict) pairs, best fit first. Never raises.
    """
    if not candidates or not config.JEV_PROMOTE or limit <= 0:
        return []
    if client is None and not is_enabled():
        return []

    profile = profile_for(channel)
    # Bound the work: judge at most JEV_PROMOTE_MAX_CALLS, freshest first.
    batch = candidates[: max(0, config.JEV_PROMOTE_MAX_CALLS)]
    verdicts = await judge_jobs(batch, profile, budget, client=client)

    promoted = [
        (job, verdict)
        for job, verdict in zip(batch, verdicts)
        if should_promote(verdict, profile)
    ]
    promoted.sort(key=lambda pair: -pair[1].fit)

    if batch:
        print(f"[JEV] '{channel.name}': promotion scan judged {len(batch)} "
              f"regex-rejected job(s), {len(promoted)} cleared fit "
              f">= {config.JEV_PROMOTE_MIN_FIT:.2f}")
    for job, verdict in promoted[:limit]:
        print(f"[JEV] promoted: {job.title} @ {job.company} "
              f"({verdict.role_family}, fit={verdict.fit:.2f}, conf={verdict.confidence:.2f})")
    return promoted[:limit]


def summarise(channel_name: str, jobs: list[Job], verdicts: list[Verdict]) -> None:
    """One log line per run per channel, plus the would-be drops while shadowing."""
    if not verdicts:
        return
    judged = [v for v in verdicts if v.judged]
    rejected = [v for v in judged if not v.keep]
    errors = [v for v in verdicts if v.error]
    mean_fit = sum(v.fit for v in judged) / len(judged) if judged else 0.0
    print(
        f"[JEV] '{channel_name}': judged {len(judged)}/{len(verdicts)}, "
        f"{'dropped' if config.JEV_ENFORCE else 'would reject'} {len(rejected)}, "
        f"mean fit {mean_fit:.2f}"
        + (f", {len(errors)} error(s)" if errors else "")
    )
    if rejected:
        verb = "dropped" if config.JEV_ENFORCE else "would drop"
        for job, verdict in zip(jobs, verdicts):
            if verdict.judged and not verdict.keep:
                print(f"[JEV] {verb}: {job.title} @ {job.company} "
                      f"({verdict.reason}, fit={verdict.fit:.2f})")
    if errors:
        print(f"[JEV] first error: {errors[0].error}")
