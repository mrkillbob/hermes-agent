"""Local Git and evidence fixtures for the plugin retirement contracts."""
from dataclasses import asdict
from hashlib import sha256
import json
import subprocess


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True,
                            text=True, encoding="utf-8")
    return result.stdout.strip()


def references(tmp_path, identity):
    result = []
    for name in ("preservation", "owner"):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({"identity": asdict(identity), "owner": "fixture-feedback-controller",
                                    "producer_generation": "7" * 40}))
        result.append((path, sha256(path.read_bytes()).hexdigest()))
    return result
