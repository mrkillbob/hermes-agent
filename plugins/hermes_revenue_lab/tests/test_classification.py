import unittest

from hermes_revenue_lab.inventory.classification import classify_resource_state


class ClassificationTest(unittest.TestCase):
    def test_loaded_model_forces_busy(self) -> None:
        """Catches heavy loaded inference being labeled idle."""
        verdict = classify_resource_state(
            [{"luna_count": 0, "loaded_models": 1, "load_1m": 0.5, "memory_free_percent": 50.0}]
        )
        self.assertEqual("observed_busy", verdict["classification"])
        self.assertIn("ollama_model_loaded", verdict["reasons"])

    def test_active_luna_forces_busy(self) -> None:
        """Catches a quiet host overriding positive Luna process evidence."""
        verdict = classify_resource_state(
            [{"luna_count": 1, "loaded_models": 0, "load_1m": 0.5, "memory_free_percent": 50.0}]
        )
        self.assertEqual("observed_busy", verdict["classification"])
        self.assertIn("luna_active", verdict["reasons"])

    def test_missing_quiet_window_keeps_idle_unavailable(self) -> None:
        """Catches missing load evidence being treated as zero."""
        verdict = classify_resource_state(
            [{"luna_count": 0, "loaded_models": 0, "load_1m": None, "memory_free_percent": 50.0}]
        )
        self.assertEqual("not_observed", verdict["classification"])
        self.assertEqual("unavailable", verdict["idle_baseline"]["status"])

    def test_three_quiet_samples_establish_idle(self) -> None:
        """Catches a valid quiet window never becoming available."""
        samples = [
            {"luna_count": 0, "loaded_models": 0, "load_1m": 1.0, "memory_free_percent": 50.0, "revenue_lab_workers": 0}
            for _ in range(3)
        ]
        verdict = classify_resource_state(samples)
        self.assertEqual("observed_idle", verdict["classification"])
        self.assertEqual("available", verdict["idle_baseline"]["status"])


if __name__ == "__main__":
    unittest.main()
