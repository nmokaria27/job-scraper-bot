"""Offline tests for the Jev second-pass judge.

Every test here runs without network and without typesafe-sdk installed: the
client is injected, and config.JEV_* is patched in place (config values are
module constants read at call time, the same convention test_run.py relies on).
"""

import asyncio
import unittest
from unittest.mock import patch

import config
import jev
import main
from config import ChannelConfig
from scrapers.base import Job


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Choice:
    def __init__(self, choice, confidence=0.99):
        self.choice = choice
        self.confidence = confidence


class _Noul:
    def __init__(self, noul):
        self.noul = noul


class _Response:
    def __init__(self, family="software_engineering", seniority="new_grad_or_entry",
                 employment="full_time", clearance=0.01, fit=0.9,
                 family_conf=0.99, seniority_conf=0.99, employment_conf=0.99):
        self.answers = {
            "role_family": _Choice(family, family_conf),
            "seniority": _Choice(seniority, seniority_conf),
            "employment_type": _Choice(employment, employment_conf),
            "requires_clearance": _Noul(clearance),
            "fit": _Noul(fit),
        }


class StubClient:
    """Conforms to the one method jev.judge_jobs calls."""

    def __init__(self, responses=None, raises=None):
        self.responses = responses or {}
        self.raises = raises or {}
        self.calls = 0
        self.states = []

    async def system_one(self, *, state, questions, model=None, retry=None, timeout=None):
        self.calls += 1
        self.states.append(state)
        title = state["job_title"]
        if title in self.raises:
            raise self.raises[title]
        return self.responses.get(title, _Response())


class _Boom(Exception):
    pass


class TypeSafeAuthenticationError(Exception):
    """Name-matched by jev._FATAL_ERRORS, so the stub only needs the class name."""


def make_job(title="Software Engineer", company="Example Co",
             posted_at="2026-09-17T12:00:00+00:00", job_id=None, platform="greenhouse"):
    return Job(
        id=job_id or f"greenhouse-example-{abs(hash(title)) % 10**6}",
        title=title,
        company=company,
        location="San Francisco, CA",
        url=f"https://example.invalid/jobs/{abs(hash(title)) % 10**6}",
        platform=platform,
        posted_at=posted_at,
    )


def run(coro):
    return asyncio.run(coro)


def fresh_budget(max_calls=100, deadline=10**9):
    return jev.Budget(max_calls=max_calls, deadline=deadline)


# ---------------------------------------------------------------------------


class EnablementTests(unittest.TestCase):
    def test_disabled_by_default_makes_no_calls(self) -> None:
        with patch.object(config, "JEV_ENABLED", False):
            verdicts = run(jev.judge_jobs([make_job()], "swe", fresh_budget()))
        self.assertEqual(verdicts, [jev.UNJUDGED])

    def test_missing_api_key_disables(self) -> None:
        with patch.object(config, "JEV_ENABLED", True), \
             patch.object(config, "JEV_API_KEY", ""):
            self.assertFalse(jev.is_enabled())

    def test_profile_off_skips_judging(self) -> None:
        client = StubClient()
        verdicts = run(jev.judge_jobs([make_job()], "off", fresh_budget(), client=client))
        self.assertEqual(client.calls, 0)
        self.assertFalse(verdicts[0].judged)

    def test_empty_job_list_short_circuits(self) -> None:
        client = StubClient()
        self.assertEqual(run(jev.judge_jobs([], "swe", fresh_budget(), client=client)), [])
        self.assertEqual(client.calls, 0)

    def test_profile_for_resolves_channel(self) -> None:
        self.assertEqual(jev.profile_for(ChannelConfig(name="pm-jobs", webhook_url="")), "pm")
        self.assertEqual(
            jev.profile_for(ChannelConfig(name="swe-ai-full-time", webhook_url="")), "swe")
        self.assertEqual(
            jev.profile_for(ChannelConfig(name="anything", webhook_url="", jev_profile="pm")),
            "pm")


