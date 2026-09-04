import unittest

from hermes_revenue_lab.inventory.parsers import (
    parse_df,
    parse_hardware,
    parse_hermes_cron,
    parse_hermes_profiles,
    parse_hermes_tools,
    parse_hermes_version,
    parse_ollama_list,
    parse_ollama_ps,
    parse_ollama_show,
    parse_process_table,
    parse_vm_stat,
)


class ParserTest(unittest.TestCase):
    def test_hardware_parser_excludes_identifiers(self) -> None:
        """Catches serial and UUID fields leaking from system_profiler."""
        parsed = parse_hardware(
            """Model Name: Mac mini
Model Identifier: Mac16,11
Chip: Apple M4 Pro
Total Number of Cores: 12 (8 Performance and 4 Efficiency)
Memory: 64 GB
Serial Number (system): REDACTME
Hardware UUID: REDACTME
Provisioning UDID: REDACTME
"""
        )

        self.assertEqual("Mac mini", parsed["model_name"])
        self.assertEqual(12, parsed["total_cores"])
        self.assertEqual(8, parsed["performance_cores"])
        self.assertEqual(4, parsed["efficiency_cores"])
        self.assertNotIn("serial_number", parsed)
        self.assertNotIn("hardware_uuid", parsed)
        self.assertNotIn("provisioning_udid", parsed)

    def test_ollama_list_preserves_size_text_and_digest(self) -> None:
        """Catches model identity or size being lost while parsing columns."""
        rows = parse_ollama_list(
            "NAME ID SIZE MODIFIED\nqwen3.5:4b abc123 3.4 GB 5 days ago\n"
        )

        self.assertEqual(
            [{"name": "qwen3.5:4b", "digest": "abc123", "size": "3.4 GB"}], rows
        )

    def test_ollama_show_extracts_quantization_and_capabilities(self) -> None:
        """Catches routing-critical model metadata being omitted."""
        parsed = parse_ollama_show(
            """  Model
    architecture        qwen35
    parameters          4.7B
    context length      262144
    quantization        Q4_K_M

  Capabilities
    completion
    vision
    tools
    thinking
"""
        )

        self.assertEqual("Q4_K_M", parsed["quantization"])
        self.assertEqual(262144, parsed["context_length"])
        self.assertEqual(["completion", "vision", "tools", "thinking"], parsed["capabilities"])

    def test_ollama_ps_reports_loaded_resource_metadata(self) -> None:
        """Catches a loaded heavy model being mistaken for an idle Ollama server."""
        rows = parse_ollama_ps(
            "NAME ID SIZE PROCESSOR CONTEXT UNTIL\n"
            "qwen3-coder:30b 06c1097 44 GB 100% GPU 262144 4 minutes from now\n"
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("44 GB", rows[0]["size"])
        self.assertEqual("100% GPU", rows[0]["processor"])
        self.assertEqual(262144, rows[0]["context_length"])

    def test_process_parser_returns_aggregates_not_commands(self) -> None:
        """Catches full process command lines leaking into inventory artifacts."""
        parsed = parse_process_table(
            "1 0 2.0 0.1 1024 00:10 python3 /x/TradingBotV18/main.py\n"
            "2 0 3.0 0.2 2048 00:20 /Applications/Ollama.app/ollama serve\n"
            "3 0 1.0 0.1 512 00:01 rg -i TradingBotV18|live_runner\n"
        )

        self.assertEqual(1, parsed["luna"]["count"])
        self.assertEqual(1024 * 1024, parsed["luna"]["rss_bytes"])
        self.assertEqual(1, parsed["ollama"]["count"])
        self.assertNotIn("command", parsed["luna"])

    def test_hermes_version_is_allowlisted(self) -> None:
        """Catches support metadata being coupled to unneeded status output."""
        parsed = parse_hermes_version(
            "Hermes Agent v0.20.4 (2026.8.18) · upstream e69b8e56\n"
            "Install directory: /Users/mikedemott/.hermes/hermes-agent\n"
            "Install method: git\nPython: 3.11.16\n"
        )

        self.assertEqual("0.20.4", parsed["version"])
        self.assertEqual("e69b8e56", parsed["upstream_revision"])
        self.assertEqual("git", parsed["install_method"])

    def test_hermes_tools_returns_names_and_states_only(self) -> None:
        """Catches tool inventory parsing unrelated descriptive text."""
        parsed = parse_hermes_tools(
            "  ✓ enabled  web  Web Search\n  ✗ disabled  video  Video Analysis\n"
        )

        self.assertEqual(
            [{"name": "web", "enabled": True}, {"name": "video", "enabled": False}],
            parsed,
        )

    def test_hermes_profiles_returns_profile_and_model_only(self) -> None:
        """Catches profile parsing pulling credentials or descriptions."""
        parsed = parse_hermes_profiles(
            "◆default qwen3-coder:30b stopped — —\n"
            "research-scout qwen3.5:4b stopped research-scout —\n"
        )

        self.assertEqual("default", parsed[0]["name"])
        self.assertEqual("qwen3-coder:30b", parsed[0]["model"])
        self.assertEqual("research-scout", parsed[1]["name"])

    def test_hermes_cron_does_not_capture_prompt_body(self) -> None:
        """Catches scheduled prompt text entering the public inventory."""
        parsed = parse_hermes_cron(
            "  abc123 [active]\n"
            "    Name:      Luna read-only audit\n"
            "    Schedule:  30 13 * * 1-5\n"
            "    Workdir:   /Users/mikedemott/TradingBotV18\n"
            "    Prompt:    secret internal instructions\n"
        )

        self.assertEqual("abc123", parsed[0]["id"])
        self.assertEqual("active", parsed[0]["status"])
        self.assertNotIn("prompt", parsed[0])
        self.assertNotIn("secret internal instructions", repr(parsed))

    def test_df_parser_uses_integer_bytes(self) -> None:
        """Catches storage arithmetic being inferred from formatted percentages."""
        parsed = parse_df(
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/disk3s5 1000 750 250 75% /System/Volumes/Data\n"
        )

        self.assertEqual(1_024_000, parsed["total_bytes"])
        self.assertEqual(256_000, parsed["available_bytes"])

    def test_vm_stat_parser_uses_reported_page_size(self) -> None:
        """Catches VM page counts being mistaken for bytes."""
        parsed = parse_vm_stat(
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free: 10.\nPages active: 20.\nPages occupied by compressor: 3.\n"
        )

        self.assertEqual(163_840, parsed["free_bytes"])
        self.assertEqual(49_152, parsed["compressed_bytes"])


if __name__ == "__main__":
    unittest.main()
