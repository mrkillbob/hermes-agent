from pathlib import Path
import unittest

from hermes_revenue_lab.inventory.collector import collect_inventory
from hermes_revenue_lab.inventory.types import CommandResult, InventoryContext


class RecordingRunner:
    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.outputs = outputs or {}
        self.argv: list[tuple[str, ...]] = []

    def __call__(self, spec):
        self.argv.append(spec.argv)
        return CommandResult(
            name=spec.name,
            status="available",
            exit_code=0,
            stdout=self.outputs.get(spec.name, ""),
            stderr="",
            duration_seconds=0.01,
        )


class CollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = InventoryContext(
            workspace=Path("/Users/mikedemott/HermesRevenueLab"),
            hermes_home=Path("/Users/mikedemott/HermesRevenueLab/.hermes"),
            tradingbot_path=Path("/Users/mikedemott/TradingBotV18"),
        )

    def test_document_has_required_sections_and_no_model_invocation(self) -> None:
        """Catches missing schema sections or an HRL-0 inference call."""
        runner = RecordingRunner(
            {
                "hermes_version": "Hermes Agent v0.20.4\nPython: 3.11.16\n",
                "ollama_version": "ollama version is 0.32.14\n",
                "ollama_list": "NAME ID SIZE MODIFIED\nqwen3.5:4b abc 3.4 GB 1 day ago\n",
                "ollama_show_0": "Model\n  quantization        Q4_K_M\n",
                "resource_uptime": "load averages: 1.0 1.1 1.2\n",
                "resource_memory": "System-wide memory free percentage: 50%\n",
            }
        )

        inventory = collect_inventory(self.context, runner=runner, sample_interval=0)

        required = {
            "schema_version", "inventory_id", "collected_at", "classification",
            "workspace", "hermes", "ollama", "machine", "storage",
            "resource_observations", "luna_observation", "schedulers",
            "browser_automation", "isolation", "unknowns", "warnings", "source_commands",
        }
        self.assertTrue(required.issubset(inventory))
        flattened = [" ".join(argv) for argv in runner.argv]
        self.assertIn("/Users/mikedemott/.local/bin/hermes --version", flattened)
        self.assertFalse(any("ollama run" in command for command in flattened))
        self.assertFalse(any(" -z " in f" {command} " for command in flattened))

    def test_required_command_failure_is_explicit(self) -> None:
        """Catches a required inventory failure being silently published."""
        class RequiredFailureRunner(RecordingRunner):
            def __call__(self, spec):
                result = super().__call__(spec)
                if spec.name == "ollama_list":
                    return CommandResult(spec.name, "blocked", None, "", "permission denied", 0.01)
                return result

        inventory = collect_inventory(self.context, runner=RequiredFailureRunner(), sample_interval=0)

        self.assertIn("ollama_list", inventory["required_sections_blocked"])
        self.assertIsNone(inventory["ollama"]["installed_models"]["value"])

    def test_verified_isolation_is_bound_into_inventory(self) -> None:
        """Catches live isolation evidence being dropped from canonical publication."""
        isolation = {
            "status": "available",
            "inside_write_allowed": True,
            "tradingbot_write_denied": True,
            "probe_hash_unchanged": True,
            "git_status_unchanged": True,
        }

        inventory = collect_inventory(
            self.context,
            runner=RecordingRunner(),
            sample_interval=0,
            isolation_verdict=isolation,
        )

        self.assertEqual(inventory["isolation"], isolation)
        self.assertNotIn("write isolation not observed", inventory["unknowns"])


if __name__ == "__main__":
    unittest.main()
