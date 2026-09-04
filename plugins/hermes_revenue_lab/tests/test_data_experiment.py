from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from hermes_revenue_lab.experiments.data_product import (
    MONETIZATION_HYPOTHESES,
    SELECTED_DATA_EXPERIMENT,
    NHTSARecallHistory,
    VehicleQuery,
    build_nhtsa_url,
    fetch_nhtsa_payload,
    process_nhtsa_payload,
    publish_update_run,
)
from hermes_revenue_lab.provenance import (
    ArtifactRunStore,
    RunManifest,
    RunVerdict,
    verify_run,
)

OBSERVED = "2026-08-21T20:00:00+00:00"
ENDED = "2026-08-21T20:00:02+00:00"
SHA_A = "a" * 64
SHA_B = "b" * 64


def query() -> VehicleQuery:
    return VehicleQuery(model_year=2012, make="Acura", model="RDX")


def result(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "Manufacturer": "Honda (American Honda Motor Co.)",
        "NHTSACampaignNumber": "19V182000",
        "parkIt": False,
        "parkOutSide": False,
        "overTheAirUpdate": False,
        "NHTSAActionNumber": "EA15001",
        "ReportReceivedDate": "06/03/2019",
        "Component": "AIR BAGS:FRONTAL",
        "Summary": "The inflator may rupture during deployment.",
        "Consequence": "Metal fragments may cause serious injury or death.",
        "Remedy": "Dealers will replace the inflator free of charge.",
        "Notes": "Owners may contact the NHTSA Vehicle Safety Hotline.",
        "ModelYear": "2012",
        "Make": "ACURA",
        "Model": "RDX",
    }
    values.update(overrides)
    return values


def payload(*rows: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "Count": len(rows),
            "Message": "Results returned successfully",
            "results": rows,
        },
        sort_keys=True,
    ).encode()


def manifest(run_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        experiment_id=SELECTED_DATA_EXPERIMENT.experiment_id,
        task_name="nhtsa_recall_update",
        run_reason="Bounded daily model-year recall change check.",
        started_at=OBSERVED,
        ended_at=ENDED,
        code_commit="4cc00dc",
        routing_policy_sha256=SHA_A,
        compliance_registry_sha256=SHA_B,
    )


def verdict(run_id: str) -> RunVerdict:
    return RunVerdict(
        run_id=run_id,
        status="completed",
        experiment_decision="continue",
        reason_codes=("private_draft_updated",),
        cost_status="known",
        total_cost_usd=Decimal(0),
        revenue_status="known",
        gross_revenue_usd=Decimal(0),
        revenue_ledger_ref=f"ledger:{SELECTED_DATA_EXPERIMENT.experiment_id}",
        output_summary="A private recall-change digest was updated.",
    )


class DataExperimentContractTest(unittest.TestCase):
    def test_one_problem_and_all_monetization_modes_remain_hypotheses(self) -> None:
        self.assertEqual(
            "independent_used_car_dealer_model_recall_watch",
            SELECTED_DATA_EXPERIMENT.experiment_id,
        )
        self.assertIn("not VIN-specific", SELECTED_DATA_EXPERIMENT.safety_limitation)
        self.assertEqual(
            {
                "one_time_csv",
                "paid_report",
                "alert_subscription",
                "business_subscription",
                "api",
            },
            {item.mode for item in MONETIZATION_HYPOTHESES},
        )
        self.assertEqual(
            {"hypothesis"}, {item.status for item in MONETIZATION_HYPOTHESES}
        )

    def test_query_builds_only_the_documented_non_vin_endpoint(self) -> None:
        self.assertEqual(
            "https://api.nhtsa.gov/recalls/recallsByVehicle?make=Acura&model=RDX&modelYear=2012",
            build_nhtsa_url(query()),
        )
        with self.assertRaisesRegex(ValueError, "model year"):
            VehicleQuery(model_year=1900, make="Acura", model="RDX")

    def test_collector_is_bounded_and_transport_injectable(self) -> None:
        observed: list[tuple[str, float]] = []

        def transport(url: str, timeout: float) -> bytes:
            observed.append((url, timeout))
            return payload(result())

        collected = fetch_nhtsa_payload(query(), transport=transport, timeout=4.0)
        self.assertEqual(payload(result()), collected)
        self.assertEqual([(build_nhtsa_url(query()), 4.0)], observed)
        with self.assertRaisesRegex(ValueError, "payload size"):
            fetch_nhtsa_payload(
                query(), transport=lambda _url, _timeout: b"x" * 5_000_001
            )


