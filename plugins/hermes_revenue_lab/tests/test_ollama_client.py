from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from hermes_revenue_lab.models.corpus import benchmark_corpus
from hermes_revenue_lab.models.ollama_client import (
    OLLAMA_CHAT_URL,
    OllamaClientError,
    run_ollama_task,
)


def corpus_task(family: str):
    return next(item for item in benchmark_corpus() if item.family == family)


class OllamaClientTest(unittest.TestCase):
    @patch("hermes_revenue_lab.models.ollama_client.time.monotonic")
    @patch("hermes_revenue_lab.models.ollama_client.urllib.request.urlopen")
    def test_streaming_request_disables_thinking_and_measures_response(
        self, urlopen, monotonic
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__iter__.return_value = iter(
            [
                b'{"message":{"content":"{\\"escalate\\":true,"},"done":false}\n',
                b'{"message":{"content":"\\"reason_codes\\":[\\"policy_conflict\\",\\"publication_action\\"]}"},'
                b'"done":true,"prompt_eval_count":12,"eval_count":8,"load_duration":1000000,'
                b'"prompt_eval_duration":2000000,"eval_duration":400000000,"total_duration":500000000}\n',
            ]
        )
        urlopen.return_value = response
        monotonic.side_effect = [10.0, 10.2, 10.8]

        result = run_ollama_task("qwen3.5:4b", corpus_task("decide_escalation"), timeout=60)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, OLLAMA_CHAT_URL)
        self.assertEqual(payload["model"], "qwen3.5:4b")
        self.assertTrue(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertFalse(payload["options"]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(0, payload["options"].get("temperature"))
        self.assertEqual(7, payload["options"].get("seed"))
        self.assertEqual(result.time_to_first_token_seconds, 0.2)
        self.assertEqual(result.wall_time_seconds, 0.8)
        self.assertEqual(result.tokens_per_second, 20.0)
        self.assertEqual(result.eval_count, 8)
        self.assertIn('"escalate":true', result.response_text)

    @patch("hermes_revenue_lab.models.ollama_client.urllib.request.urlopen")
    def test_tool_schema_is_sent_without_executing_tool(self, urlopen) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__iter__.return_value = iter(
            [
                b'{"message":{"content":"","tool_calls":[{"function":{"name":"store_candidate",'
                b'"arguments":{"candidate_id":"C-17","score":4}}}]},"done":true,'
                b'"eval_count":1,"eval_duration":100000000}\n'
            ]
        )
        urlopen.return_value = response

        result = run_ollama_task("qwen3.5:4b", corpus_task("select_tool"))

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "store_candidate")
        self.assertNotIn("format", payload)
        self.assertIn("Emit exactly one declared tool call", payload["messages"][1]["content"])
        self.assertNotIn("Return only JSON", payload["messages"][1]["content"])
        self.assertEqual(result.tool_call["name"], "store_candidate")
        self.assertEqual(result.tool_call["arguments"]["score"], 4)

    @patch("hermes_revenue_lab.models.ollama_client.urllib.request.urlopen")
    def test_incomplete_stream_fails_without_retry(self, urlopen) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__iter__.return_value = iter([b'{"message":{"content":"partial"},"done":false}\n'])
        urlopen.return_value = response

        with self.assertRaisesRegex(OllamaClientError, "without a terminal"):
            run_ollama_task("qwen3.5:4b", corpus_task("classify_opportunities"))

        self.assertEqual(urlopen.call_count, 1)

    @patch("hermes_revenue_lab.models.ollama_client.urllib.request.urlopen")
    def test_disabled_thinking_rejects_thinking_channel(self, urlopen) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__iter__.return_value = iter(
            [b'{"message":{"thinking":"hidden chain","content":"{}"},"done":true}\n']
        )
        urlopen.return_value = response

        with self.assertRaisesRegex(OllamaClientError, "thinking was disabled"):
            run_ollama_task("qwen3.5:4b", corpus_task("decide_escalation"))


if __name__ == "__main__":
    unittest.main()
