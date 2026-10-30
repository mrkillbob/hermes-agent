#!/usr/bin/env python3
"""Offline, read-only audit of Hermes profile model routing.

Walks ``<home>/config.yaml`` and ``<home>/profiles/*/`` and reports, per profile, the
effective route plus findings: Ollama pins, paid-provider routes, config/manifest
divergence, local models missing on disk, and local routes with no matching benchmark
receipt. Never writes, never imports Hermes runtime code, never touches the network.

    python scripts/audit_profile_readiness.py --home ~/.hermes [--receipts DIR] [--json]
"""

from __future__ import annotations

import argparse
import re
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

LLAMACPP = frozenset({"llamacpp", "llama.cpp", "llama-cpp", "local"})
OLLAMA = frozenset({"ollama", "ollama-launch"})
# Fail closed: any remote provider is metered unless it is listed here or the model is ``:free``.
# A denylist of paid providers would silently pass every provider it forgot.
FREE_CLOUD = frozenset({"nvidia"})  # NIM entitlement, no per-token charge
# Endpoint decides the cost (custom base_url); not classifiable from provider name alone.
UNCLASSIFIED = frozenset({"", "custom", "auto"})
TOOL_HEAVY = frozenset({"computer_use", "browser", "delegation", "code_execution"})


# Mirrors hermes_cli/local_runtime/gguf.py SPLIT_PART_RE; this script stays free of runtime imports.
_SPLIT_FIRST_PART = re.compile(r"-00001-of-(\d{5})\.gguf$")


def model_on_disk(models_dir: Path, model: str) -> bool:
    """A single ``<model>.gguf``, or a split model with every ``-NNNNN-of-MMMMM`` part present."""
    if (models_dir / f"{model}.gguf").is_file():
        return True
    # Compare the literal prefix; glob would read "[", "?" or "*" in a model id as a pattern.
    for first in models_dir.glob("*-00001-of-*.gguf") if models_dir.is_dir() else ():
        m = _SPLIT_FIRST_PART.search(first.name)
        if m and first.name[: m.start()] == model and all(
            (models_dir / f"{model}-{i:05d}-of-{m.group(1)}.gguf").is_file()
            for i in range(2, int(m.group(1)) + 1)
        ):
            return True
    return False


# Subset of hermes_cli/providers.py _ALIAS_GROUPS that decides a route's billing class; this script
# stays free of runtime imports, so the table is mirrored here.
_PROVIDER_ALIASES = {
    "claude": "anthropic", "claude-code": "anthropic",
    "chatgpt": "openai-codex", "chatgpt-codex": "openai-codex",
    "openai": "openrouter",
    "x-ai": "xai", "x.ai": "xai", "grok": "xai",
    "nim": "nvidia", "nvidia-nim": "nvidia", "build-nvidia": "nvidia", "nemotron": "nvidia",
}


def classify(provider: str, model: str) -> str:
    p = (provider or "").strip().lower()
    p = _PROVIDER_ALIASES.get(p, p)
    if p in LLAMACPP:
        return "local-llamacpp"
    if p in OLLAMA:
        return "ollama"
    if p in FREE_CLOUD or str(model).endswith(":free"):
        return "free-cloud"
    if p in UNCLASSIFIED:
        return "other-cloud"
    return "paid-cloud"


def _route(entry: Any, slot: str) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    model = entry.get("model") or entry.get("default")
    provider = entry.get("provider")
    if not model and not provider:
        return None
    return {
        "slot": slot,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "effort": str(entry.get("reasoning_effort") or ""),
        "class": classify(str(provider or ""), str(model or "")),
    }