class VerdictMappingTests(unittest.TestCase):
    def test_clean_entry_level_role_is_kept(self) -> None:
        v = jev.to_verdict(_Response(), "swe")
        self.assertTrue(v.judged)
        self.assertTrue(v.keep)
        self.assertAlmostEqual(v.fit, 0.9)
        self.assertEqual(v.reason, "")

    def test_rejected_family_drops(self) -> None:
        v = jev.to_verdict(_Response(family="other_engineering"), "swe")
        self.assertFalse(v.keep)
        self.assertEqual(v.reason, "family=other_engineering")

    def test_mid_level_drops(self) -> None:
        v = jev.to_verdict(_Response(seniority="mid_level"), "swe")
        self.assertFalse(v.keep)
        self.assertEqual(v.reason, "seniority=mid_level")

    def test_clearance_drops(self) -> None:
        v = jev.to_verdict(_Response(clearance=0.97), "swe")
        self.assertFalse(v.keep)
        self.assertEqual(v.reason, "clearance")

    def test_fit_below_floor_drops(self) -> None:
        v = jev.to_verdict(_Response(fit=0.05), "swe")
        self.assertFalse(v.keep)
        self.assertTrue(v.reason.startswith("fit<"))

    def test_low_confidence_does_not_drop(self) -> None:
        """Fail-open bias: an uncertain categorical answer must not reject."""
        v = jev.to_verdict(_Response(family="other_engineering", family_conf=0.20), "swe")
        self.assertTrue(v.keep)

    def test_confidence_is_applied_per_dimension(self) -> None:
        """A confident seniority rejection must survive an uncertain employment answer.
        Using min() across answers previously let one coin flip veto every rejection."""
        v = jev.to_verdict(
            _Response(seniority="senior_or_above", seniority_conf=0.95, employment_conf=0.10),
            "swe",
        )
        self.assertFalse(v.keep)
        self.assertEqual(v.reason, "seniority=senior_or_above")

    def test_pm_profile_keeps_technical_program_managers(self) -> None:
        """CLAUDE.md defines the pm channel as entry-level PM/APM/TPM."""
        v = jev.to_verdict(_Response(family="program_or_project_management"), "pm")
        self.assertTrue(v.keep)


class FailOpenTests(unittest.TestCase):
    def test_single_failure_does_not_affect_other_jobs(self) -> None:
        good, bad = make_job("Software Engineer"), make_job("Backend Engineer")
        client = StubClient(raises={"Backend Engineer": _Boom("network down")})
        verdicts = run(jev.judge_jobs([good, bad], "swe", fresh_budget(), client=client))
        self.assertTrue(verdicts[0].judged)
        self.assertFalse(verdicts[1].judged)
        self.assertTrue(verdicts[1].keep, "an unjudged job must still be kept")
        self.assertIn("_Boom", verdicts[1].error)

    def test_every_call_failing_keeps_every_job(self) -> None:
        jobs = [make_job(f"Engineer {i}") for i in range(4)]
        client = StubClient(raises={j.title: _Boom("down") for j in jobs})
        with patch.object(config, "JEV_ERROR_CIRCUIT", 99):
            verdicts = run(jev.judge_jobs(jobs, "swe", fresh_budget(), client=client))
        self.assertTrue(all(v.keep for v in verdicts))
        self.assertTrue(all(not v.judged for v in verdicts))

    def test_malformed_response_fails_open(self) -> None:
        class Bad:
            answers: dict = {}
        client = StubClient(responses={"Software Engineer": Bad()})
        verdicts = run(jev.judge_jobs([make_job()], "swe", fresh_budget(), client=client))
        self.assertFalse(verdicts[0].judged)
        self.assertTrue(verdicts[0].keep)

    def test_auth_error_disables_for_the_rest_of_the_run(self) -> None:
        budget = fresh_budget()
        client = StubClient(raises={"A": TypeSafeAuthenticationError("bad key")})
        run(jev.judge_jobs([make_job("A")], "swe", budget, client=client))
        self.assertTrue(budget.disabled)

        # A later channel sharing the budget must make no further calls.
        client2 = StubClient()
        verdicts = run(jev.judge_jobs([make_job("B")], "swe", budget, client=client2))
        self.assertEqual(client2.calls, 0)
        self.assertTrue(verdicts[0].keep)

    def test_circuit_trips_after_consecutive_errors(self) -> None:
        jobs = [make_job(f"Engineer {i}") for i in range(10)]
        client = StubClient(raises={j.title: _Boom("x") for j in jobs})
        budget = fresh_budget()
        with patch.object(config, "JEV_ERROR_CIRCUIT", 3), \
             patch.object(config, "JEV_CONCURRENCY", 1):
            run(jev.judge_jobs(jobs, "swe", budget, client=client))
        self.assertTrue(budget.disabled)
        self.assertLess(client.calls, len(jobs), "breaker should stop further calls")


