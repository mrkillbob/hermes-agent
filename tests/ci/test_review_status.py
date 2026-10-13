"""Inert API contracts for the bounded shared-head review publisher."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts.ci import review_status as status
from scripts.ci.review_admission import CODEX_USER_ID, GitHub, MetadataError, REPOSITORY

HEAD = "a" * 40
SOURCE = "b" * 40
ENV = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_REF": "refs/heads/main",
       "GITHUB_SHA": SOURCE, "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1",
       "GITHUB_EVENT_NAME": "push"}


def pr(number, head=HEAD):
    return {"number": number, "state": "open", "head": {"sha": head},
            "base": {"repo": {"full_name": REPOSITORY}}, "requested_reviewers": []}


def snapshot(number, held=False):
    at = "2026-10-12T01:00:00Z"
    return {"pr": pr(number), "comments": [], "threads": [],
            "reviews": [{"id": index, "user": {"id": CODEX_USER_ID}, "commit_id": HEAD,
                         "body": body, "state": "COMMENTED", "submitted_at": at}
                        for index, body in enumerate(("Codex Review", "Security Review"), 1)],
            "activity": {"updated_at": at,
                         "passes": {kind: {"sha": HEAD, "at": at,
                                           "state": "running" if held else "completed"}
                                    for kind in ("code", "security")},
                         "security": {"status": "completed", "headSha": HEAD}},
            "reactions": [{"content": "+1", "user": {"id": CODEX_USER_ID},
                           "created_at": "2026-10-12T01:00:01Z"}]}


class FakeAPI(GitHub):
    def __init__(self):
        super().__init__(REPOSITORY)
        self.run = {"id": 7, "run_attempt": 1, "repository": {"full_name": REPOSITORY},
                    "head_sha": SOURCE, "head_branch": "main", "path": status.WORKFLOW,
                    "event": "push"}
        self.prs = [pr(1)]
        self.checks = []
        self.calls = []
        self.snapshots = []
        self.held = set()
        self.on_read = lambda route: None
        self.on_snapshot = lambda number: None
        self.ambiguous_success = False
        self.rollback_fails = False

    def request(self, route, *, post=False, payload=None, method=None):
        verb = method or ("POST" if post else "GET")
        self.calls.append((verb, route, deepcopy(payload)))
        if verb == "GET":
            self.on_read(route)
        prefix = f"repos/{self.repo}"
        if route == prefix:
            return {"full_name": self.repo, "default_branch": "main"}
        if "/actions/runs/" in route:
            return deepcopy(self.run)
        if route.startswith(prefix + "/pulls?"):
            page = int(route.rsplit("=", 1)[1])
            return deepcopy(self.prs[(page - 1) * 100:page * 100])
        if "/commits/" in route and "/check-runs?" in route:
            assert "filter=all" in route
            return {"total_count": len(self.checks), "check_runs": deepcopy(self.checks)}
        if verb == "POST":
            check = dict(payload, id=10 + len(self.checks),
                         app={"id": status.APP_ID, "slug": status.APP_SLUG})
            self.checks.append(check)
            return deepcopy(check)
        if "/check-runs/" in route:
            check_id = int(route.rsplit("/", 1)[1])
            check = next(row for row in self.checks if row["id"] == check_id)
            if verb == "PATCH":
                if payload["conclusion"] == "failure" and self.rollback_fails:
                    raise MetadataError("Rollback unavailable")
                check.update(payload)
                if payload["conclusion"] == "success" and self.ambiguous_success:
                    raise MetadataError("Response lost after applied update")
            return deepcopy(check)
        raise AssertionError(route)

    def snapshot(self, number, head):
        self.calls.append(("SNAPSHOT", number, None))
        self.snapshots.append(number)
        self.on_snapshot(number)
        live = next(row for row in self.prs if row["number"] == number)
        if live["head"]["sha"] != head or live["state"] != "open":
            raise MetadataError("Head moved")
        return snapshot(number, number in self.held)


def writes(api):
    return [row for row in api.calls if row[0] in ("POST", "PATCH")]


@pytest.mark.parametrize("field,value", [
    ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/heads/topic"),
    ("GITHUB_SHA", "abc"), ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ATTEMPT", "-1"),
    ("GITHUB_RUN_ID", "8"), ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_EVENT_NAME", "pull_request"),
])
def test_invalid_runtime_never_writes(field, value):
    api = FakeAPI()
    assert status.publish(api, HEAD, dict(ENV, **{field: value}))["state"] == "failure"
    assert writes(api) == []


@pytest.mark.parametrize("field,value", [
    ("head_sha", "c" * 40), ("head_branch", "topic"),
    ("path", ".github/workflows/other.yml"), ("event", "pull_request"),
    ("repository", {"full_name": "other/repo"}),
])
def test_invalid_server_execution_never_writes(field, value):
    api = FakeAPI()
    api.run[field] = value
    assert status.publish(api, HEAD, ENV)["state"] == "failure"
    assert writes(api) == []


def test_fresh_failure_precedes_all_rich_reads_and_stable_own_patch_succeeds():
    api = FakeAPI()
    assert status.publish(api, HEAD, ENV)["state"] == "success"
    assert [row[0] for row in writes(api)] == ["POST", "PATCH"]
    first = writes(api)[0]
    assert first[2]["conclusion"] == "failure"
    assert first[2]["head_sha"] == HEAD
    assert SOURCE in first[2]["external_id"]
    assert all("/actions/runs/" in route or route == f"repos/{REPOSITORY}"
               for _, route, _ in api.calls[:api.calls.index(first)])
    assert writes(api)[1][1].endswith("/10")
    assert api.checks[0]["conclusion"] == "success"


def test_held_single_pr_stays_failure():
    api = FakeAPI()
    api.held.add(1)
    assert status.publish(api, HEAD, ENV)["state"] == "failure"
    assert [row[0] for row in writes(api)] == ["POST"]
    assert api.checks[0]["conclusion"] == "failure"


@pytest.mark.parametrize("held", [False, True])
def test_every_open_pr_sharing_head_across_bases_must_clear(held):
    api = FakeAPI()
    api.prs.append(pr(2))
    api.prs[1]["base"]["ref"] = "release"
    if held:
        api.held.add(1)
    verdict = status.publish(api, HEAD, ENV)
    assert verdict["state"] == ("failure" if held else "success")
    assert {1, 2}.issubset(api.snapshots)
    assert api.checks[0]["conclusion"] == verdict["state"]


@pytest.mark.parametrize("change", ["moved", "added", "hold"])
def test_late_change_after_own_check_read_prevents_success(change):
    api = FakeAPI()

    def mutate(route):
        if route.endswith("/check-runs/10"):
            if change == "moved":
                api.prs[0]["head"]["sha"] = "c" * 40
            elif change == "added":
                api.prs.append(pr(2))
            else:
                api.held.add(1)

    api.on_read = mutate
    assert status.publish(api, HEAD, ENV)["state"] == "failure"
    assert [row[0] for row in writes(api)] == ["POST"]


def test_metadata_outage_after_post_leaves_failure():
    api = FakeAPI()

    def outage(route):
        if "/commits/" in route:
            raise MetadataError("Metadata unavailable")

    api.on_read = outage
    assert status.publish(api, HEAD, ENV)["state"] == "failure"
    assert api.checks[0]["conclusion"] == "failure"
    assert [row[0] for row in writes(api)] == ["POST"]


@pytest.mark.parametrize("mismatch", ["app", "namespace", "duplicate"])
def test_unowned_or_duplicate_checks_are_never_mutated(mismatch):
    api = FakeAPI()
    historic = {"id": 90, "name": status.NAME, "head_sha": HEAD,
                "external_id": status.external_id(REPOSITORY, HEAD,
                                                  {"run_id": 7, "attempt": 1, "source": SOURCE}),
                "app": {"id": status.APP_ID, "slug": status.APP_SLUG},
                "status": "completed", "conclusion": "success"}
    if mismatch == "app":
        historic["app"] = {"id": 1, "slug": "other"}
    elif mismatch == "namespace":
        historic["external_id"] = "unowned"
    api.checks.append(deepcopy(historic))
    assert status.publish(api, HEAD, ENV)["state"] == "failure"
    assert api.checks[0] == historic
    assert [row[0] for row in writes(api)] == ["POST"]
    assert api.checks[1]["conclusion"] == "failure"


def test_same_execution_replay_creates_fresh_failure_and_cannot_reuse_success():
    api = FakeAPI()
    assert status.publish(api, HEAD, ENV)["state"] == "success"
    previous = deepcopy(api.checks[0])
    assert status.publish(api, HEAD, ENV)["state"] == "failure"
    assert api.checks[0] == previous
    assert api.checks[1]["conclusion"] == "failure"
    assert [row[0] for row in writes(api)] == ["POST", "PATCH", "POST"]


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_applied_success_with_lost_response_attempts_owned_failure_rollback(rollback_fails):
    api = FakeAPI()
    api.ambiguous_success = True
    api.rollback_fails = rollback_fails
    verdict = status.publish(api, HEAD, ENV)
    assert verdict["state"] == "failure"
    assert [row[2]["conclusion"] for row in writes(api)] == ["failure", "success", "failure"]
    assert all(row[1].endswith("/10") for row in writes(api)[1:])
    if rollback_fails:
        assert "uncertain" in verdict["reason"]
        assert api.checks[0]["conclusion"] == "success"
    else:
        assert api.checks[0]["conclusion"] == "failure"


def event(body="ordinary comment", canonical=False, action="created"):
    return {"action": action, "repository": {"full_name": REPOSITORY},
            "issue": {"number": 1, "pull_request": {"url": "https://example.invalid/pr/1"}},
            "comment": {"body": body, "user": {"id": CODEX_USER_ID if canonical else 42},
                        "performed_via_github_app": {"id": status.CODEX_APP_ID if canonical else 0},
                        "issue_url": f"https://api.github.com/repos/{REPOSITORY}/issues/1"}}


@pytest.mark.parametrize("body,canonical,action,trigger", [
    ("ordinary comment", False, "created", False),
    ("ordinary comment", False, "deleted", False),
    ("@codex review", False, "created", True),
    ("@codex security review", False, "edited", True),
    (status.SUMMARY_MARKER + "\nsummary", True, "deleted", True),
])
def test_comment_discovery_filters_wakeups(body, canonical, action, trigger):
    api = FakeAPI()
    api.run["event"] = "issue_comment"
    matrix = status.discover(api, dict(ENV, GITHUB_EVENT_NAME="issue_comment"),
                             event(body, canonical, action))
    assert matrix == {"include": [{"head": HEAD}] if trigger else []}
    assert writes(api) == []
    if not trigger:
        assert not any("/pulls?" in row[1] for row in api.calls)


def test_discovery_deduplicates_all_open_heads_and_not_controller_source():
    api = FakeAPI()
    api.prs.extend([pr(2), pr(3, "c" * 40)])
    assert status.discover(api, ENV) == {"include": [{"head": HEAD}, {"head": "c" * 40}]}
    assert writes(api) == []


def test_open_pr_bound_fails_closed():
    api = FakeAPI()
    api.prs = [pr(number) for number in range(1, 202)]
    with pytest.raises(MetadataError, match="bound"):
        status.discover(api, ENV)
    assert writes(api) == []


def test_cli_discovery_exports_matrix_and_cannot_skip_guard(monkeypatch, tmp_path, capsys):
    api = FakeAPI()
    monkeypatch.setattr(status, "GitHub", lambda repo: api)
    monkeypatch.setattr("sys.argv", ["review_status", "--repo", REPOSITORY, "--discover"])
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert status.main() == 0
    assert json.loads(output.read_text().split("=", 1)[1]) == {"include": [{"head": HEAD}]}
    monkeypatch.setenv("GITHUB_REF", "refs/heads/topic")
    assert status.main() == 1
    assert writes(api) == []
    assert "failure" in capsys.readouterr().out


@pytest.mark.parametrize("options,verb", [({"post": True}, "POST"), ({"method": "PATCH"}, "PATCH")])
def test_github_transport_supports_patch_and_retains_post(monkeypatch, options, verb):
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"id": 10}')

    monkeypatch.setattr("scripts.ci.review_admission.subprocess.run", run)
    assert GitHub(REPOSITORY).request("inert-route", payload={"conclusion": "failure"}, **options) == {"id": 10}
    assert captured[0][0] == ["gh", "api", "inert-route", "--method", verb, "--input", "-"]
    assert json.loads(captured[0][1]["input"]) == {"conclusion": "failure"}
