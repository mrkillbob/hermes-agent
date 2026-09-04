from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from hermes_revenue_lab.deterministic.catalog import (
    DETERMINISTIC_OPERATIONS,
    require_no_llm,
)
from hermes_revenue_lab.deterministic.operations import (
    calculate_revenue,
    compare_timestamps,
    content_changed,
    cpu_load_snapshot,
    decimal_arithmetic,
    deduplicate_exact_ids,
    experiment_metrics,
    hash_file,
    loopback_health_check,
    memory_free_percent,
    read_only_sqlite_query,
    threshold_compare,
    within_schedule,
)


class FakeResponse:
    status = 200


class FakeConnection:
    def __init__(self, host, port, timeout):
        self.identity = (host, port, timeout)
        self.request_value = None
        self.closed = False

    def request(self, method, path, headers):
        self.request_value = (method, path, headers)

    def getresponse(self):
        return FakeResponse()

    def close(self):
        self.closed = True


class ZeroLlmOperationsTest(unittest.TestCase):
    def test_catalog_covers_every_forbidden_llm_operation(self) -> None:
        self.assertEqual(
            {
                "url_change",
                "document_hash",
                "timestamp_compare",
                "exact_id_deduplicate",
                "decimal_arithmetic",
                "sqlite_query",
                "experiment_metrics",
                "revenue_calculation",
                "health_endpoint",
                "cpu_load",
                "ram_inspection",
                "market_schedule",
                "threshold_compare",
            },
            DETERMINISTIC_OPERATIONS,
        )
        for operation in DETERMINISTIC_OPERATIONS:
            self.assertEqual("no_llm", require_no_llm(operation, "no_llm"))
            with self.assertRaisesRegex(ValueError, "requires no_llm"):
                require_no_llm(operation, "fast")

    def test_hash_and_change_detection_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "document.txt"
            path.write_bytes(b"bounded evidence")
            digest = hashlib.sha256(b"bounded evidence").hexdigest()

            self.assertEqual(digest, hash_file(path, allowed_root=root, max_bytes=100))
            self.assertFalse(content_changed(digest, b"bounded evidence"))
            self.assertTrue(content_changed("0" * 64, b"bounded evidence"))
            with self.assertRaisesRegex(ValueError, "size bound"):
                hash_file(path, allowed_root=root, max_bytes=5)
            with self.assertRaisesRegex(ValueError, "allowed root"):
                hash_file(root.parent / "outside", allowed_root=root)

    def test_timestamp_decimal_revenue_and_threshold_math_is_exact(self) -> None:
        self.assertTrue(
            compare_timestamps(
                "2026-08-21T00:00:00Z",
                "2026-08-20T23:59:59+00:00",
                ">",
            )
        )
        self.assertEqual(Decimal("0.3"), decimal_arithmetic("0.1", "+", "0.2"))
        self.assertEqual(Decimal("10.01"), calculate_revenue(["1.01", "9.00"]))
        self.assertTrue(threshold_compare("84.5", ">=", "80"))
        with self.assertRaises(ZeroDivisionError):
            decimal_arithmetic("1", "/", "0")

    def test_exact_id_deduplication_preserves_first_row(self) -> None:
        rows = [
            {"id": "A", "value": 1},
            {"id": "B", "value": 2},
            {"id": "A", "value": 3},
        ]
        unique, duplicate_count = deduplicate_exact_ids(rows, "id")
        self.assertEqual((rows[0], rows[1]), unique)
        self.assertEqual(1, duplicate_count)
        with self.assertRaisesRegex(ValueError, "missing exact id"):
            deduplicate_exact_ids([{}], "id")

    def test_sqlite_query_is_read_only_single_statement_and_row_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "metrics.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("create table metrics (id text, value integer)")
            connection.executemany("insert into metrics values (?, ?)", [("A", 1), ("B", 2)])
            connection.commit()
            connection.close()

            rows = read_only_sqlite_query(
                database,
                "select id, value from metrics order by id",
                allowed_root=root,
                max_rows=10,
            )

            self.assertEqual([{"id": "A", "value": 1}, {"id": "B", "value": 2}], rows)
            with self.assertRaisesRegex(ValueError, "SELECT or WITH"):
                read_only_sqlite_query(database, "delete from metrics", allowed_root=root)
            with self.assertRaisesRegex(ValueError, "single statement"):
                read_only_sqlite_query(database, "select 1; select 2", allowed_root=root)
            with self.assertRaisesRegex(ValueError, "row bound"):
                read_only_sqlite_query(
                    database,
                    "select * from metrics",
                    allowed_root=root,
                    max_rows=1,
                )

    def test_experiment_metrics_do_not_infer_invalid_counts(self) -> None:
        metrics = experiment_metrics(exposures=20, conversions=5, revenue="50.00")
        self.assertEqual(Decimal("0.25"), metrics["conversion_rate"])
        self.assertEqual(Decimal("2.50"), metrics["revenue_per_exposure"])
        with self.assertRaisesRegex(ValueError, "conversions"):
            experiment_metrics(exposures=2, conversions=3, revenue="1")

    def test_health_check_is_loopback_only_and_does_not_follow_redirects(self) -> None:
        observed = []

        def factory(host, port, timeout):
            connection = FakeConnection(host, port, timeout)
            observed.append(connection)
            return connection

        self.assertTrue(
            loopback_health_check(
                "http://127.0.0.1:9120/health",
                connection_factory=factory,
            )
        )
        self.assertEqual(("127.0.0.1", 9120, 2.0), observed[0].identity)
        self.assertEqual("/health", observed[0].request_value[1])
        self.assertTrue(observed[0].closed)
        with self.assertRaisesRegex(ValueError, "loopback"):
            loopback_health_check("https://example.com/health", connection_factory=factory)

    def test_cpu_ram_and_schedule_evaluation_are_explicit(self) -> None:
        snapshot = cpu_load_snapshot(load_values=(4.0, 3.0, 2.0), cpu_count=8)
        self.assertEqual(Decimal("0.5"), snapshot["normalized_load_1m"])
        self.assertEqual(
            Decimal("73"),
            memory_free_percent("System-wide memory free percentage: 73%"),
        )
        during = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)
        self.assertTrue(
            within_schedule(
                during,
                timezone_name="America/New_York",
                weekdays={0, 1, 2, 3, 4},
                start="09:30",
                end="16:00",
            )
        )
        self.assertFalse(
            within_schedule(
                datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc),
                timezone_name="America/New_York",
                weekdays={0, 1, 2, 3, 4},
                start="09:30",
                end="16:00",
            )
        )

    def test_deterministic_package_has_no_model_or_ollama_imports(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "hermes_revenue_lab" / "deterministic"
        source = "\n".join(path.read_text() for path in root.glob("*.py"))
        self.assertNotIn("ollama_client", source)
        self.assertNotIn("routing.router", source)
        self.assertNotIn("/api/chat", source)


if __name__ == "__main__":
    unittest.main()
