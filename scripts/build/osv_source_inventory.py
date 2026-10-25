"""Export an additive OSV commit inventory from an existing install stamp.

Run from the repository root with ``python -m scripts.build.osv_source_inventory``.
The build controller supplies its admitted GitHub owner/repository and stamp;
this exporter reuses the existing clean-checkout admission, not a new version
resolver. Retain the stamp for release identity and the ordinary lockfile scan
for package matching. No stamp or package metadata is rewritten.

Output belongs outside the source checkout so it cannot become an implicitly
discovered scanner input. A commit inventory is source identity, never a build
or runtime-adoption receipt. Without Git-range advisory data, a commit lookup
cannot establish absence of vulnerabilities or clear package-version findings.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib

from hermes_cli.steward import UPDATE_MECHANISMS
from scripts.bundles.desktop_prepare import git, require_source
from scripts.releases.commit_build import require_commit
from scripts.write_install_stamp import FALLBACK_COMMIT, STAMP_SCHEMA_VERSION


def _stamp_commit(stamp: dict) -> str:
    if (not isinstance(stamp, dict) or stamp.get("schemaVersion") != STAMP_SCHEMA_VERSION
            or stamp.get("dirty") is not False or not isinstance(stamp.get("source"), str)
            or stamp["source"] in {"", "unknown", "fallback"}
            or not isinstance(stamp.get("updateMechanism"), str)
            or stamp["updateMechanism"] not in UPDATE_MECHANISMS):
        raise ValueError("source inventory requires a qualified clean install stamp")
    commit = require_commit(stamp.get("commit"))
    if commit == FALLBACK_COMMIT:
        raise ValueError("source inventory requires a non-fallback commit")
    return commit


def source_inventory(source: Path, stamp: dict, repository: str) -> dict:
    """Bind a canonical clean stamp to the checkout's unique editable root."""
    commit = _stamp_commit(stamp)
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository)
            or repository.split("/")[-1] in {".", ".."}):
        raise ValueError("repository must be a GitHub owner/repository without URL credentials or parameters")
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source.resolve():
        raise ValueError("source inventory requires the Git checkout root")
    require_source(source, commit)
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8-sig")).get("project", {})
    lock = tomllib.loads((source / "uv.lock").read_text(encoding="utf-8-sig"))
    roots = [package for package in lock.get("package", [])
             if isinstance(package, dict) and package.get("source") == {"editable": "."}]
    if (not isinstance(project, dict) or len(roots) != 1
            or any(not isinstance(project.get(key), str) or not project[key]
                   or roots[0].get(key) != project[key] for key in ("name", "version"))):
        raise ValueError("expected exactly one editable root matching project name and version")
    require_source(source, commit)
    return {"results": [{"packages": [{"package": {
        "name": f"https://github.com/{repository}", "commit": commit}}]}]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Clean admitted source checkout")
    parser.add_argument("--stamp", type=Path, required=True, help="Existing canonical install stamp")
    parser.add_argument("--repository", required=True, help="Build-controller-admitted GitHub owner/repository")
    parser.add_argument("--output", type=Path, required=True, help="New artifact outside the source checkout")
    args = parser.parse_args(argv)
    try:
        source, output = args.source.resolve(), args.output.resolve()
        if output.is_relative_to(source):
            raise ValueError("source inventory output must be outside the source checkout")
        stamp = json.loads(args.stamp.read_text(encoding="utf-8-sig"))
        inventory = source_inventory(source, stamp, args.repository)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(inventory, indent=2) + "\n")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