class BudgetTests(unittest.TestCase):
    def test_call_budget_caps_requests(self) -> None:
        jobs = [make_job(f"Engineer {i}") for i in range(10)]
        client = StubClient()
        budget = fresh_budget(max_calls=4)
        with patch.object(config, "JEV_CONCURRENCY", 1):
            verdicts = run(jev.judge_jobs(jobs, "swe", budget, client=client))
        self.assertEqual(client.calls, 4)
        self.assertEqual(budget.calls_used, 4)
        self.assertEqual(sum(1 for v in verdicts if v.judged), 4)
        self.assertTrue(all(v.keep for v in verdicts), "unjudged overflow is still kept")

    def test_expired_deadline_makes_no_calls(self) -> None:
        client = StubClient()
        budget = jev.Budget(max_calls=100, deadline=-1.0)
        verdicts = run(jev.judge_jobs([make_job()], "swe", budget, client=client))
        self.assertEqual(client.calls, 0)
        self.assertEqual(verdicts[0].error, "deadline")


class StateShapingTests(unittest.TestCase):
    def test_state_omits_url_and_date(self) -> None:
        state = jev.build_state(make_job())
        self.assertEqual(set(state), {"job_title", "company", "location", "source"})
        self.assertNotIn("url", state)
        self.assertNotIn("posted_at", state)

    def test_state_collapses_whitespace_and_truncates(self) -> None:
        job = make_job(title="Software   \n  Engineer " + "x" * 400)
        state = jev.build_state(job)
        self.assertTrue(state["job_title"].startswith("Software Engineer x"))
        self.assertLessEqual(len(state["job_title"]), 200)


