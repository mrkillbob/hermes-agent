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
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

LLAMACPP = frozenset({"llamacpp", "llama.cpp", "llama-cpp", "local"})
OLLAMA = frozenset({"ollama", "ollama-launch"})
# Metered or subscription-quota providers. ``:free`` Nous/OpenRouter routes are not metered.
PAID = frozenset({"openai-codex", "openai", "anthropic", "openrouter", "gemini", "xai", "bedrock", "vertex"})
FREE_CLOUD = frozenset({"nous", "nvidia", "nim"})
TOOL_HEAVY = frozenset({"computer_use", "browser", "delegation", "code_execution"})


def classify(provider: str, model: str) -> str:
    p = (provider or "").strip().lower()
    if p in LLAMACPP:
        return "local-llamacpp"
    if p in OLLAMA:
        return "ollama"
    if p in PAID and not str(model).endswith(":free"):
        return "paid-cloud"
    if p in FREE_CLOUD or str(model).endswith(":free"):
        return "free-cloud"
    return "other-cloud"


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


def config_routes(cfg: dict[str, Any]) -> list[dict[str, str]]:
    routes = [r for r in [_route(cfg.get("model"), "primary")] if r]
    for i, fb in enumerate(cfg.get("fallback_providers") or []):
        if r := _route(fb, f"fallback{i}"):
            routes.append(r)
    comp = (cfg.get("compression") or {}).get("model")
    if r := _route(comp, "compression"):
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
    name: str, pdir: Path, models_dir: Path | None, receipts: set[tuple[str, str, str]]
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
            if models_dir is not None and not (models_dir / f"{r['model']}.gguf").is_file():
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
        rows.append(audit_profile("@root", home, models_dir if models_dir.is_dir() else None, receipts))
    pdirs = sorted(p for p in (home / "profiles").glob("*") if p.is_dir()) if (home / "profiles").is_dir() else []
    for p in pdirs:
        rows.append(audit_profile(p.name, p, models_dir if models_dir.is_dir() else None, receipts))
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
