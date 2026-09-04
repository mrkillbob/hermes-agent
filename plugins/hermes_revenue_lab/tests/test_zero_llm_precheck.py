from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hermes_revenue_lab.inventory.redaction import PublicationSafetyError
from hermes_revenue_lab.deterministic.precheck import (
    PrecheckDecision,
    evaluate_precheck,
)


ROOT = Path(__file__).resolve().parents[1]


class ZeroLlmPrecheckTest(unittest.TestCase):
    def test_false_gate_is_exact_canonical_hermes_contract(self) -> None:
        decision = PrecheckDecision(wake_agent=False, context={})
        self.assertEqual('{"wakeAgent":false}', decision.render())

    def test_file_digest_gate_wakes_only_when_bounded_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "inputs" / "page.txt"
            source.parent.mkdir()
            source.write_text("version one", encoding="utf-8")
            config = {
                "schema_version": "hrl.precheck.v1",
                "operation": "url_change",
                "input_path": "inputs/page.txt",
                "state_path": "state/page.sha256",
                "max_bytes": 100,
            }

            first = evaluate_precheck(config, allowed_root=root)
            second = evaluate_precheck(config, allowed_root=root)
            source.write_text("version two", encoding="utf-8")
            third = evaluate_precheck(config, allowed_root=root)

            self.assertTrue(first.wake_agent)
            self.assertEqual("content_changed", first.context["reason_code"])
            self.assertFalse(second.wake_agent)
            self.assertTrue(third.wake_agent)
            state = root / "state" / "page.sha256"
            self.assertEqual(64, len(state.read_text().strip()))
            self.assertEqual(0o600, os.stat(state).st_mode & 0o777)

    def test_threshold_gate_wakes_only_on_deterministic_breach(self) -> None:
        quiet = evaluate_precheck(
            {
                "schema_version": "hrl.precheck.v1",
                "operation": "threshold_compare",
                "value": "49.9",
                "operator": ">=",
                "threshold": "50",
            },
            allowed_root=Path.cwd(),
        )
        breached = evaluate_precheck(
            {
                "schema_version": "hrl.precheck.v1",
                "operation": "threshold_compare",
                "value": "50",
                "operator": ">=",
                "threshold": "50",
            },
            allowed_root=Path.cwd(),
        )

        self.assertFalse(quiet.wake_agent)
        self.assertEqual(
            {
                "operator": ">=",
                "reason_code": "threshold_met",
                "threshold": "50",
                "value": "50",
            },
            breached.context,
        )

    def test_unsafe_input_or_state_path_fails_before_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            root.mkdir()
            outside = root.parent / "outside.txt"
            outside.write_text("external", encoding="utf-8")
            config = {
                "schema_version": "hrl.precheck.v1",
                "operation": "document_hash",
                "input_path": str(outside),
                "state_path": "state/hash.txt",
                "max_bytes": 100,
            }

            with self.assertRaisesRegex(ValueError, "allowed root"):
                evaluate_precheck(config, allowed_root=root)

            self.assertFalse((root / "state").exists())

    def test_unknown_or_secret_labeled_configuration_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported precheck operation"):
            evaluate_precheck(
                {"schema_version": "hrl.precheck.v1", "operation": "interpret_document"},
                allowed_root=Path.cwd(),
            )
        with self.assertRaises(PublicationSafetyError):
            evaluate_precheck(
                {
                    "schema_version": "hrl.precheck.v1",
                    "operation": "threshold_compare",
                    "value": "1",
                    "operator": ">",
                    "threshold": "0",
                    "note": "token: must-not-enter-context",
                },
                allowed_root=Path.cwd(),
            )

    def test_cli_prints_gate_as_last_and_only_stdout_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "quiet.json"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": "hrl.precheck.v1",
                        "operation": "threshold_compare",
                        "value": "1",
                        "operator": ">",
                        "threshold": "2",
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "zero_llm_precheck.py"),
                    "--config",
                    str(config),
                    "--allowed-root",
                    str(root),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(['{"wakeAgent":false}'], result.stdout.splitlines())
            self.assertEqual("", result.stderr)

    def test_copied_hermes_script_resolves_matching_precheck_config_without_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hermes_home = root / ".hermes"
            scripts = hermes_home / "scripts"
            prechecks = hermes_home / "prechecks"
            scripts.mkdir(parents=True)
            prechecks.mkdir()
            installed = scripts / "quiet-threshold.py"
            shutil.copy2(ROOT / "scripts" / "zero_llm_precheck.py", installed)
            (prechecks / "quiet-threshold.json").write_text(
                json.dumps(
                    {
                        "schema_version": "hrl.precheck.v1",
                        "operation": "threshold_compare",
                        "value": "1",
                        "operator": ">",
                        "threshold": "2",
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "HERMES_HOME": str(hermes_home),
                "HERMES_WRITE_SAFE_ROOT": str(root),
            }

            result = subprocess.run(
                [sys.executable, str(installed)],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('{"wakeAgent":false}\n', result.stdout)


if __name__ == "__main__":
    unittest.main()
