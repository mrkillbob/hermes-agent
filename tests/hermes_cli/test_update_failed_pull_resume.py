"""A failed source pull settles paused runtimes before command custody ends."""

import atexit
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from hermes_cli import (
    gitlock,
    main,
    update_cmd,
    update_cmd_windows,
    update_custody,
    update_owning_install,
    update_pause_record,
    update_proxy_env,
    update_receipt,
    update_stage,
)
from tests.compat import old_updater_support

no_external_work = old_updater_support.no_external_work


def _noop(*args, **kwargs):
    return None


def _isolate_pipeline(monkeypatch, root, token, resume, failed_pull):
    """Replace machine boundaries while retaining the command's control flow."""
    facade = SimpleNamespace(
        PROJECT_ROOT=root,
        _run_pre_update_backup=_noop,
        _desktop_packaged_executable=_noop,
        _desktop_dist_exists=lambda *_: False,
        _installed_desktop_apps=list,
        _resolve_update_branch=lambda *_: "main",
        _warn_orphaned_update_autostashes=_noop,
        _resume_windows_gateways_after_update=resume,
    )
    monkeypatch.setattr(update_cmd, "_m", lambda: facade)
    for module, names in (
        (update_cmd, ("_record_pre_update_backup_outcome", "_record_snapshot_stage",
                      "_heal_stale_shallow_checkout", "_refuse_before_commit_point")),
        (update_cmd._commit, ("begin_update_attempt", "record_run_start")),
        (update_cmd._check, ("clear_git_debris", "report_pack_tidy", "fetch_release_tag")),
        (gitlock, ("release_dead_index_lock", "repair_broken_shallow_boundaries",
                   "prune_stale_shallow_grafts")),
        (update_proxy_env, ("prefer_dotenv_proxy_env",)),
        (update_stage, ("publish_stage",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, _noop)

    options = SimpleNamespace(gw_input_fn=None, assume_yes=True, switch_branch=False,
                              discard_local_changes=False, keep_stash=False)
    plan = SimpleNamespace(commit_count=1, auto_stash_ref=None, prompt_for_restore=False,
                           pre_sync_sha=None, rollback_branch=None, in_place_update=False)
    request = {"branch": "main", "receipt": {"update_id": "b" * 32}, "windows_resume": token}
    monkeypatch.setattr(update_cmd, "git_operation_in_progress", _noop)
    monkeypatch.setattr(update_cmd, "_resolve_update_options", lambda *_: options)
    monkeypatch.setattr(update_cmd, "_begin_update_receipt_and_plan", _noop)
    monkeypatch.setattr(update_cmd, "_pause_gateways_for_update", lambda *_: token)
    monkeypatch.setattr(update_cmd, "_prepare_git_command",
                        lambda **_: (False, ["unused-git"], False))
    monkeypatch.setattr(update_cmd, "_source_completion_request", lambda *_: request)
    monkeypatch.setattr(update_cmd._check, "select_apply_target",
                        lambda *_, **__: ("origin/main", None, False, None))
    monkeypatch.setattr(update_cmd._check, "tracking_refspec",
                        lambda *_: "refs/heads/main:refs/remotes/origin/main")
    monkeypatch.setattr(gitlock, "fetch_with_partial_clone_recovery",
                        lambda *_, **__: subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(update_cmd, "_current_branch_name", lambda *_, **__: "main")
    monkeypatch.setattr(update_cmd, "_prepare_checkout_for_update", lambda *_, **__: plan)
    monkeypatch.setattr(update_cmd, "_pull_updates", failed_pull)

    def unexpected_completion(*args, **kwargs):
        pytest.fail("a failed pull reached update completion")

    monkeypatch.setattr(update_cmd, "_apply_pulled_update", unexpected_completion)


@pytest.mark.usefixtures("no_external_work")
@pytest.mark.parametrize("verdict", [
    "restored-verified", "restored-unverified", "torn", "already-settled",
])
def test_failed_pull_settles_pause_before_releasing_command_custody(
    tmp_path, monkeypatch, verdict
):
    events = []
    state = {"checkout_held": False, "settled": False}
    observations = []
    writes = []
    discharged = []
    followups = []
    registered = []
    token = {"pause_id": "a" * 32, "resume_needed": True,
             "profiles": {"default": 101}, "unmapped": []}
    stored = {"token": deepcopy(token)}

    class FakeUpdateLock:
        holder = None

        def __init__(self, *, install_root):
            assert install_root == tmp_path

        def acquire(self):
            state["checkout_held"] = True
            events.append("acquire-checkout")
            return True

        def release(self):
            events.append("release-checkout")
            state["checkout_held"] = False

    def failed_pull(*args, **kwargs):
        assert kwargs["_windows_gateway_resume"] is token
        assert state["checkout_held"]
        # The checkout boundary settles its attempted restoration before exit 1.
        state["settled"] = True
        events.append("settle-tree")
        if verdict == "already-settled":
            token["resume_needed"] = False
            token["profiles"] = {}
            stored.clear()
        raise SystemExit(1)

    def whole_tree(resume_token):
        assert resume_token is token
        events.append("tree-gate")
        return (False, "interrupted pull remains") if verdict == "torn" else (True, "")

    def resume_paused_set(resume_token):
        assert resume_token is token
        events.append("verify-resume")
        if verdict == "restored-unverified":
            raise RuntimeError("gateway relaunch was not verified")
        resume_token["profiles"] = {}
        resume_token["resume_needed"] = False

    def guarded_resume(resume_token):
        observations.append({"checkout_held": state["checkout_held"],
                             "tree_settled": state["settled"]})
        events.append("guarded-resume")
        update_cmd_windows._resume_unless_torn(resume_token)

    def write_record(owed):
        events.append("retain-obligation")
        writes.append(deepcopy(owed))
        stored["token"] = deepcopy(owed)

    def discharge_record(done):
        events.append("discharge-obligation")
        discharged.append(done["pause_id"])
        stored.clear()

    real_register = atexit.register

    def register_fallback(callback, *args, **kwargs):
        if callback is guarded_resume:
            registered.append((callback, args, kwargs))
            return callback
        return real_register(callback, *args, **kwargs)

    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(update_owning_install, "retarget_to_owning_install", _noop)
    monkeypatch.setattr(main, "_update_preflight_handled", lambda *_: False)
    monkeypatch.setattr(main, "_install_hangup_protection", _noop)
    monkeypatch.setattr(main, "_finalize_update_output", _noop)
    monkeypatch.setattr(main, "_finalize_update_receipt", _noop)
    monkeypatch.setattr("hermes_cli.update_lock.UpdateLock", FakeUpdateLock)
    monkeypatch.setattr(update_custody, "refusal_notice", _noop)
    monkeypatch.setattr(atexit, "register", register_fallback)
    monkeypatch.setattr(update_pause_record, "tree_is_whole", whole_tree)
    monkeypatch.setattr(update_cmd_windows, "_resume_paused_set", resume_paused_set)
    monkeypatch.setattr(update_pause_record, "write", write_record)
    monkeypatch.setattr(update_pause_record, "discharge", discharge_record)
    monkeypatch.setattr(update_receipt, "owe_followup",
                        lambda *args, **kwargs: followups.append((args, kwargs)))
    _isolate_pipeline(monkeypatch, tmp_path, token, guarded_resume, failed_pull)

    # The updater's captured fallback is deliberately never invoked by this test.
    with pytest.raises(SystemExit) as error:
        main.cmd_update(SimpleNamespace(gateway=False))

    assert error.value.code == 1
    assert len(registered) == 1
    if verdict == "already-settled":
        assert observations == []
        assert events == ["acquire-checkout", "settle-tree", "release-checkout"]
        assert writes == discharged == followups == []
        assert stored == {}
        assert state["checkout_held"] is False
        return

    assert observations == [{"checkout_held": True, "tree_settled": True}]
    assert events.index("settle-tree") < events.index("guarded-resume")
    assert events.index("guarded-resume") < events.index("release-checkout")
    assert state["checkout_held"] is False

    if verdict == "restored-verified":
        assert events.count("verify-resume") == 1
        assert discharged == [token["pause_id"]]
        assert writes == []
        assert stored == {}
        assert followups == []
    else:
        assert discharged == []
        assert len(writes) == 1
        assert stored["token"]["pause_id"] == token["pause_id"]
        assert stored["token"]["profiles"] == {"default": 101}
        assert stored["token"]["resume_needed"] is True
        if verdict == "torn":
            assert "verify-resume" not in events
            assert followups == []
        else:
            assert events.count("verify-resume") == 1
            assert len(followups) == 1
            assert followups[0][0][:2] == ("b" * 32, "windows_resume")
