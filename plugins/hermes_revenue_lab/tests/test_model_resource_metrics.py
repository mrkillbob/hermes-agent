from __future__ import annotations

import unittest
from unittest.mock import patch

from hermes_revenue_lab.inventory.types import CommandResult
from hermes_revenue_lab.models.resource_metrics import (
    ResourceSample,
    collect_resource_sample,
    measure_resource_call,
    parse_ollama_process_resources,
    summarize_resource_samples,
)


class ModelResourceMetricsTest(unittest.TestCase):
    def test_sampler_targets_exact_ollama_pids_before_process_collection(self) -> None:
        """Catches full process-table truncation hiding a high-PID inference runner."""
        def command_result(spec):
            outputs = {
                "ollama_pids": "2043\n",
                "llama_server_pids": "18621\n",
                "uptime": "00:00 up 1 day, load averages: 2.00 1.00 1.00\n",
                "memory": "System-wide memory free percentage: 75%\n",
                "swap": "vm.swapusage: total = 0.00M used = 0.00M free = 0.00M\n",
            }
            if spec.name == "processes":
                stdout = (
                    "18621 2043 91.5 123456 "
                    "/Applications/Ollama.app/Contents/Resources/llama-server --model /tmp/blob\n"
                    if "-p" in spec.argv
                    else ""
                )
            else:
                stdout = outputs[spec.name]
            return CommandResult(spec.name, "available", 0, stdout, "", 0.01)

        with patch(
            "hermes_revenue_lab.models.resource_metrics.run_command",
            side_effect=command_result,
        ):
            sample = collect_resource_sample()

        self.assertEqual(1, sample.ollama_runner_count)
        self.assertEqual(123456 * 1024, sample.ollama_rss_bytes)

    def test_process_parser_recognizes_current_ollama_llama_server(self) -> None:
        """Catches current Ollama inference being published as zero RSS and CPU."""
        text = (
            "  18621 2043 91.5 123456 "
            "/Applications/Ollama.app/Contents/Resources/llama-server --model /tmp/blob\n"
        )

        result = parse_ollama_process_resources(text)

        self.assertEqual(1, result["runner_count"])
        self.assertEqual(123456 * 1024, result["rss_bytes"])
        self.assertEqual(91.5, result["cpu_percent"])

    def test_process_parser_aggregates_only_ollama_runner(self) -> None:
        text = """\
  100 1 20.5 100000 /usr/local/bin/ollama serve
  101 100 80.0 200000 /tmp/ollama runner --model first
  102 100 50.0 300000 /tmp/ollama runner --model second
  103 1 99.0 999999 /bin/zsh mention ollama runner
"""
        result = parse_ollama_process_resources(text)
        self.assertEqual(result["runner_count"], 2)
        self.assertEqual(result["rss_bytes"], 500000 * 1024)
        self.assertEqual(result["cpu_percent"], 130.0)

    def test_summary_preserves_before_peak_after_and_unknown_gpu(self) -> None:
        samples = [
            ResourceSample(1.0, 2.0, 60.0, 100, 1000, 1, 10.0, 1000),
            ResourceSample(2.0, 4.0, 50.0, 300, 1000, 1, 80.0, 5000),
            ResourceSample(3.0, 3.0, 55.0, 200, 1000, 1, 20.0, 2000),
        ]
        summary = summarize_resource_samples(samples)
        self.assertEqual(summary["peak_ollama_rss_bytes"], 5000)
        self.assertEqual(summary["peak_ollama_cpu_percent"], 80.0)
        self.assertEqual(summary["swap_used_before_bytes"], 100)
        self.assertEqual(summary["swap_used_peak_bytes"], 300)
        self.assertEqual(summary["swap_used_after_bytes"], 200)
        self.assertIsNone(summary["gpu_pressure"])
        self.assertEqual(summary["gpu_pressure_reason"], "trusted sampler unavailable")

    def test_measurement_returns_callback_result_and_samples_boundaries(self) -> None:
        sequence = iter(
            [
                ResourceSample(1.0, 2.0, 60.0, 100, 1000, 0, 0.0, 0),
                ResourceSample(2.0, 3.0, 55.0, 200, 1000, 1, 40.0, 4000),
            ]
        )
        result, metrics = measure_resource_call(
            lambda: "result",
            sample_provider=lambda: next(sequence),
            interval_seconds=60.0,
        )
        self.assertEqual(result, "result")
        self.assertEqual(metrics["peak_ollama_rss_bytes"], 4000)
        self.assertEqual(metrics["sample_count"], 2)


if __name__ == "__main__":
    unittest.main()
