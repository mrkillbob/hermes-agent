"""Retain additive identity evidence for the checkout an existing OSV job scans.

This is not an installed-runtime receipt, build attestation or advisory verdict.
The ordinary package scan remains authoritative for its own package matches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.build.osv_source_inventory import source_inventory
from scripts.bundles.desktop_prepare import git, require_source
from scripts.write_install_stamp import build_stamp


def collect(source: Path, repository: str, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source):
        raise ValueError("scan identity output must be outside the source checkout")
    commit = git(source, "rev-parse", "HEAD")
    require_source(source, commit)
    stamp = build_stamp(
        commit=commit, branch=git(source, "rev-parse", "--abbrev-ref", "HEAD"),
        commit_date=int(git(source, "log", "-1", "--format=%ct", "HEAD")),
        dirty=False, source="ci", update_mechanism="external")
    inventory = source_inventory(source, stamp, repository)
    # All admission precedes output; a failed step must not upload partial evidence.
    output.mkdir(parents=True)
    for name, value in (("install-stamp.json", stamp), ("source-inventory.osv.json", inventory)):
        with (output / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        collect(args.source, args.repository, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
