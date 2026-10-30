"""Sessionless palette resolves the selected profile while preserving shared-skill support."""

from unittest.mock import patch

from tests.tui_gateway.protocol_fixtures import (
    _restore_stdout as _restore_stdout, server as server,  # noqa: PLC0414
)


def test_sessionless_slash_palette_follows_profile_param(server, tmp_path, monkeypatch):
    """A Desktop draft has no session yet: ``commands.catalog`` / ``complete.slash`` must scan the
    named ``profile``'s home, not the launch profile's — A→B→A under multiplexing (#124651). The
    palette's quick_commands are that profile's too, and an unknown profile is 4064, not a
    launch-profile palette."""
    import agent.skill_commands as sc_mod
    from agent.secret_scope import is_multiplex_active, set_multiplex_active

    root = tmp_path / "hermes_home"
    for name in ("s6probe-a", "s6probe-b"):
        skill_dir = tmp_path / f"external_{name}" / f"{name}-only"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}-only\ndescription: Only in {name}.\n---\n\n# x\n")
        (root / "profiles" / name).mkdir(parents=True)
        (root / "profiles" / name / "config.yaml").write_text(
            f"skills:\n  external_dirs:\n    - {skill_dir.parent}\n"
            f"quick_commands:\n  {name}-qc:\n    type: exec\n    command: echo {name}\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(server, "_hermes_home", str(root))

    def palette(profile):
        catalog = server.handle_request({"id": "r1", "method": "commands.catalog", "params": {"profile": profile}})
        typed = server.handle_request({
            "id": "r2", "method": "complete.slash", "params": {"text": "/s6probe", "profile": profile}})
        assert "result" in catalog and "result" in typed, (catalog, typed)
        quick = {key for key, _ in catalog["result"]["pairs"] if key.endswith("-qc")}
        return set(catalog["result"]["skills"]), {item["text"].strip("/") for item in typed["result"]["items"]}, quick

    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with (
            patch("tools.skills_tool.SKILLS_DIR", tmp_path / "no-local-skills"),
            # This probe isolates profile-owned roots from the fork's intentionally shared user tier.
            patch("agent.skill_utils._shared_user_skills_dir", return_value=None),
            patch.object(sc_mod, "_skill_commands_by_key", {}),
        ):
            assert palette("s6probe-a") == ({"/s6probe-a-only"}, {"s6probe-a-only"}, {"/s6probe-a-qc"})
            assert palette("s6probe-b") == ({"/s6probe-b-only"}, {"s6probe-b-only"}, {"/s6probe-b-qc"})
            assert palette("s6probe-a") == ({"/s6probe-a-only"}, {"s6probe-a-only"}, {"/s6probe-a-qc"})
            for method in ("commands.catalog", "skills.reload"):
                bad = server.handle_request({"id": "r3", "method": method, "params": {"profile": "../x"}})
                assert bad.get("error", {}).get("code") == 4064, (method, bad)
    finally:
        set_multiplex_active(previous)
