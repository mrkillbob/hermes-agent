"""Print native lifetime handoff evidence using two explicitly selected interpreters."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def child(domain, verbatim):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pm import lifetime
    from pm.filesystem import long_root

    domain = long_root(Path(domain)) if verbatim else Path(domain)
    print(json.dumps({"python": sys.version, "domain_argument": str(domain)}), flush=True)
    try:
        with lifetime.activity(domain, create=True):
            with lifetime.gate(domain) as guard:
                print(json.dumps({"recorded": guard._record(), "observed": guard._snapshot(),
                                  "qualified": guard.qualified, "held": guard.held()}), flush=True)
                assert guard.qualified and guard.held()
    except lifetime.LifetimeUnavailable:
        with lifetime.gate(domain) as guard:
            print(json.dumps({"recorded": guard._record(), "observed": guard._snapshot()}), flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy")
    parser.add_argument("--worker")
    parser.add_argument("--domain")
    parser.add_argument("--verbatim", action="store_true")
    options = parser.parse_args()
    if options.domain:
        child(options.domain, options.verbatim)
        return
    if os.name != "nt" or not options.legacy or not options.worker:
        parser.error("native Windows and both interpreter paths are required")
    script = str(Path(__file__).resolve())
    failures = 0
    with tempfile.TemporaryDirectory(prefix="hermes-lifetime-probe-") as temporary:
        for name, creator, reader, creator_verbatim, reader_verbatim in (
            ("python311-to-python314", options.legacy, options.worker, False, False),
            ("native-to-verbatim", options.worker, options.worker, False, True),
            ("verbatim-to-native", options.worker, options.worker, True, False),
        ):
            domain = str(Path(temporary) / name / "state")
            print(f"CASE {name}", flush=True)
            for label, python, verbatim in (("creator", creator, creator_verbatim),
                                            ("reader", reader, reader_verbatim)):
                command = [python, "-I", "-S", "-B", script, "--domain", domain]
                if verbatim:
                    command.append("--verbatim")
                result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
                print(f"{label}: exit={result.returncode}\n{result.stdout}{result.stderr}", flush=True)
                if result.returncode:
                    failures += 1
                    break
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