def _normalized_model(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Mirror hermes_cli/config.py _normalize_root_model_keys: ``model: <id>`` plus root-level
    ``provider``/``base_url``, and ``model.model`` / ``model.name`` as aliases of ``model.default``."""
    raw = cfg.get("model")
    model = dict(raw) if isinstance(raw, dict) else {"default": raw} if raw else {}
    for key in ("model", "name"):
        if not model.get("default") and isinstance(model.get(key), str):
            model["default"] = model[key]
    for key in ("provider", "base_url"):
        if not model.get(key) and cfg.get(key):
            model[key] = cfg[key]
    return model or None


def config_routes(cfg: dict[str, Any]) -> list[dict[str, str]]:
    routes = [r for r in [_route(_normalized_model(cfg), "primary")] if r]
    # Federation seeding writes the primary effort to agent.reasoning_effort, not into model.
    agent_effort = str((cfg.get("agent") or {}).get("reasoning_effort") or "")
    if routes and not routes[0]["effort"]:
        routes[0]["effort"] = agent_effort
    # Mirrors hermes_cli/fallback_config.get_fallback_chain: both keys, list or single mapping, de-duplicated.
    seen: set[tuple[str, str, str]] = set()
    for key in ("fallback_providers", "fallback_model"):
        raw = cfg.get(key)
        for fb in raw if isinstance(raw, list) else [raw]:
            identity = (str(fb.get("provider")), str(fb.get("model")), str(fb.get("base_url"))) if isinstance(fb, dict) else None
            if identity in seen:
                continue
            if r := _route(fb, f"fallback{len(seen)}"):
                seen.add(identity)
                routes.append(r)
    comp = (cfg.get("compression") or {}).get("model")
    if r := _route(comp, "compression"):
        routes.append(r)
    for task, aux in (cfg.get("auxiliary") or {}).items():
        if r := _route(aux, f"aux:{task}"):
            routes.append(r)
        # agent/auxiliary_client.py tries these in order when the task's own provider fails.
        chain = aux.get("fallback_chain") if isinstance(aux, dict) else None
        for i, entry in enumerate(chain if isinstance(chain, list) else []):
            if r := _route(entry, f"aux:{task}:fallback{i}"):
                routes.append(r)
    return routes


def manifest_routes(role: dict[str, Any]) -> list[dict[str, str]]:
    pol = role.get("model_policy") or {}
    routes = [r for r in [_route(pol.get("primary"), "primary")] if r]
    for i, fb in enumerate(pol.get("fallback_providers") or []):
        if r := _route(fb, f"fallback{i}"):
            routes.append(r)
    for name, aux in (pol.get("auxiliary") or {}).items():
        if r := _route(aux, f"aux:{name}"):
            routes.append(r)
    return routes


def load_receipts(receipt_dir: Path | None) -> set[tuple[str, str, str]]:
    """(provider, model, effort) triples with a perfect-or-recorded local receipt."""
    found: set[tuple[str, str, str]] = set()
    if not receipt_dir or not receipt_dir.is_dir():
        return found
    for f in receipt_dir.rglob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("model") and d.get("provider") and "score" in d:
            found.add((str(d["provider"]), str(d["model"]), str(d.get("reasoning_effort", ""))))
    return found


def _load(path: Path, loader) -> Any:
    try:
        return loader(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def audit_profile(
    name: str, pdir: Path, models_dir: Path, receipts: set[tuple[str, str, str]]
) -> dict[str, Any]:
    cfg = _load(pdir / "config.yaml", yaml.safe_load) or {}
    role = _load(pdir / "federation_role.json", json.loads) or {}
    routes = config_routes(cfg)
    findings: list[str] = []
    toolsets = set(cfg.get("toolsets") or []) | set(role.get("toolsets") or [])

    for r in routes:
        tag = f"{r['slot']}={r['provider']}/{r['model']}"
        if r["class"] == "ollama":
            findings.append(f"OLLAMA_PIN {tag}")
        elif r["class"] == "paid-cloud":
            findings.append(f"PAID_ROUTE {tag}")
        elif r["class"] == "local-llamacpp":
            if not model_on_disk(models_dir, r["model"]):
                findings.append(f"LOCAL_MODEL_MISSING {tag}")
            if (r["provider"], r["model"], r["effort"]) not in receipts:
                findings.append(f"LOCAL_UNQUALIFIED {tag}@{r['effort'] or 'unset'}")
            if toolsets & TOOL_HEAVY:
                findings.append(f"LOCAL_TOOL_RISK {tag} toolsets={sorted(toolsets & TOOL_HEAVY)}")

    for r in manifest_routes(role):
        if r["class"] == "ollama":
            findings.append(f"MANIFEST_OLLAMA_PIN {r['slot']}={r['provider']}/{r['model']}")
        elif r["class"] == "paid-cloud" and r["slot"] == "primary":
            cfg_primary = next((x for x in routes if x["slot"] == "primary"), None)
            if not cfg_primary or cfg_primary["class"] != "paid-cloud":
                findings.append(f"MANIFEST_PAID_PRIMARY {r['provider']}/{r['model']} (config differs)")
    m_primary = next((x for x in manifest_routes(role) if x["slot"] == "primary"), None)
    c_primary = next((x for x in routes if x["slot"] == "primary"), None)
    if m_primary and c_primary and (m_primary["provider"], m_primary["model"]) != (
        c_primary["provider"],
        c_primary["model"],
    ):
        findings.append(
            f"PIN_CONTRADICTION config={c_primary['provider']}/{c_primary['model']} "
            f"manifest={m_primary['provider']}/{m_primary['model']}"
        )
    if not cfg:
        findings.append("NO_CONFIG")
    return {
        "profile": name,
        "has_manifest": bool(role),
        "authority": role.get("authority"),
        "toolsets": sorted(toolsets),
        "routes": routes,
        "findings": findings,
    }


def audit_home(home: Path, receipt_dir: Path | None = None) -> dict[str, Any]:
    models_dir = home / "models"
    receipts = load_receipts(receipt_dir)
    rows = []
    root_cfg_present = (home / "config.yaml").is_file()
    if root_cfg_present:
        rows.append(audit_profile("@root", home, models_dir, receipts))
    pdirs = sorted(p for p in (home / "profiles").glob("*") if p.is_dir()) if (home / "profiles").is_dir() else []
    for p in pdirs:
        rows.append(audit_profile(p.name, p, models_dir, receipts))
    kinds = Counter(f.split(" ", 1)[0] for r in rows for f in r["findings"])
    return {
        "profile_dirs": len(pdirs),
        "manifests": sum(r["has_manifest"] for r in rows),
        "profiles_with_findings": sum(bool(r["findings"]) for r in rows),
        "finding_counts": dict(kinds),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--receipts", type=Path, help="directory of benchmark receipt JSON files")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = audit_home(args.home.expanduser(), args.receipts.expanduser() if args.receipts else None)
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        return 0
    print(f"profile dirs={report['profile_dirs']} manifests={report['manifests']} "
          f"with findings={report['profiles_with_findings']}")
    for k, v in sorted(report["finding_counts"].items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
