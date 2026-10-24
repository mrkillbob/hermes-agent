"""Provider setup delegates package policy to PM and external server owners."""

import os
import sys
from types import ModuleType
from unittest.mock import patch

import pm
import pytest

from hermes_cli.tools_config_post_setup import _run_post_setup


@pytest.mark.platforms('linux')
@pytest.mark.parametrize('provider,docker_image,playwright', [
    ('browserbase', False, False), ('chromium', False, True),
    ('chromium', False, False), ('chromium', True, False),
])
def test_browser_setup_respects_provider_and_native_owner(monkeypatch, capsys, tmp_path, provider, docker_image,
                                                          playwright):
    baked = tmp_path / 'chromium-executable-path'
    if docker_image:
        baked.write_text('/opt/hermes/chromium/chrome', encoding='utf-8')
    found = iter([None, None if docker_image else '/pm/chromium/chrome'])
    with (
        patch('tools.browser_use_cli._find_cli', return_value=['harness']) as install,
        patch('hermes_cli.browser_runtime.BAKED_EXECUTABLE_FILE', baked),
        patch('hermes_cli.browser_runtime.chromium_executable', side_effect=lambda **_kw: next(found)) as chromium,
        patch('hermes_constants.is_termux', return_value=False),
        patch('hermes_platform.host.runtime.is_termux', return_value=False),
        patch('importlib.util.find_spec', return_value=object() if playwright else None),
        patch('shutil.which', return_value='/usr/bin/apt-get'),
        patch('pm.ensure') as ensure, patch('subprocess.run') as run,
    ):
        before = dict(os.environ)
        _run_post_setup(provider)
        assert dict(os.environ) == before
    install.assert_called_once()
    run.assert_not_called()
    if provider == 'browserbase':
        chromium.assert_not_called()
    if provider == 'browserbase' or docker_image:
        ensure.assert_not_called()
    else:
        ensure.assert_called_once_with('chromium', explicit=True)
    output = capsys.readouterr().out
    if docker_image:
        assert 'image' in output
    elif provider == 'chromium':
        assert '/pm/chromium/chrome' in output
        assert 'system' in output and 'npx' not in output
        if playwright:
            assert sys.executable in output and '-m playwright install-deps chromium' in output
        else:
            assert 'https://playwright.dev/python/docs/browsers#install-system-dependencies' in output


@pytest.mark.parametrize('harness,chromium,ready', [
    (True, '/pm/chromium/chrome', True), (True, None, False), (False, '/pm/chromium/chrome', False),
])
def test_local_browser_row_ready_only_when_setup_would_be_a_noop(harness, chromium, ready):
    from hermes_cli.tools_config_post_setup import _POST_SETUP_READY

    with (
        patch('tools.browser_use_cli._find_cli', return_value=['harness'] if harness else None),
        patch('hermes_cli.browser_runtime.chromium_executable', return_value=chromium),
    ):
        assert _POST_SETUP_READY['chromium']() is ready
        # Cloud rows need only browser_exec's engine: the vendor hosts Chromium.
        assert _POST_SETUP_READY['browserbase']() is harness


@pytest.mark.parametrize("key,extra", [
    ("ddgs", "ddgs"), ("faster_whisper", "stt-whisper"),
    ("kittentts", "kittentts"), ("piper", "piper"),
])
@pytest.mark.parametrize("refused", [False, True])
def test_importable_sdk_does_not_bypass_pm_constraints(monkeypatch, capsys, key, extra, refused):
    sdk = ModuleType(key)
    sdk.__version__ = "0.0.0"
    monkeypatch.setitem(sys.modules, key, sdk)
    error = pm.InstallError("venv", "outside frozen feature set") if refused else None
    with patch("pm.sync_venv", side_effect=error) as sync:
        _run_post_setup(key)

    sync.assert_called_once_with([extra], explicit=True)
    output = capsys.readouterr().out
    if refused:
        assert "outside frozen feature set" in output
        assert "Retry with: hermes tools" in output
        assert "Restart Hermes" not in output
    else:
        assert "Restart Hermes" in output


@pytest.mark.parametrize("failure", [None, "sdk", "admission"])
def test_langfuse_setup_uses_plugin_admission_and_preserves_config_on_refusal(
    monkeypatch, tmp_path, capsys, failure,
):
    from hermes_cli.config import get_hermes_home, read_raw_config

    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setitem(sys.modules, "langfuse", ModuleType("langfuse"))
    config_path = get_hermes_home() / "config.yaml"
    config_path.write_text(
        "plugins:\n  enabled: [other]\n  disabled: [langfuse, observability/langfuse]\n",
        encoding="utf-8",
    )
    before = config_path.read_bytes()

    def resolve_candidate(**kwargs):
        assert kwargs["explicit"] is True
        assert kwargs["plugins"].data["enabled"]
        if failure == "admission":
            raise pm.InstallError("venv", "candidate refused")
        # The real admission publisher must commit both lists, not a second UI writer.
        from pm.publication import PluginSelection
        from pm.paths import repo_root
        from hermes_cli.runtime_state import runtime_lock, finish_publication
        with runtime_lock(repo_root()):
            PluginSelection(dict(kwargs["plugins"].data)).publish(repo_root())
            finish_publication(repo_root())

    with (
        patch("pm.sync_venv", side_effect=pm.InstallError("venv", "SDK refused") if failure == "sdk" else None) as sdk,
        patch("pm.client.sync_venv", side_effect=resolve_candidate) as admission,
    ):
        _run_post_setup("langfuse")

    sdk.assert_called_once_with(["langfuse"], explicit=True)
    if failure == "sdk":
        admission.assert_not_called()
    else:
        admission.assert_called_once()
    if failure:
        assert config_path.read_bytes() == before
        output = capsys.readouterr().out
        assert "refused" in output
        if failure == "sdk":
            assert "Retry with: hermes tools" in output
    else:
        plugin_config = read_raw_config()["plugins"]
        assert set(plugin_config["enabled"]) == {"other", "observability/langfuse"}
        assert plugin_config["disabled"] == []
