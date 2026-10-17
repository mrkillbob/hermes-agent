"""Real GitHub converters/feedback parser with a bounded inert JSON transport."""

import copy
import json

import pytest
from github_pr_feedback.github_client import GitHubClient
from .test_intake_only import Kanban, REPO, run


class Transport:
    def __init__(self, changed_field=None, changed_read=None):
        self.changed_field, self.changed_read = changed_field, changed_read
        self.detail_reads = 0
        self.listing = {
            "number": 7, "state": "OPEN", "isDraft": False,
            "headRepository": {"nameWithOwner": REPO}, "author": {"login": "mrkillbob"},
            "headRefName": "feature", "headRefOid": "a" * 40,
            "baseRefName": "stable", "baseRefOid": "b" * 40,
            "updatedAt": "2026-10-17T10:00:00Z", "labels": [],
        }
        self.detail = {
            "number": 7, "state": "open", "draft": False,
            "base": {"repo": {"full_name": REPO}, "ref": "stable", "sha": "b" * 40},
            "head": {"repo": {"full_name": REPO}, "ref": "feature", "sha": "a" * 40},
            "user": {"login": "mrkillbob"}, "labels": [],
            "updated_at": "2026-10-17T10:00:00Z",
        }

    def run(self, argv):
        if argv[1:3] == ["pr", "list"]:
            return json.dumps([self.listing])
        if argv == ["gh", "api", f"repos/{REPO}/pulls/7"]:
            self.detail_reads += 1
            row = copy.deepcopy(self.detail)
            if self.detail_reads == self.changed_read:
                mutations = {
                    "number": lambda: row.update(number=8),
                    "state": lambda: row.update(state="closed"),
                    "base_repository": lambda: row["base"]["repo"].update(full_name="other/repo"),
                    "head_repository": lambda: row["head"]["repo"].update(full_name="other/repo"),
                    "author_login": lambda: row["user"].update(login="other"),
                    "head_ref_name": lambda: row["head"].update(ref="other"),
                    "head_sha": lambda: row["head"].update(sha="c" * 40),
                    "labels": lambda: row.update(labels=[{"name": "hold"}]),
                    "base_branch": lambda: row["base"].update(ref="other"),
                    "base_sha": lambda: row["base"].update(sha="c" * 40),
                    "is_draft": lambda: row.update(draft=True),
                    "updated_at": lambda: row.update(updated_at="2026-10-17T10:01:00Z"),
                }
                mutations[self.changed_field]()
            return json.dumps(row)
        assert argv[:4] == ["gh", "api", "--paginate", "--slurp"]
        comments = [{"id": 101, "user": {"login": "trusted", "type": "User"},
                     "body": "Please address this finding", "created_at": "2026-10-17T10:15:00Z"}]
        return json.dumps([comments if "/issues/" in argv[-1] else []])


def test_real_list_detail_and_feedback_converters_allow_same_pr(tmp_path):
    kanban = Kanban()
    result = run(tmp_path, GitHubClient(Transport()), kanban)
    assert result["status"] == "ok" and result["created"] == 1
    assert kanban.creates == 1


@pytest.mark.parametrize("read", [1, 2, 3])
@pytest.mark.parametrize("field", [
    "number", "state", "base_repository", "head_repository", "author_login",
    "head_ref_name", "head_sha", "labels", "base_branch", "base_sha", "is_draft", "updated_at",
])
def test_shared_pr_identity_change_at_any_reread_holds_delivery(tmp_path, field, read):
    kanban = Kanban()
    result = run(tmp_path, GitHubClient(Transport(field, read)), kanban)
    assert result["status"] == ("held" if read == 3 else "partial")
    assert result["created"] == 0 and kanban.creates == 0


@pytest.mark.parametrize("marker", [None, "malformed", "missing"])
def test_real_detail_missing_or_invalid_marker_cannot_match_list(tmp_path, marker):
    transport = Transport()
    if marker == "missing":
        transport.detail.pop("updated_at")
    else:
        transport.detail["updated_at"] = marker
    kanban = Kanban()
    result = run(tmp_path, GitHubClient(transport), kanban)
    assert result["status"] == "partial"
    assert result["created"] == 0 and kanban.creates == 0