class RankingTests(unittest.TestCase):
    """The regression lock: ranking must be invisible when Jev says nothing."""

    def setUp(self) -> None:
        self.jobs = [
            make_job("Oldest", posted_at="2026-09-15T00:00:00+00:00"),
            make_job("Undated", posted_at="Unknown"),
            make_job("Newest", posted_at="2026-09-17T18:00:00+00:00"),
            make_job("Middle", posted_at="2026-09-16T09:00:00+00:00"),
        ]

    def test_all_unjudged_matches_newest_first_exactly(self) -> None:
        verdicts = [jev.UNJUDGED] * len(self.jobs)
        with patch.object(config, "JEV_RANKING", True), \
             patch.object(config, "JEV_ENFORCE", True):
            ordered, dropped = main._rank_for_notification(self.jobs, verdicts)
        self.assertEqual(dropped, [])
        self.assertEqual(
            [j.title for j in ordered],
            [j.title for j in main._newest_first(self.jobs)],
            "an unjudged run must reproduce _newest_first character for character",
        )

    def test_high_fit_outranks_newer_low_fit(self) -> None:
        verdicts = [
            jev.Verdict(True, True, 0.95, "software_engineering", "new_grad_or_entry",
                        "full_time", 0.9, "", ""),    # Oldest, great fit
            jev.UNJUDGED,
            jev.Verdict(True, True, 0.20, "software_engineering", "unspecified",
                        "unspecified", 0.9, "", ""),  # Newest, poor fit
            jev.UNJUDGED,
        ]
        with patch.object(config, "JEV_RANKING", True):
            ordered, _ = main._rank_for_notification(self.jobs, verdicts)
        self.assertEqual(ordered[0].title, "Oldest")
        self.assertEqual(ordered[-1].title, "Newest")

    def test_ranking_disabled_ignores_fit(self) -> None:
        verdicts = [
            jev.Verdict(True, True, 0.99, "software_engineering", "new_grad_or_entry",
                        "full_time", 0.9, "", ""),
            jev.UNJUDGED,
            jev.Verdict(True, True, 0.01, "software_engineering", "unspecified",
                        "unspecified", 0.9, "", ""),
            jev.UNJUDGED,
        ]
        with patch.object(config, "JEV_RANKING", False):
            ordered, _ = main._rank_for_notification(self.jobs, verdicts)
        self.assertEqual([j.title for j in ordered],
                         [j.title for j in main._newest_first(self.jobs)])

    def test_enforce_off_keeps_rejected_jobs(self) -> None:
        verdicts = [jev.UNJUDGED] * 4
        verdicts[0] = jev.Verdict(True, False, 0.1, "other_engineering", "unspecified",
                                  "unspecified", 0.9, "family=other_engineering", "")
        with patch.object(config, "JEV_ENFORCE", False), \
             patch.object(config, "JEV_RANKING", True):
            ordered, dropped = main._rank_for_notification(self.jobs, verdicts)
        self.assertEqual(dropped, [])
        self.assertEqual(len(ordered), 4)

    def test_enforce_on_drops_rejected_jobs(self) -> None:
        verdicts = [jev.UNJUDGED] * 4
        verdicts[0] = jev.Verdict(True, False, 0.1, "other_engineering", "unspecified",
                                  "unspecified", 0.9, "family=other_engineering", "")
        with patch.object(config, "JEV_ENFORCE", True), \
             patch.object(config, "JEV_RANKING", True):
            ordered, dropped = main._rank_for_notification(self.jobs, verdicts)
        self.assertEqual([j.title for j in dropped], ["Oldest"])
        self.assertEqual(len(ordered), 3)

    def test_unjudged_jobs_are_not_buried_under_judged_ones(self) -> None:
        """During an outage some jobs may be judged and others not. Unjudged jobs
        sit in a neutral bucket so they still compete."""
        verdicts = [
            jev.UNJUDGED,
            jev.UNJUDGED,
            jev.Verdict(True, True, 0.05, "software_engineering", "unspecified",
                        "unspecified", 0.9, "", ""),   # Newest, judged poorly
            jev.UNJUDGED,
        ]
        with patch.object(config, "JEV_RANKING", True):
            ordered, _ = main._rank_for_notification(self.jobs, verdicts)
        self.assertNotEqual(ordered[0].title, "Newest")
        self.assertEqual(ordered[-1].title, "Newest")


if __name__ == "__main__":
    unittest.main()


