"""Behaviour contract for the offline profile-readiness audit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "audit_profile_readiness", Path(__file__).resolve().parents[2] / "scripts" / "audit_profile_readiness.py"
)
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)

RECEIPT = {"model": "Qwen3.5-4B-Q6_K", "provider": "llamacpp", "reasoning_effort": "none", "score": 5}


def _profile(root: Path, name: str, cfg: dict, role: dict | None = None) -> None:
    d = root / "profiles" / name
    d.mkdir(parents=True)
    (d / "config.yaml").write_text(yaml.safe_dump(cfg))
    if role is not None:
        (d / "federation_role.json").write_text(json.dumps(role))


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    (home / "models").mkdir(parents=True)
    (home / "models" / "Qwen3.5-4B-Q6_K.gguf").write_bytes(b"")
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "q.json").write_text(json.dumps(RECEIPT))
    return home, receipts


def _kinds(report: dict, profile: str) -> set[str]:
    row = next(r for r in report["rows"] if r["profile"] == profile)
    return {f.split(" ", 1)[0] for f in row["findings"]}


def test_clean_local_fallback_with_matching_receipt_has_no_findings(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "ok", {
        "model": {"provider": "nvidia", "default": "z-ai/glm-5.3-flash"},
        "fallback_providers": [{"provider": "llamacpp", "model": "Qwen3.5-4B-Q6_K", "reasoning_effort": "none"}],
    })
    assert audit.audit_home(home, receipts)["rows"][0]["findings"] == []


def test_receipt_for_other_effort_does_not_qualify_local_route(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {
        "model": {"provider": "llamacpp", "default": "Qwen3.5-4B-Q6_K", "reasoning_effort": "medium"},
    })
    assert "LOCAL_UNQUALIFIED" in _kinds(audit.audit_home(home, receipts), "p")


def test_missing_gguf_and_ollama_pin_and_paid_route_are_flagged(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {
        "model": {"provider": "openai-codex", "default": "gpt-6-luna"},
        "fallback_providers": [
            {"provider": "llamacpp", "model": "Gone-Q4", "reasoning_effort": "none"},
            {"provider": "ollama-launch", "model": "qwen3.5:4b"},
        ],
    })
    assert {"PAID_ROUTE", "LOCAL_MODEL_MISSING", "OLLAMA_PIN"} <= _kinds(audit.audit_home(home, receipts), "p")


def test_free_suffix_is_not_paid():
    assert audit.classify("nous", "x:free") == "free-cloud"
    assert audit.classify("openrouter", "x:free") == "free-cloud"
    assert audit.classify("openrouter", "x") == "paid-cloud"


def test_manifest_contradicting_config_is_flagged(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(
        home, "p",
        {"model": {"provider": "nvidia", "default": "z-ai/glm-5.3-flash"}},
        {"model_policy": {
            "primary": {"provider": "openai-codex", "model": "gpt-5.6-terra"},
            "auxiliary": {"compression": {"provider": "ollama-launch", "model": "qwen3.5:4b"}},
        }, "toolsets": ["file"]},
    )
    kinds = _kinds(audit.audit_home(home, receipts), "p")
    assert {"PIN_CONTRADICTION", "MANIFEST_OLLAMA_PIN", "MANIFEST_PAID_PRIMARY"} <= kinds


def test_tool_heavy_toolset_on_local_route_is_flagged(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {
        "model": {"provider": "llamacpp", "default": "Qwen3.5-4B-Q6_K", "reasoning_effort": "none"},
        "toolsets": ["terminal", "computer_use"],
    })
    assert "LOCAL_TOOL_RISK" in _kinds(audit.audit_home(home, receipts), "p")


def test_audit_is_read_only(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {"model": {"provider": "nvidia", "default": "m"}})
    before = {p: p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
    audit.audit_home(home, receipts)
    assert before == {p: p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
