from pathlib import Path
import unittest

from hermes_revenue_lab.inventory.types import InventoryContext, Observation


class EvidenceTypesTest(unittest.TestCase):
    def test_unknown_numeric_value_stays_none(self) -> None:
        """Catches unavailable numeric evidence being coerced to zero."""
        observation = Observation.unavailable("swap_bytes", "command blocked")

        self.assertIsNone(observation.value)
        self.assertEqual("unavailable", observation.status)

    def test_context_rejects_workspace_inside_tradingbot(self) -> None:
        """Catches accidental Revenue Lab placement under TradingBotV18."""
        with self.assertRaisesRegex(ValueError, "outside TradingBotV18"):
            InventoryContext(
                workspace=Path("/Users/mikedemott/TradingBotV18/HermesRevenueLab"),
                hermes_home=Path(
                    "/Users/mikedemott/TradingBotV18/HermesRevenueLab/.hermes"
                ),
                tradingbot_path=Path("/Users/mikedemott/TradingBotV18"),
            )


if __name__ == "__main__":
    unittest.main()
