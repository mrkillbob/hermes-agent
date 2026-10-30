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
    (d / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    if role is not None:
        (d / "federation_role.json").write_text(json.dumps(role), encoding="utf-8")


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    (home / "models").mkdir(parents=True)
    (home / "models" / "Qwen3.5-4B-Q6_K.gguf").write_bytes(b"")
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "q.json").write_text(json.dumps(RECEIPT), encoding="utf-8")
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


def test_auxiliary_routes_are_audited(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {
        "model": {"provider": "nvidia", "default": "m"},
        "auxiliary": {"compression": {"provider": "ollama-launch", "model": "qwen3.5:4b"}, "transient_retries": 1},
    })
    assert "OLLAMA_PIN" in _kinds(audit.audit_home(home, receipts), "p")


def test_primary_effort_is_read_from_agent_config(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {
        "model": {"provider": "llamacpp", "default": "Qwen3.5-4B-Q6_K"},
        "agent": {"reasoning_effort": "none"},
    })
    assert "LOCAL_UNQUALIFIED" not in _kinds(audit.audit_home(home, receipts), "p")


def test_missing_models_directory_reports_every_local_model_missing(tmp_path):
    home, receipts = _home(tmp_path)
    for f in (home / "models").iterdir():
        f.unlink()
    (home / "models").rmdir()
    _profile(home, "p", {"model": {"provider": "llamacpp", "default": "Qwen3.5-4B-Q6_K", "reasoning_effort": "none"}})
    assert "LOCAL_MODEL_MISSING" in _kinds(audit.audit_home(home, receipts), "p")


def test_only_free_nous_models_are_unmetered():
    assert audit.classify("nous", "x:free") == "free-cloud"
    assert audit.classify("nous", "hermes-4-405b") == "paid-cloud"


def test_split_gguf_counts_only_when_every_part_is_staged(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "Big-00001-of-00003.gguf").write_bytes(b"")
    (models / "Big-00003-of-00003.gguf").write_bytes(b"")
    assert not audit.model_on_disk(models, "Big")
    (models / "Big-00002-of-00003.gguf").write_bytes(b"")
    assert audit.model_on_disk(models, "Big")
    assert not audit.model_on_disk(models, "Bi")


def test_auxiliary_fallback_chain_entries_are_audited(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {
        "model": {"provider": "nvidia", "default": "m"},
        "auxiliary": {"compression": {
            "provider": "nvidia", "model": "m",
            "fallback_chain": [
                {"provider": "openai-codex", "model": "gpt-6-luna"},
                {"provider": "ollama-launch", "model": "qwen3.5:4b"},
                "not-a-route",
            ],
        }},
    })
    assert {"PAID_ROUTE", "OLLAMA_PIN"} <= _kinds(audit.audit_home(home, receipts), "p")


def test_legacy_fallback_model_and_single_mapping_are_audited(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "legacy", {
        "model": {"provider": "nvidia", "default": "m"},
        "fallback_model": {"provider": "openai-codex", "model": "gpt-6-luna"},
    })
    _profile(home, "mapping", {
        "model": {"provider": "nvidia", "default": "m"},
        "fallback_providers": {"provider": "ollama-launch", "model": "qwen3.5:4b"},
    })
    report = audit.audit_home(home, receipts)
    assert "PAID_ROUTE" in _kinds(report, "legacy")
    assert "OLLAMA_PIN" in _kinds(report, "mapping")


def test_duplicate_fallback_across_keys_is_reported_once(tmp_path):
    home, receipts = _home(tmp_path)
    dup = {"provider": "ollama-launch", "model": "qwen3.5:4b"}
    _profile(home, "p", {
        "model": {"provider": "nvidia", "default": "m"},
        "fallback_providers": [dup], "fallback_model": dup,
    })
    row = next(r for r in audit.audit_home(home, receipts)["rows"] if r["profile"] == "p")
    assert sum(f.startswith("OLLAMA_PIN") for f in row["findings"]) == 1


def test_split_gguf_with_punctuated_model_id_is_found(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    for i in (1, 2):
        (models / f"Qwen3.8-Flash-Next-UD-IQ4_XS-{i:05d}-of-00002.gguf").write_bytes(b"")
    (models / "Odd[1]-00001-of-00001.gguf").write_bytes(b"")
    assert audit.model_on_disk(models, "Qwen3.8-Flash-Next-UD-IQ4_XS")
    assert audit.model_on_disk(models, "Odd[1]")
    assert not audit.model_on_disk(models, "Qwen3.8-Flash")


def test_provider_aliases_resolve_to_billing_class():
    assert audit.classify("chatgpt", "gpt-6-luna") == "paid-cloud"
    assert audit.classify("claude", "x") == "paid-cloud"
    assert audit.classify("nim", "m") == "free-cloud"


def test_root_level_provider_with_string_model_is_audited(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {"model": "Not-Staged-Q4", "provider": "llamacpp"})
    assert {"LOCAL_MODEL_MISSING", "LOCAL_UNQUALIFIED"} <= _kinds(audit.audit_home(home, receipts), "p")


def test_model_name_alias_is_read_as_default(tmp_path):
    home, receipts = _home(tmp_path)
    _profile(home, "p", {"model": {"provider": "llamacpp", "name": "Gone-Q4"}})
    assert "LOCAL_MODEL_MISSING" in _kinds(audit.audit_home(home, receipts), "p")


def test_unlisted_remote_providers_are_metered_unless_free():
    assert audit.classify("deepseek", "deepseek-flash") == "paid-cloud"
    assert audit.classify("minimax", "m") == "paid-cloud"
    assert audit.classify("some-new-provider", "m:free") == "free-cloud"
    assert audit.classify("custom", "m") == "other-cloud"
