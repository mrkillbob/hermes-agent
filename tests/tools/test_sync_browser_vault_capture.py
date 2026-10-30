"""Keep the fork's vault-filled task capture floor after the browser refactor."""
import json
import shlex

import pytest

from tests.tools.test_browser_use_cli import (
    _clean_env, _fake_cli, _fake_managed_chromium, _fake_supervisor_registry,
)

__all__ = ["_clean_env", "_fake_managed_chromium", "_fake_supervisor_registry"]


@pytest.mark.parametrize("native", [False, True])
def test_vault_filled_task_returns_neither_image_nor_screenshot_path(tmp_path, monkeypatch, native):
    from tools import browser_use_cli as browser, browser_vault_tool as vault

    monkeypatch.setattr(vault, "_VAULT_FILLED_SESSIONS", set())
    vault._mark_vault_filled("filled-task")
    shot = tmp_path / "capture.png"
    cli = _fake_cli(tmp_path, f"cat > /dev/null\ntouch {shlex.quote(str(shot))}\necho {shlex.quote(str(shot))}\n")
    monkeypatch.setattr(browser, "_find_cli", lambda: [cli])
    monkeypatch.setattr("tools.vision_tools._should_use_native_vision_fast_path", lambda: native)
    monkeypatch.setattr("tools.vision_tools._resize_image_for_vision", lambda *args, **kwargs: "data:image/png;base64,QUJD")

    blocked = browser.browser_exec("print(capture_screenshot())", task_id="filled-task", session="named")
    assert isinstance(blocked, str)
    payload = json.loads(blocked)
    assert "blocked" in payload["error"].lower()
    assert str(shot) not in blocked
    assert "_multimodal" not in payload and "screenshot_path" not in payload

    # A separate task still receives its own normal screenshot result.
    allowed = browser.browser_exec("print(capture_screenshot())", task_id="other-task", session="named")
    if native:
        assert allowed["_multimodal"] is True
        assert allowed["meta"]["screenshot_path"] == str(shot)
    else:
        assert json.loads(allowed)["screenshot_path"] == str(shot)