class PromotionTests(unittest.TestCase):
    """Promotion is the ONLY path where Jev may admit a job the regex rejected,
    so every guard on it is tested."""

    def setUp(self) -> None:
        self.channel = ChannelConfig(name="swe-ai-full-time", webhook_url="")
        self.jobs = [make_job("AI Compiler Engineer - New College Grad")]

    def _run(self, response, limit=5, **cfg):
        client = StubClient(responses={self.jobs[0].title: response})
        defaults = dict(JEV_PROMOTE=True, JEV_PROMOTE_MIN_FIT=0.75,
                        JEV_PROMOTE_MAX_CALLS=150, JEV_MIN_CONFIDENCE=0.5)
        defaults.update(cfg)
        patches = [patch.object(config, k, v) for k, v in defaults.items()]
        for p in patches:
            p.start()
        try:
            return run(jev.promote_jobs(self.jobs, self.channel, fresh_budget(),
                                        limit, client=client)), client
        finally:
            for p in patches:
                p.stop()

    def test_strong_match_is_promoted(self) -> None:
        promoted, _ = self._run(_Response(fit=0.83))
        self.assertEqual(len(promoted), 1)
        self.assertEqual(promoted[0][0].title, "AI Compiler Engineer - New College Grad")

    def test_disabled_by_default_makes_no_calls(self) -> None:
        promoted, client = self._run(_Response(fit=0.95), JEV_PROMOTE=False)
        self.assertEqual(promoted, [])
        self.assertEqual(client.calls, 0)

    def test_fit_below_promote_bar_is_not_promoted(self) -> None:
        """0.60 passes the ordinary keep floor (0.17) but not the promotion bar."""
        promoted, _ = self._run(_Response(fit=0.60))
        self.assertEqual(promoted, [])

    def test_family_outside_accept_set_is_not_promoted(self) -> None:
        """A family merely absent from REJECT_FAMILIES is not enough; promotion
        needs a positive match in ACCEPT_FAMILIES."""
        promoted, _ = self._run(_Response(family="other", fit=0.95))
        self.assertEqual(promoted, [])

    def test_low_confidence_is_not_promoted(self) -> None:
        promoted, _ = self._run(_Response(fit=0.95, family_conf=0.30))
        self.assertEqual(promoted, [])

    def test_senior_role_is_not_promoted(self) -> None:
        promoted, _ = self._run(_Response(seniority="senior_or_above", fit=0.95))
        self.assertEqual(promoted, [])

    def test_unspecified_seniority_is_allowed(self) -> None:
        """Most entry roles carry no level tag, which is why the keyword filter is
        role-only; promotion must not be stricter than that."""
        promoted, _ = self._run(_Response(seniority="unspecified", fit=0.90))
        self.assertEqual(len(promoted), 1)

    def test_zero_free_slots_makes_no_calls(self) -> None:
        promoted, client = self._run(_Response(fit=0.95), limit=0)
        self.assertEqual(promoted, [])
        self.assertEqual(client.calls, 0)

    def test_limit_caps_the_number_promoted(self) -> None:
        self.jobs = [make_job(f"Software Engineer I - {i}") for i in range(5)]
        client = StubClient()
        with patch.object(config, "JEV_PROMOTE", True), \
             patch.object(config, "JEV_PROMOTE_MIN_FIT", 0.75), \
             patch.object(config, "JEV_PROMOTE_MAX_CALLS", 150):
            promoted = run(jev.promote_jobs(self.jobs, self.channel, fresh_budget(),
                                            2, client=client))
        self.assertEqual(len(promoted), 2)

    def test_max_calls_bounds_the_scan(self) -> None:
        self.jobs = [make_job(f"Software Engineer I - {i}") for i in range(20)]
        client = StubClient()
        with patch.object(config, "JEV_PROMOTE", True), \
             patch.object(config, "JEV_PROMOTE_MAX_CALLS", 6), \
             patch.object(config, "JEV_CONCURRENCY", 1):
            run(jev.promote_jobs(self.jobs, self.channel, fresh_budget(), 25, client=client))
        self.assertEqual(client.calls, 6)

    def test_promotion_failure_yields_nothing_not_an_exception(self) -> None:
        client = StubClient(raises={self.jobs[0].title: _Boom("down")})
        with patch.object(config, "JEV_PROMOTE", True):
            promoted = run(jev.promote_jobs(self.jobs, self.channel, fresh_budget(),
                                            5, client=client))
        self.assertEqual(promoted, [])

    def test_results_are_ordered_best_fit_first(self) -> None:
        self.jobs = [make_job("Low"), make_job("High"), make_job("Mid")]
        client = StubClient(responses={
            "Low": _Response(fit=0.76), "High": _Response(fit=0.97),
            "Mid": _Response(fit=0.85),
        })
        with patch.object(config, "JEV_PROMOTE", True), \
             patch.object(config, "JEV_PROMOTE_MIN_FIT", 0.75):
            promoted = run(jev.promote_jobs(self.jobs, self.channel, fresh_budget(),
                                            5, client=client))
        self.assertEqual([j.title for j, _ in promoted], ["High", "Mid", "Low"])


