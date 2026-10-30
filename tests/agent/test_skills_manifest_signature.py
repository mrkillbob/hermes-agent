"""A same-length SKILL.md rewrite that preserves mtime must still invalidate the skills index.

Another process (``cp -p``, ``rsync -t``, a tar restore, ``os.utime``) can replace a skill with
different bytes of the same length and the original mtime. The manifest feeding the prompt cache key
and the disk snapshot must then still change (inode/ctime via ``utils.file_signature``), otherwise the
model's skills index stays stale until restart.
"""

import json
import os

import pytest

from agent.prompt_builder import (
    _SKILLS_SNAPSHOT_VERSION,
    _build_skills_manifest,
    _load_skills_snapshot,
    _skills_prompt_snapshot_path,
    build_skills_system_prompt,
    clear_skills_system_prompt_cache,
)

OLD_DESC = "Alpha one"
NEW_DESC = "Bravo two"  # same byte length as OLD_DESC


def _skill_text(desc: str) -> str:
    return f"---\nname: demo\ndescription: {desc}\n---\nbody\n"


@pytest.fixture(autouse=True)
def _skills_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    clear_skills_system_prompt_cache(clear_snapshot=True)
    yield tmp_path
    clear_skills_system_prompt_cache(clear_snapshot=True)


def _write_skill(home, desc):
    d = home / "skills" / "tools" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "SKILL.md"
    path.write_text(_skill_text(desc), encoding="utf-8")
    return path


def _replace_preserving_mtime(path, desc, *, atomic):
    st = path.stat()
    if atomic:
        tmp = path.with_name("SKILL.md.new")
        tmp.write_text(_skill_text(desc), encoding="utf-8")
        os.replace(tmp, path)
    else:
        path.write_text(_skill_text(desc), encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert path.stat().st_mtime_ns == st.st_mtime_ns
    assert path.stat().st_size == st.st_size


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("atomic", [False, True], ids=["in-place", "atomic-replace"])
def test_same_length_mtime_preserving_rewrite_changes_manifest(_skills_home, atomic):
    skill = _write_skill(_skills_home, OLD_DESC)
    before = _build_skills_manifest(_skills_home / "skills")
    _replace_preserving_mtime(skill, NEW_DESC, atomic=atomic)
    assert _build_skills_manifest(_skills_home / "skills") != before


@pytest.mark.platforms("linux", "macos")
@pytest.mark.parametrize("atomic", [False, True], ids=["in-place", "atomic-replace"])
def test_same_length_mtime_preserving_rewrite_refreshes_index(_skills_home, atomic):
    skill = _write_skill(_skills_home, OLD_DESC)
    first = build_skills_system_prompt()
    assert OLD_DESC in first
    _replace_preserving_mtime(skill, NEW_DESC, atomic=atomic)
    second = build_skills_system_prompt()
    assert NEW_DESC in second
    assert OLD_DESC not in second


def test_manifest_values_stay_json_serialisable_lists(_skills_home):
    _write_skill(_skills_home, OLD_DESC)
    manifest = _build_skills_manifest(_skills_home / "skills")
    assert manifest
    assert all(isinstance(sig, list) for sig in manifest.values())
    assert json.loads(json.dumps(manifest)) == manifest


def test_old_version_snapshot_is_discarded(_skills_home):
    _write_skill(_skills_home, OLD_DESC)
    skills_dir = _skills_home / "skills"
    build_skills_system_prompt()
    snap_path = _skills_prompt_snapshot_path()
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snapshot["version"] == _SKILLS_SNAPSHOT_VERSION
    assert _load_skills_snapshot(skills_dir) is not None

    # A pre-bump snapshot carries [mtime_ns, size] manifests and the previous version number.
    legacy = dict(snapshot, version=_SKILLS_SNAPSHOT_VERSION - 1)
    legacy["manifest"] = {rel: sig[:2] for rel, sig in snapshot["manifest"].items()}
    snap_path.write_text(json.dumps(legacy), encoding="utf-8")
    assert _load_skills_snapshot(skills_dir) is None
    # Even with a manifest that matches today's, a stale version number is rejected.
    snap_path.write_text(json.dumps(dict(snapshot, version=_SKILLS_SNAPSHOT_VERSION - 1)), encoding="utf-8")
    assert _load_skills_snapshot(skills_dir) is None

    # The next build rewrites it at the current version.
    clear_skills_system_prompt_cache()
    build_skills_system_prompt()
    assert json.loads(snap_path.read_text(encoding="utf-8"))["version"] == _SKILLS_SNAPSHOT_VERSION
