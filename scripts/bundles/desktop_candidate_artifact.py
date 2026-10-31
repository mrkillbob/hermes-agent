"""Verify and retain a development candidate; never launch or install it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def metadata(path: Path, root: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"candidate metadata escapes its package: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"candidate metadata is not a record: {path.name}")
    return value


def verify_signature_report(report: str) -> None:
    if (report.splitlines().count("Signature=adhoc") != 1
            or len(re.findall(r"^TeamIdentifier=not\s*set$", report, re.MULTILINE)) != 1
            or re.search(r"^Authority=", report, re.MULTILINE)
            or not re.search(r"^CodeDirectory\b[^\n]*flags=[^\n]*\bruntime\b", report, re.MULTILINE)):
        raise ValueError("candidate must have an ad-hoc signature and hardened runtime without a signing team")


def verify_payload_facts(payload: Path, commit: str, expected_lock: Path) -> dict:
    from pm.lock import Lockfile
    from pm.store import tree_digest

    manifest_path = payload / "manifest.json"
    manifest = metadata(manifest_path, payload)
    if (manifest.get("schema") != 1 or manifest.get("target") != "darwin-arm64"
            or manifest.get("repo") != "hermes-agent" or manifest.get("ref") != commit
            or manifest.get("store") != "tools"
            or manifest.get("runtime", {}).get("repoDir") != "hermes-agent"):
        raise ValueError("candidate payload provenance differs from the admitted commit/target")
    tools = payload / "tools"
    facts_path = tools / "facts.json"
    packages = metadata(facts_path, payload).get("packages", {})
    if not isinstance(packages, dict):
        raise ValueError("candidate payload tool facts are not a record")
    lock_path = payload / "hermes-agent/pm/lock.json"
    metadata(lock_path, payload)
    if lock_path.read_bytes() != expected_lock.read_bytes():
        raise ValueError("candidate payload lock provenance differs from the source commit")
    lock = Lockfile(lock_path)
    count = 0
    for name, fact in packages.items():
        if isinstance(fact, dict) and "entry" not in fact and "stamp" in fact:
            continue
        verify_tool_fact(name, fact, tools, lock)
        count += 1
    if not count:
        raise ValueError("candidate payload carries no verified tools")
    return {
        "toolsVerified": count, "factsSha256": sha256(facts_path),
        "manifestSha256": sha256(manifest_path), "lockSha256": sha256(lock_path),
        "payloadSha256": tree_digest(payload),
        "sourceSnapshotSha256": tree_digest(payload / "hermes-agent"),
    }


def verify_tool_fact(name, fact, tools, lock) -> None:
    from pm.store import tree_digest

    entry = fact.get("entry") if isinstance(fact, dict) else None
    if (not isinstance(entry, str) or entry in ("", ".", "..")
            or any(char in entry for char in "/\\:")):
        raise ValueError(f"candidate payload tool entry is invalid: {name}")
    directory = tools / entry
    if (directory.is_symlink() or not directory.is_dir()
            or not directory.resolve().is_relative_to(tools.resolve())
            or fact.get("version") != lock.version(name) or fact.get("target") != "darwin-arm64"
            or not lock.artifacts(name, "darwin-arm64")
            or fact.get("artifacts") != [item["sha256"] for item in lock.artifacts(name, "darwin-arm64")]):
        raise ValueError(f"candidate payload tool does not match its source pin: {name}")
    if fact.get("digest") != tree_digest(directory):
        raise ValueError(f"candidate payload final-byte digest mismatch: {name}")


def inspect_zip(package: Path) -> None:
    """Reject traversal and writes through symlink aliases before extraction."""
    with zipfile.ZipFile(package) as archive:
        seen: set[str] = set()
        links: set[str] = set()
        for member in archive.infolist():
            name = member.filename
            normalized = posixpath.normpath(name)
            if name.startswith("/") or ".." in name.split("/") or normalized in seen:
                raise ValueError("candidate ZIP has an escaping or duplicate member")
            seen.add(normalized)
            if stat.S_ISLNK(member.external_attr >> 16):
                verify_zip_link(archive, member, normalized)
                links.add(normalized)
        # Check after collecting all links: entry order cannot change admission.
        for name in seen:
            parts = name.split("/")
            if any("/".join(parts[:index]) in links for index in range(1, len(parts))):
                raise ValueError("candidate ZIP writes through an archive symlink")


def verify_zip_link(archive, member, name: str) -> None:
    if member.file_size > 4096:
        raise ValueError("candidate ZIP symlink is unbounded")
    target = archive.read(member).decode("utf-8")
    destination = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    app_root = name.split("/", 1)[0]
    if (target.startswith("/") or destination == ".." or destination.startswith("../")
            or (app_root.endswith(".app") and destination.split("/", 1)[0] != app_root)):
        raise ValueError("candidate ZIP symlink escapes its app or extraction")

def verify_stamps(app: Path, commit: str) -> tuple[bytes, Path]:
    resources = app / "Contents/Resources"
    stamp_path = resources / "install-stamp.json"
    stamp = metadata(stamp_path, app)
    if (stamp.get("commit") != commit or stamp.get("dirty") is not False
            or stamp.get("source") != "commit-build" or stamp.get("payload") != "bundled"
            or stamp.get("distribution") != "desktop-app" or stamp.get("updateMechanism") != "external"
            or stamp.get("tag") is not None or stamp.get("channelBuild") is not None):
        raise ValueError("candidate install stamp differs from its admitted commit-build contract")
    payload = resources / "agent-payload"
    embedded_stamp = payload / "hermes-agent/install-stamp.json"
    metadata(embedded_stamp, payload)
    stamp_bytes = stamp_path.read_bytes()
    if embedded_stamp.read_bytes() != stamp_bytes:
        raise ValueError("candidate app and embedded payload stamps differ")
    return stamp_bytes, payload


def export_candidate(prepared) -> Path:
    prepared.validate()
    request = prepared.request
    if not request.development_candidate:
        raise ValueError("candidate export requires explicit development admission")
    packages = list((request.source / "apps/desktop/release").glob("*-mac-arm64.zip"))
    if len(packages) != 1 or packages[0].is_symlink() or not packages[0].is_file():
        raise ValueError("candidate requires exactly one regular arm64 ZIP")
    package = packages[0]
    package_hash = sha256(package)
    inspect_zip(package)
    if request.work.is_symlink():
        raise ValueError("candidate work root must not be a symlink")
    output = request.work / "candidate-artifact"
    # Refuse stale output rather than mixing evidence from different builds.
    if output.exists() or output.is_symlink():
        raise ValueError("candidate export already exists; use a fresh prepared job")
    with tempfile.TemporaryDirectory(prefix="candidate-unpacked-", dir=request.work) as temporary:
        unpacked = Path(temporary)
        subprocess.run(["ditto", "-x", "-k", str(package), str(unpacked)], check=True)
        apps = list(unpacked.glob("*.app"))
        if len(apps) != 1 or apps[0].is_symlink():
            raise ValueError("candidate ZIP must contain exactly one regular app bundle")
        app = apps[0]
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        signature = subprocess.run(["codesign", "-d", "--verbose=4", str(app)],
                                   capture_output=True, text=True, check=True)
        verify_signature_report(signature.stdout + signature.stderr)
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        executable = info["CFBundleExecutable"]
        if (not isinstance(executable, str) or not executable
                or any(character in executable for character in ("/", chr(92), chr(0)))):
            raise ValueError("candidate executable identity is invalid")
        arch = subprocess.check_output(["lipo", "-archs", str(app / "Contents/MacOS" / executable)], text=True)
        if arch.split() != ["arm64"]:
            raise ValueError("candidate executable is not exclusively arm64")
        stamp_bytes, payload = verify_stamps(app, request.commit)
        payload_evidence = verify_payload_facts(payload, request.commit, request.source / "pm/lock.json")
        assessment = subprocess.run(["spctl", "--assess", "--type", "execute", str(app)],
                                    capture_output=True, text=True, check=False)
        receipt = {
            "schema": 1, "commit": request.commit,
            "tree": subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"],
                                             cwd=request.source, text=True).strip(),
            "target": request.target, "signingMode": "development-adhoc",
            "requestDigest": request.identity_digest(), "packagingReceiptSha256": sha256(prepared.packager),
            "artifact": {"name": package.name, "size": package.stat().st_size, "sha256": package_hash},
            "stampSha256": hashlib.sha256(stamp_bytes).hexdigest(),
            "signatureSubject": "app extracted from the retained ZIP",
            "codeSignatureVerified": True, "architectureVerified": True, "hardenedRuntime": True,
            "gatekeeperAccepted": assessment.returncode == 0, "installationAuthorized": False,
            **payload_evidence,
        }
        output.mkdir()
        shutil.copy2(package, output / package.name)
        if sha256(output / package.name) != package_hash:
            raise ValueError("candidate package changed between verification and retention")
        (output / "install-stamp.json").write_bytes(stamp_bytes)
        (output / "candidate.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return output


def main() -> None:
    from scripts.bundles.desktop_prepare import PreparedDesktop

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    args = parser.parse_args()
    prepared = PreparedDesktop.load(args.prepared)
    export_candidate(prepared)


if __name__ == "__main__":
    main()
