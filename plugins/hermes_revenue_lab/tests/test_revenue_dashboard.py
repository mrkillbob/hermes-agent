from __future__ import annotations

import json
import threading
import unittest
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hermes_revenue_lab.dashboard import (
    DashboardSnapshot,
    ExperimentCounts,
    GuardPanel,
    GuardResourceState,
    ModelEconomics,
    OpportunityQueueItem,
    TodayMetrics,
    dashboard_server,
    render_dashboard,
)

OBSERVED = "2026-08-21T23:30:00+00:00"


def snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        generated_at=OBSERVED,
        freshness="current",
        source_reasons=(),
        today=TodayMetrics(
            revenue=Decimal("120.00"),
            expenses=Decimal("20.00"),
            profit=Decimal("100.00"),
            customers=3,
            compute_hours=Decimal("1.5"),
            human_intervention_minutes=Decimal(12),
        ),
        experiments=ExperimentCounts(
            researching=2,
            testing=1,
            profitable=1,
            scaling=0,
            killed=1,
        ),
        model_economics=(
            ModelEconomics(
                model="qwen3.5:4b",
                invocations=4,
                median_latency_seconds=Decimal("8.2"),
                success_rate=Decimal("0.75"),
                escalation_rate=Decimal("0.25"),
                compute_hours=Decimal("0.01"),
            ),
        ),
        guard=GuardPanel(
            state="FULL",
            reasons=(),
            last_transition=OBSERVED,
            resources=GuardResourceState(
                load_1m=1.2,
                cpu_count=10,
                memory_free_percent=62.5,
                swap_used_bytes=0,
                foreign_ollama_model_count=0,
                luna_health_status="healthy",
            ),
        ),
        opportunity_queue=(
            OpportunityQueueItem(
                candidate_id="candidate-001",
                score="A",
                evidence_count=4,
                proposed_experiment="nhtsa_model_recall_watch",
                required_approval="human_review",
            ),
        ),
    )


class DashboardSnapshotTest(unittest.TestCase):
    def test_known_snapshot_round_trips_without_float_money(self) -> None:
        document = snapshot().canonical_record()
        encoded = json.dumps(document, sort_keys=True)
        restored = DashboardSnapshot.from_document(json.loads(encoded))
        self.assertEqual(snapshot(), restored)
        self.assertEqual("120.00", document["today"]["revenue"])

    def test_unknowns_remain_unavailable_and_profit_must_reconcile(self) -> None:
        unavailable = DashboardSnapshot.unavailable(
            generated_at=OBSERVED,
            reasons=("ledger_unavailable", "routing_events_unavailable"),
        )
        html = render_dashboard(unavailable)
        self.assertIn("Unavailable", html)
        self.assertIn("ledger_unavailable", html)
        self.assertNotIn("$0.00", html)
        with self.assertRaisesRegex(ValueError, "profit"):
            TodayMetrics(
                revenue=Decimal(10),
                expenses=Decimal(2),
                profit=Decimal(9),
                customers=0,
                compute_hours=Decimal(0),
                human_intervention_minutes=Decimal(0),
            )
        loss = TodayMetrics(
            revenue=Decimal(2),
            expenses=Decimal(5),
            profit=Decimal(-3),
        )
        self.assertEqual(Decimal(-3), loss.profit)

    def test_rates_and_freshness_are_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "success rate"):
            ModelEconomics(
                model="model",
                invocations=1,
                median_latency_seconds=Decimal(1),
                success_rate=Decimal("1.1"),
                escalation_rate=Decimal(0),
                compute_hours=Decimal(0),
            )
        with self.assertRaisesRegex(ValueError, "freshness"):
            DashboardSnapshot(
                generated_at=OBSERVED,
                freshness="fresh-ish",
                source_reasons=(),
                today=TodayMetrics(),
                experiments=ExperimentCounts(),
                model_economics=(),
                guard=GuardPanel.unavailable(),
                opportunity_queue=(),
            )


class DashboardRenderTest(unittest.TestCase):
    def test_render_matches_required_information_architecture(self) -> None:
        html = render_dashboard(snapshot())
        for text in (
            "Hermes Revenue Lab",
            "Local evidence only",
            "Today summary",
            "Experiments status rail",
            "Model economics",
            "Luna guard",
            "Opportunity queue",
            "Required approval",
        ):
            self.assertIn(text, html)
        self.assertIn("$120.00", html)
        self.assertIn("qwen3.5:4b", html)
        self.assertIn("candidate-001", html)
        self.assertIn("@media", html)

    def test_render_is_observability_only_and_escapes_data(self) -> None:
        unsafe = DashboardSnapshot(
            generated_at=OBSERVED,
            freshness="partial",
            source_reasons=("<script>alert(1)</script>",),
            today=TodayMetrics(),
            experiments=ExperimentCounts(),
            model_economics=(),
            guard=GuardPanel.unavailable(),
            opportunity_queue=(),
        )
        html = render_dashboard(unsafe)
        self.assertIn("&lt;script&gt;", html)
        lowered = html.lower()
        for prohibited in ("<form", "<button", "brokerage", "buy order", "sell order"):
            self.assertNotIn(prohibited, lowered)

    def test_loopback_server_exposes_only_read_only_endpoints(self) -> None:
        server = dashboard_server(lambda: snapshot(), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/", timeout=2) as response:
                self.assertEqual(200, response.status)
                self.assertIn(b"Hermes Revenue Lab", response.read())
                self.assertEqual("DENY", response.headers["X-Frame-Options"])
            with urlopen(base + "/api/snapshot", timeout=2) as response:
                self.assertEqual(
                    snapshot().canonical_record(), json.loads(response.read())
                )
            with urlopen(base + "/health", timeout=2) as response:
                self.assertEqual(
                    {"status": "ok", "mode": "read_only"}, json.loads(response.read())
                )
            with self.assertRaises(HTTPError) as context:
                urlopen(Request(base + "/", data=b"{}", method="POST"), timeout=2)
            self.assertEqual(405, context.exception.code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