class RecallPipelineTest(unittest.TestCase):
    def test_collect_normalize_source_dedupe_history_score_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = NHTSARecallHistory(
                root / "private" / "history.sqlite3", allowed_root=root
            )
            update = process_nhtsa_payload(
                query=query(),
                raw_payload=payload(result()),
                collected_at=OBSERVED,
                history=history,
            )

            self.assertEqual(("19V182000:2012:ACURA:RDX",), update.added_record_ids)
            self.assertEqual((), update.changed_record_ids)
            self.assertEqual((), update.unchanged_record_ids)
            self.assertEqual(1, len(update.digest.items))
            self.assertEqual(30, update.digest.items[0].urgency_score)
            self.assertEqual(("death",), update.digest.items[0].reason_codes)
            self.assertEqual("private_draft", update.digest.status)
            self.assertIn("not VIN-specific", update.digest.disclaimer)
            item_document = update.digest.canonical_record()["items"][0]
            self.assertEqual(
                update.source.source_id, item_document["provenance"]["source_id"]
            )
            self.assertEqual(
                update.source.content_sha256,
                item_document["provenance"]["source_content_sha256"],
            )
            self.assertTrue(update.source.use_permitted)
            self.assertEqual("public_api", update.source.source_kind)
            self.assertEqual(0o600, history.database.stat().st_mode & 0o777)

            same = process_nhtsa_payload(
                query=query(),
                raw_payload=payload(result()),
                collected_at="2026-08-22T20:00:00+00:00",
                history=history,
            )
            self.assertEqual(("19V182000:2012:ACURA:RDX",), same.unchanged_record_ids)
            self.assertEqual(1, history.version_count("19V182000:2012:ACURA:RDX"))

            changed = process_nhtsa_payload(
                query=query(),
                raw_payload=payload(result(Remedy="A revised remedy is available.")),
                collected_at="2026-08-23T20:00:00+00:00",
                history=history,
            )
            self.assertEqual(("19V182000:2012:ACURA:RDX",), changed.changed_record_ids)
            self.assertEqual(2, history.version_count("19V182000:2012:ACURA:RDX"))

    def test_duplicate_rows_are_exact_deduped_and_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = NHTSARecallHistory(root / "history.sqlite3", allowed_root=root)
            exact = process_nhtsa_payload(
                query=query(),
                raw_payload=payload(result(), result()),
                collected_at=OBSERVED,
                history=history,
            )
            self.assertEqual(1, len(exact.digest.items))
            with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                process_nhtsa_payload(
                    query=query(),
                    raw_payload=payload(result(), result(Remedy="Conflicting remedy.")),
                    collected_at="2026-08-22T20:00:00+00:00",
                    history=history,
                )

    def test_malformed_or_wrong_scope_payloads_fail_without_history_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = NHTSARecallHistory(root / "history.sqlite3", allowed_root=root)
            wrong_count = json.dumps(
                {"Count": 2, "Message": "ok", "results": [result()]}
            ).encode()
            with self.assertRaisesRegex(ValueError, "count"):
                process_nhtsa_payload(
                    query=query(),
                    raw_payload=wrong_count,
                    collected_at=OBSERVED,
                    history=history,
                )
            with self.assertRaisesRegex(ValueError, "requested vehicle"):
                process_nhtsa_payload(
                    query=query(),
                    raw_payload=payload(result(Model="MDX")),
                    collected_at=OBSERVED,
                    history=history,
                )
            self.assertEqual(0, history.total_versions())

    def test_history_is_root_contained_and_not_symlinked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, "outside"):
                NHTSARecallHistory(outside / "history.sqlite3", allowed_root=root)
            real = root / "real.sqlite3"
            sqlite3.connect(real).close()
            linked = root / "linked.sqlite3"
            linked.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "symlink"):
                NHTSARecallHistory(linked, allowed_root=root)

    def test_update_publishes_through_hrl15_provenance_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = NHTSARecallHistory(root / "history.sqlite3", allowed_root=root)
            update = process_nhtsa_payload(
                query=query(),
                raw_payload=payload(result()),
                collected_at=OBSERVED,
                history=history,
            )
            run_id = "nhtsa-run-001"
            run_dir = publish_update_run(
                store=ArtifactRunStore(root / "artifacts", allowed_root=root),
                manifest=manifest(run_id),
                update=update,
                model_usage=(),
                verdict=verdict(run_id),
            )
            self.assertTrue(verify_run(run_dir, allowed_root=root).valid)
            sources = json.loads((run_dir / "sources.json").read_text())
            self.assertEqual(
                "us_government_work", sources["sources"][0]["permission_basis"]
            )
            digest = json.loads(
                (run_dir / "outputs" / "recall_digest.json").read_text()
            )
            self.assertEqual("private_draft", digest["status"])

            wrong_manifest = replace(
                manifest("wrong-experiment-run"), experiment_id="other"
            )
            with self.assertRaisesRegex(ValueError, "selected HRL-9 experiment"):
                publish_update_run(
                    store=ArtifactRunStore(root / "other-artifacts", allowed_root=root),
                    manifest=wrong_manifest,
                    update=update,
                    model_usage=(),
                    verdict=verdict("wrong-experiment-run"),
                )


if __name__ == "__main__":
    unittest.main()
