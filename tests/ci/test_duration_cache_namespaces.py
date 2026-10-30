"""Duration cache restore prefixes must accept only their own test lane."""

from pathlib import Path

from ruamel.yaml import YAML


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/tests.yml"


def test_duration_cache_restore_namespaces_do_not_admit_other_lanes():
    jobs = YAML(typ="base").load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    caches = {}
    for lane in ("test", "e2e"):
        steps = jobs[lane]["steps"]
        restore = next(
            step for step in steps if step.get("with", {}).get("restore-keys")
        )
        save = next(
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/cache/save@")
            and step.get("with", {}).get("path") == "test_durations.json"
        )
        saved_key = save["with"]["key"].replace("${{ github.run_id }}", "123456")
        prefixes = restore["with"]["restore-keys"].split()
        assert any(saved_key.startswith(prefix) for prefix in prefixes), lane
        caches[lane] = (saved_key, prefixes)

    for lane, (_, prefixes) in caches.items():
        for other_lane, (saved_key, _) in caches.items():
            if other_lane != lane:
                assert not any(saved_key.startswith(prefix) for prefix in prefixes), (
                    f"{lane} restores {other_lane} durations through {prefixes}"
                )
