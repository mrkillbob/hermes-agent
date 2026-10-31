"""Admission and receipt binding for development-only Desktop candidates."""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re


def reject_signing_inputs(env: Mapping[str, str]) -> None:
    for key, value in env.items():
        name = key.upper()
        if value and (name.startswith(("CSC_", "WIN_CSC_", "APPLE_"))
                      or name in {"HERMES_MAC_SIGNATURE_CACHE", "HERMES_PREPARED_PACKAGING",
                                  "HERMES_PREPARED_NATIVE_DEPS", "HERMES_PREPARED_TARGET",
                                  "HERMES_PAYLOAD_TAG", "_HERMES_CHANNEL_REQUEST_JSON",
                                  "RELEASE_CLAIM_TAG", "RELEASE_CLAIM_OBJECT", "HERMES_RELEASE_EPOCH"}):
            raise ValueError(f"development candidate refuses ambient signing input {key}")


def validate_selection(data: Mapping, env: Mapping[str, str]) -> None:
    enabled = data.get("development_candidate", False)
    if type(enabled) is not bool:
        raise ValueError("development candidate selection must be boolean")
    if not enabled:
        return
    if (data.get("variant") != "bundled" or data.get("target") != "darwin-arm64"
            or data.get("tag") is not None or data.get("channel_request") is not None
            or data.get("release_epoch") is not None or data.get("release_commit") is not None
            or not isinstance(data.get("commit"), str)
            or not re.fullmatch(r"[a-f0-9]{40}", data["commit"])):
        raise ValueError("development candidate requires a bundled darwin-arm64 commit build without release selectors")
    reject_signing_inputs(env)


def bind_packaging(request, packager: Path) -> None:
    """Enrich the owned receipt before PreparedDesktop fingerprints it."""
    from pm.lock import _write

    if not request.development_candidate:
        return
    data = json.loads(packager.read_text(encoding="utf-8-sig"))
    data["desktopCandidate"] = {
        "request": request.data(), "requestDigest": request.identity_digest(),
    }
    _write(packager, data)


def validate_packaging(request, packager: Path) -> None:
    data = json.loads(packager.read_text(encoding="utf-8-sig"))
    candidate = data.get("desktopCandidate")
    if not request.development_candidate:
        if candidate is not None:
            raise ValueError("development candidate packaging was not admitted by this preparation")
        return
    if candidate != {"request": request.data(), "requestDigest": request.identity_digest()}:
        raise ValueError("development candidate packaging differs from its admitted request")
