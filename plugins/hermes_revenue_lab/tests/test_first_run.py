from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_revenue_lab.first_run import (
    CandidateReceipt,
    HumanReviewReceipt,
    SubsystemCheck,
    build_first_run_plan,
)
from hermes_revenue_lab.guard import (
    RevenueSnapshot,
    WorkloadSpec,
    evaluate_revenue_guard,
)

REQUIRED_CHECKS = (
    "model_routing",
    "guard",
    "cron",
    "artifacts",
    "browser",
    "ledger",
    "opportunity_scoring",
)
LANES = ("b2b_opportunity", "niche_intelligence", "digital_product")


def checks() -> tuple[SubsystemCheck, ...]:
    return tuple(
        SubsystemCheck(name, True, (), f"test:{name}") for name in REQUIRED_CHECKS
    )


def candidates(count: int = 20) -> tuple[CandidateReceipt, ...]:
    return tuple(
        CandidateReceipt(
            candidate_id=f"candidate-{index:02d}",
            experiment_lane=LANES[index % len(LANES)],
            ranking_tier="A" if index < 3 else "B",
            scout_eligible=True,
            evidence_ref=f"scout:candidate-{index:02d}",
        )
        for index in range(count)
    )


def review(rows: tuple[CandidateReceipt, ...]) -> HumanReviewReceipt:
    approved = tuple(
        next(item.candidate_id for item in rows if item.experiment_lane == lane)
        for lane in LANES
    )
    return HumanReviewReceipt(
        review_id="review-001",
        reviewer_kind="human",
        authenticated=True,
        reviewed_candidate_ids=tuple(item.candidate_id for item in rows),
        approved_candidate_ids=approved,
        rationale_refs=tuple(f"review:{item}" for item in approved),
        approval_receipt_ref="approval:first-run-001",
        reviewed_at="2026-08-21T12:00:00+00:00",
    )


def snapshot(**overrides: object) -> RevenueSnapshot:
    values: dict[str, object] = {
        "luna_process_count": 0,
        "revenue_worker_count": 0,
        "load_1m": 1.0,
        "cpu_count": 16,
        "memory_free_percent": 70.0,
        "swap_used_bytes": 0,
        "swap_total_bytes": 0,
        "swap_delta_bytes": 0,
        "memory_pressure_available": True,
        "foreign_ollama_model_count": 0,
        "luna_health_status": "unavailable",
        "luna_health_latency_ms": None,
    }
    values.update(overrides)
    return RevenueSnapshot(**values)  # type: ignore[arg-type]


class FirstRunTest(unittest.TestCase):
    def test_complete_review_nominates_exactly_three_without_launch_authority(
        self,
    ) -> None:
        rows = candidates()
        plan = build_first_run_plan(checks(), rows, human_review=review(rows))
        self.assertEqual("ready_for_operator_launch_decision", plan.status)
        self.assertEqual(
            set(LANES), {item.experiment_lane for item in plan.nominations}
        )
        self.assertEqual(3, len(plan.nominations))
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.publishing_allowed)
        self.assertFalse(plan.spending_allowed)
        self.assertFalse(plan.customer_outreach_allowed)
        self.assertFalse(plan.experiment_launch_allowed)
        self.assertTrue(all(item.requires_fresh_approval for item in plan.nominations))

    def test_failed_subsystem_blocks_before_scout_or_review(self) -> None:
        broken = tuple(
            replace(item, passed=False, reason_codes=("browser_unavailable",))
            if item.name == "browser"
            else item
            for item in checks()
        )
        plan = build_first_run_plan(
            broken, candidates(), human_review=review(candidates())
        )
        self.assertEqual("blocked_validation", plan.status)
        self.assertIn("browser:browser_unavailable", plan.reasons)
        self.assertEqual((), plan.nominations)

    def test_fewer_than_twenty_evidence_bound_candidates_blocks(self) -> None:
        rows = candidates(19)
        plan = build_first_run_plan(checks(), rows, human_review=None)
        self.assertEqual("blocked_scout_count", plan.status)
        self.assertIn("minimum_20_candidates_not_met", plan.reasons)

    def test_human_review_is_mandatory_and_must_cover_all_candidates(self) -> None:
        rows = candidates()
        awaiting = build_first_run_plan(checks(), rows, human_review=None)
        incomplete = build_first_run_plan(
            checks(),
            rows,
            human_review=replace(
                review(rows),
                reviewed_candidate_ids=review(rows).reviewed_candidate_ids[:-1],
            ),
        )
        self.assertEqual("awaiting_human_review", awaiting.status)
        self.assertEqual("blocked_human_review", incomplete.status)

    def test_real_guard_blocks_artificial_active_luna_and_queued_heavy_worker(
        self,
    ) -> None:
        overnight = datetime(2026, 8, 21, 1, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        for guard_snapshot, reason in (
            (snapshot(luna_process_count=1), "luna_active"),
            (snapshot(revenue_worker_count=1), "revenue_worker_active"),
        ):
            decision = evaluate_revenue_guard(
                guard_snapshot,
                WorkloadSpec("heavy_model", parameter_billions=27),
                now=overnight,
            )
            guarded_checks = tuple(
                SubsystemCheck(
                    "guard", decision.permitted, decision.reasons, "guard:test"
                )
                if item.name == "guard"
                else item
                for item in checks()
            )
            plan = build_first_run_plan(
                guarded_checks,
                candidates(),
                human_review=review(candidates()),
            )
            self.assertEqual("blocked_validation", plan.status)
            self.assertIn(f"guard:{reason}", plan.reasons)

    def test_review_cannot_approve_more_than_three_or_duplicate_a_lane(self) -> None:
        rows = candidates()
        too_many = replace(
            review(rows),
            approved_candidate_ids=tuple(item.candidate_id for item in rows[:4]),
            rationale_refs=tuple(f"review:{item.candidate_id}" for item in rows[:4]),
        )
        duplicate_lane = replace(
            review(rows),
            approved_candidate_ids=("candidate-00", "candidate-03", "candidate-02"),
            rationale_refs=("review:0", "review:3", "review:2"),
        )
        self.assertEqual(
            "blocked_human_review",
            build_first_run_plan(checks(), rows, human_review=too_many).status,
        )
        self.assertEqual(
            "blocked_human_review",
            build_first_run_plan(checks(), rows, human_review=duplicate_lane).status,
        )


if __name__ == "__main__":
    unittest.main()