class PromotionCandidateTests(unittest.TestCase):
    """main.promotion_candidates must return exactly what filter_for_channel discards."""

    def setUp(self) -> None:
        self.channel = ChannelConfig(
            name="swe-ai-full-time", webhook_url="",
            keywords=config.DEFAULT_SWE_FULL_TIME_KEYWORDS,
            excluded_keywords=config.DEFAULT_SWE_FULL_TIME_EXCLUDED_KEYWORDS,
            locations=config.DEFAULT_LOCATIONS,
            excluded_locations=config.DEFAULT_EXCLUDED_LOCATIONS,
            excluded_companies=config.DEFAULT_EXCLUDED_COMPANIES,
        )

    def _job(self, title, company="Example Co", location="San Francisco, CA"):
        return Job(
            id=f"id-{abs(hash(title + company)) % 10**6}", title=title, company=company,
            location=location, url="https://example.invalid/1", platform="greenhouse",
            posted_at="2026-09-17T00:00:00+00:00")

    def test_candidates_are_disjoint_from_matches(self) -> None:
        jobs = [
            self._job("Software Engineer"),            # keyword match
            self._job("Vibration Analyst"),            # no keyword match -> candidate
            self._job("Space Systems Engineer"),       # no keyword match -> candidate
        ]
        matched = main.filter_for_channel(jobs, self.channel)
        candidates = main.promotion_candidates(jobs, self.channel)
        self.assertEqual([j.title for j in matched], ["Software Engineer"])
        self.assertCountEqual(
            [j.title for j in candidates], ["Vibration Analyst", "Space Systems Engineer"])
        self.assertFalse(set(j.id for j in matched) & set(j.id for j in candidates))

    def test_excluded_company_is_never_a_candidate(self) -> None:
        jobs = [self._job("Vibration Analyst", company="Microsoft")]
        self.assertEqual(main.promotion_candidates(jobs, self.channel), [])

    def test_foreign_location_is_never_a_candidate(self) -> None:
        jobs = [self._job("Vibration Analyst", location="Toronto, ON, CA")]
        self.assertEqual(main.promotion_candidates(jobs, self.channel), [])


class SponsorshipFilterTests(unittest.TestCase):
    """US-person-only employers must be excluded by default, from EVERY source.

    They arrive mostly from the aggregator feeds rather than companies.py, so the
    filter has to be a company-name exclusion, not just an ATS-list split.
    """

    def _excluded(self, company: str) -> bool:
        from scrapers.base import company_is_excluded
        return company_is_excluded(company, config.effective_excluded_companies())

    def test_defense_primes_are_excluded_by_default(self) -> None:
        for company in ("Lockheed Martin", "Northrop Grumman", "L3Harris Technologies",
                        "BAE Systems", "General Dynamics", "Anduril Industries"):
            self.assertTrue(self._excluded(company), company)

    def test_federal_integrators_are_excluded_by_default(self) -> None:
        for company in ("CACI International", "Leidos", "Booz Allen Hamilton",
                        "Peraton", "Noblis", "MITRE", "Guidehouse", "SAIC"):
            self.assertTrue(self._excluded(company), company)

    def test_commercial_employers_are_not_excluded(self) -> None:
        for company in ("Stripe", "Databricks", "OpenAI", "Anthropic", "Vercel",
                        "Crusoe", "CoreWeave", "Snowflake"):
            self.assertFalse(self._excluded(company), company)

    def test_whole_word_matching_avoids_false_positives(self) -> None:
        """"bae" must not hit "Bae Systems Inc"-alikes via substring matching."""
        self.assertFalse(self._excluded("Metabase"))
        self.assertFalse(self._excluded("Baemin"))
        self.assertTrue(self._excluded("BAE Systems"))

    def test_base_exclusions_are_preserved(self) -> None:
        for company in ("Microsoft", "Uber", "Meta"):
            self.assertTrue(self._excluded(company), company)

    def test_flag_restores_non_sponsoring_employers(self) -> None:
        from scrapers.base import company_is_excluded
        with patch.object(config, "INCLUDE_NON_SPONSORING_COMPANIES", True):
            eff = config.effective_excluded_companies()
        self.assertFalse(company_is_excluded("Lockheed Martin", eff))
        self.assertTrue(company_is_excluded("Microsoft", eff))

    def test_us_only_ats_boards_are_out_of_the_default_list(self) -> None:
        import companies
        self.assertNotIn("andurilindustries", companies.COMPANIES["greenhouse"])
        self.assertNotIn("skydio", companies.COMPANIES["ashby"])
        self.assertIn("andurilindustries", companies.US_ONLY_COMPANIES["greenhouse"])
        self.assertIn("skydio", companies.US_ONLY_COMPANIES["ashby"])
