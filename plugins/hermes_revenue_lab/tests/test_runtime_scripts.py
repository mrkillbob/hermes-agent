from __future__ import annotations

import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from plugins.hermes_revenue_lab.scripts.desktop_smoke import (
    ENDPOINT,
    GATEWAY_NAME,
    load_session_token,
    publish_verdict,
    verify_token_auth,
    wait_for_status,
)
from plugins.hermes_revenue_lab.scripts.init_lab_runtime import initialize_runtime


class RuntimeInitializationTest(unittest.TestCase):
    def test_initialization_creates_stable_secret_without_returning_or_printing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hermes_home = Path(directory) / ".hermes"
            output = io.StringIO()
            with redirect_stdout(output):
                first = initialize_runtime(hermes_home)
                second = initialize_runtime(hermes_home)

            env_path = hermes_home / ".env"
            env_text = env_path.read_text(encoding="utf-8")
            token = env_text.removeprefix("HERMES_DASHBOARD_SESSION_TOKEN=").strip()

            self.assertGreaterEqual(len(token), 43)
            self.assertNotIn(token, output.getvalue())
            self.assertNotIn(token, json.dumps(first))
            self.assertNotIn(token, json.dumps(second))
            self.assertEqual(first, second)
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
            self.assertEqual(env_text.count("\n"), 1)
            self.assertEqual(env_text, env_path.read_text(encoding="utf-8"))

    def test_existing_env_with_extra_entries_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hermes_home = Path(directory) / ".hermes"
            hermes_home.mkdir()
            (hermes_home / ".env").write_text(
                "HERMES_DASHBOARD_SESSION_TOKEN=safe\nOTHER=value\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                initialize_runtime(hermes_home)


class DesktopSmokeTest(unittest.TestCase):
    def test_constants_are_fixed_to_dedicated_loopback_gateway(self) -> None:
        self.assertEqual(ENDPOINT, "http://127.0.0.1:9120")
        self.assertEqual(GATEWAY_NAME, "Hermes Revenue Lab")

    def test_load_session_token_requires_single_expected_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("HERMES_DASHBOARD_SESSION_TOKEN=abc123\n", encoding="utf-8")
            self.assertEqual(load_session_token(env_path), "abc123")
            env_path.write_text("OTHER=value\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "session token"):
                load_session_token(env_path)

    @patch("plugins.hermes_revenue_lab.scripts.desktop_smoke.time.sleep", return_value=None)
    @patch("plugins.hermes_revenue_lab.scripts.desktop_smoke.urllib.request.urlopen")
    def test_wait_for_status_uses_only_authenticated_status_endpoint(
        self, urlopen, _sleep
    ) -> None:
        response = unittest.mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        response.read.return_value = b'{"auth_required": true}'
        urlopen.return_value = response

        verdict = wait_for_status(ENDPOINT, "do-not-publish", timeout_seconds=0.1)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{ENDPOINT}/api/status")
        self.assertEqual(request.get_header("Authorization"), "Bearer do-not-publish")
        self.assertEqual(verdict["status"], "available")
        self.assertEqual(verdict["endpoint"], ENDPOINT)
        self.assertNotIn("do-not-publish", json.dumps(verdict))

    def test_publish_verdict_is_atomic_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "desktop_connection_verdict.json"
            verdict = {
                "status": "available",
                "http_status": 200,
                "endpoint": ENDPOINT,
                "gateway_name": GATEWAY_NAME,
                "auth_required": True,
            }
            publish_verdict(verdict, destination)
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload, verdict)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)
            self.assertFalse((destination.parent / f".{destination.name}.tmp").exists())

    @patch("plugins.hermes_revenue_lab.scripts.desktop_smoke.urllib.request.urlopen")
    def test_verify_token_auth_requires_anonymous_401_and_authenticated_200(
        self, urlopen
    ) -> None:
        from urllib.error import HTTPError

        denied = HTTPError(
            f"{ENDPOINT}/api/config", 401, "Unauthorized", hdrs=None, fp=None
        )
        allowed = unittest.mock.MagicMock()
        allowed.status = 200
        allowed.__enter__.return_value = allowed
        urlopen.side_effect = [denied, allowed]

        result = verify_token_auth(ENDPOINT, "do-not-publish")

        anonymous, authenticated = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(anonymous.full_url, f"{ENDPOINT}/api/config")
        self.assertIsNone(anonymous.get_header("X-Hermes-session-token"))
        self.assertEqual(
            dict(authenticated.header_items())["X-hermes-session-token"], "do-not-publish"
        )
        self.assertEqual(result, {"token_auth_verified": True})
        self.assertNotIn("do-not-publish", json.dumps(result))


class RuntimeWrapperTest(unittest.TestCase):
    def test_wrapper_is_executable_and_contains_fixed_safe_runtime(self) -> None:
        wrapper = Path(__file__).parents[1] / "scripts" / "hermes-revenue-lab"
        text = wrapper.read_text(encoding="utf-8")
        mode = stat.S_IMODE(wrapper.stat().st_mode)
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertIn("HERMES_HOME=\"$LAB_ROOT/.hermes\"", text)
        self.assertIn("HERMES_WRITE_SAFE_ROOT=\"$LAB_ROOT\"", text)
        self.assertIn("127.0.0.1", text)
        self.assertIn("9120", text)
        self.assertIn("sandbox-exec", text)
        self.assertNotIn("ollama", text.lower())


if __name__ == "__main__":
    unittest.main()
