"""One narrowly governed private Luna issue from a fresh pure-unit failure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


class PublicationError(ValueError):
    """A static, payload-free rejection suitable for worker handoffs."""


REPOSITORY = "mrkillbob/luna-bot"
_FIELDS = {
    "schema_version",
    "repository",
    "expected_stable_head",
    "worktree",
    "test_nodeid",
    "source_owner",
    "expected_exit_code",
    "task_id",
    "profile",
}
_SHA = re.compile(r"[0-9a-f]{40}")
_NODE = re.compile(
    r"tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+"
)
_UNSAFE = re.compile(r"broker|live|integration|smoke|slow|submit|order|account", re.I)
_TEMPLATE_IDS = {
    "dedupe_marker",
    "summary",
    "repository_identity",
    "command_result",
    "environment_reproduction",
    "expected_actual",
    "evidence_class",
    "evidence",
    "safety",
    "next_step",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def _relative_file(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+\.py", value):
        raise PublicationError("invalid repository-relative owner")
    p = Path(value)
    if (
        p.is_absolute()
        or ".." in p.parts
        or not (root / p).resolve().is_relative_to(root)
    ):
        raise PublicationError("owner escapes worktree")
    if not (root / p).is_file():
        raise PublicationError("owner does not exist")
    _git(root, "ls-files", "--error-unmatch", value)
    return p


def _validate_local(packet: dict, policy: Any, expected: str) -> Path:
    if (
        not policy.enabled
        or REPOSITORY not in policy.targets
        or packet["repository"] != REPOSITORY
    ):
        raise PublicationError("repository is not an enabled Luna policy target")
    if (
        not isinstance(expected, str)
        or not _SHA.fullmatch(expected)
        or packet["expected_stable_head"] != expected
    ):
        raise PublicationError("expected stable head binding is invalid")
    worktree = packet.get("worktree", Path.cwd())
    if not isinstance(worktree, (str, Path)):
        raise PublicationError("worktree binding must be a local path")
    root = Path(worktree)
    if root.resolve() != Path.cwd().resolve():
        raise PublicationError(
            "packet worktree must match the dispatcher current directory"
        )
    if not root.is_absolute() or not root.is_dir():
        raise PublicationError("worktree must be an existing absolute local path")
    root = root.resolve()
    canonical = Path(policy.targets[REPOSITORY].local_path).resolve()
    if _git(root, "rev-parse", "--show-toplevel") != str(root):
        raise PublicationError("worktree is not a repository root")
    if _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") != _git(
        canonical, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ):
        raise PublicationError("worktree does not belong to the configured repository")
    if _git(root, "remote", "get-url", "origin") not in {
        "https://github.com/mrkillbob/luna-bot.git",
        "git@github.com:mrkillbob/luna-bot.git",
    }:
        raise PublicationError("origin is not the canonical Luna fork")
    if _git(root, "rev-parse", "HEAD") != expected or _git(
        root, "status", "--porcelain", "--untracked-files=no"
    ):
        raise PublicationError("worktree is dirty or does not match stable head")
    node = packet["test_nodeid"]
    if not isinstance(node, str) or not _NODE.fullmatch(node) or _UNSAFE.search(node):
        raise PublicationError(
            "unsupported_external_repro: requires a named pure-unit test"
        )
    _relative_file(root, node.split("::", 1)[0])
    _relative_file(root, packet["source_owner"])
    if packet["expected_exit_code"] != 1 or isinstance(
        packet["expected_exit_code"], bool
    ):
        raise PublicationError("reproduction must expect a focused assertion failure")
    if packet["profile"] != "lunabot-issue-reporter" or not re.fullmatch(
        r"t_[0-9a-f]{8}", str(packet["task_id"])
    ):
        raise PublicationError("invalid reporter task binding")
    return root


def _argv(packet: dict) -> list[str]:
    return [
        "./.venv/bin/python",
        "-m",
        "pytest",
        "-q",
        "-m",
        "not integration and not smoke and not slow",
        packet["test_nodeid"],
    ]


def command_fingerprint(packet: dict) -> str:
    return hashlib.sha256(
        json.dumps(_argv(packet), separators=(",", ":")).encode()
    ).hexdigest()


def fingerprint_marker(packet: dict) -> str:
    identity = [
        REPOSITORY,
        packet["test_nodeid"],
        "pytest_assertion_failure",
        packet["source_owner"],
    ]
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()
    ).hexdigest()
    return f"<!-- hermes-worker-failure:v1 sha256={digest} -->"


def _run_reproduction(root: Path, packet: dict) -> dict:
    # No credential-bearing parent environment is passed to the test process.
    with tempfile.TemporaryDirectory(prefix="luna-issue-repro-") as home:
        env = {
            "PATH": os.defpath,
            "HOME": home,
            "TMPDIR": home,
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        with tempfile.TemporaryFile() as output:
            try:
                result = subprocess.run(
                    _argv(packet),
                    cwd=root,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise PublicationError(
                    "reproduction timed out; no issue admitted"
                ) from error
            size = output.tell()
            if size > 1_000_000:
                raise PublicationError(
                    "reproduction output exceeded the evidence budget"
                )
            output.seek(0)
            raw = output.read()
    # Only canonical failure metadata leaves this local capture, never assertions or logs.
    match = re.search(rb"\b([1-9][0-9]*) failed\b", raw)
    return {
        "exit_code": result.returncode,
        "failed": int(match[1]) if match else 0,
        "output_sha256": hashlib.sha256(raw).hexdigest(),
        "output_bytes": size,
    }


def _body(root: Path, packet: dict, evidence: dict) -> str:
    import yaml

    template_path = root / ".github/ISSUE_TEMPLATE/hermes-worker-test-failure.yml"
    if not template_path.is_file() or template_path.stat().st_size > 32_768:
        raise PublicationError("required worker issue template is unavailable")
    template = yaml.safe_load(template_path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(template, dict)
        or {row.get("id") for row in template.get("body", [])} != _TEMPLATE_IDS
    ):
        raise PublicationError("worker issue template contract does not match")
    branch = (
        _git(root, "symbolic-ref", "--short", "-q", "HEAD")
        if _git(root, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD"
        else "detached"
    )
    # Branch names can contain operator branding; only the detached/attached shape is published.
    shape = "detached" if branch == "detached" else "attached (name withheld)"
    head = packet["expected_stable_head"]
    legacy = f"hermes-worker:{REPOSITORY}:{head}:{command_fingerprint(packet)}"
    return "\n\n".join([
        "Hermes automated verified unit-failure report (lunabot-issue-reporter)",
        f"### Dedupe marker\n{legacy}\n{fingerprint_marker(packet)}",
        f"### Failure summary\nA fresh focused unit assertion fails in `{packet['test_nodeid']}`. Canonical owner: `{packet['source_owner']}`.",
        f"### Repository, revision, task, and profile identity\nRepository: {REPOSITORY}\nBase branch: stable\nBase SHA: {head}\nHead branch: {shape}\nHead SHA: {head}\nTask ID: {packet['task_id']}\nProfile: lunabot-issue-reporter",
        f"### Exact command and exit code\nCommand argv: {json.dumps(_argv(packet))}\nExit code: 1",
        "### Environment and reproduction\nIsolated credential-free environment; pure-unit marker selection; 120-second cap. Run the single recorded test node on the recorded stable revision.",
        f"### Expected and actual behavior\nExpected: focused unit test passes.\nActual pytest terminal diagnostic: `{evidence['failed']} failed`.",
        "### Strongest evidence class\nStatic or unit evidence",
        f"### Redacted evidence and artifacts\nLocal output SHA256: {evidence['output_sha256']}\nOutput bytes: {evidence['output_bytes']}\nRaw output and private paths withheld.",
        "### Safety checks\n- [x] No live orders or broker/account state changes.\n- [x] No GitHub Actions dispatch, rerun, cancel or CI controls.\n- [x] No merge, approval, force push or repository settings change.\n- [x] No secrets, account identifiers, private paths or operator data in this report.",
        f"### Bounded next step\nReproduce and inspect `{packet['source_owner']}`; reuse existing root-cause repair work. Stop if the focused assertion passes or the exact revision changes.",
    ])


def publish_issue(
    policy: Any,
    packet_path: Path,
    *,
    expected_stable_head: str,
    client_factory: Callable,
    dry_run: bool = False,
) -> dict:
    if packet_path.stat().st_size > 16_384:
        raise PublicationError("packet exceeds the bounded evidence budget")
    packet = json.loads(packet_path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(packet, dict)
        or set(packet) not in (_FIELDS, _FIELDS - {"worktree"})
        or packet["schema_version"] != 1
    ):
        raise PublicationError("unsupported reproduction packet schema")
    root = _validate_local(packet, policy, expected_stable_head)
    if dry_run:
        return {
            "status": "validated",
            "repository": REPOSITORY,
            "marker": fingerprint_marker(packet),
            "github_accessed": False,
            "reproduction_executed": False,
        }
    identity = getattr(policy, "github_identity", None)
    if identity is None or identity.expected_login != "mrkillbobbot":
        raise PublicationError(
            "policy must require mrkillbobbot before credential resolution"
        )
    github = client_factory(policy)
    if github.viewer_login() != "mrkillbobbot":
        raise PublicationError("publication requires verified mrkillbobbot identity")
    if not github.repository_is_private(REPOSITORY):
        raise PublicationError("Luna issue publication requires a private repository")
    if github.get_branch_head(REPOSITORY, "stable") != expected_stable_head:
        raise PublicationError("canonical stable head changed")
    evidence = _run_reproduction(root, packet)
    _validate_local(packet, policy, expected_stable_head)
    if evidence["exit_code"] != 1 or not evidence.get("failed"):
        return {"status": "idle", "reason": "no verified focused assertion failure"}
    body = _body(root, packet, evidence)
    issues, prs = github.issue_publication_landscape(REPOSITORY)
    marker = fingerprint_marker(packet)

    def matching(row):
        if "body" not in row:
            raise PublicationError("issue/PR landscape body is incomplete")
        text = row["body"]
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise PublicationError("issue/PR landscape body is incomplete")
        legacy = re.findall(
            r"hermes-worker:mrkillbob/luna-bot:[0-9a-f]{40}:([0-9a-f]{64})", text
        )
        return (
            marker in text
            or command_fingerprint(packet) in legacy
            or (
                "hermes-worker:" in text
                and packet["test_nodeid"] in text
                and packet["source_owner"] in text
            )
        )

    for rows, status in [(issues, "duplicate"), (prs, "competing_pr")]:
        for row in rows:
            if matching(row):
                return {"status": status, "number": row["number"], "marker": marker}
    _validate_local(packet, policy, expected_stable_head)
    if (
        github.viewer_login() != "mrkillbobbot"
        or not github.repository_is_private(REPOSITORY)
        or github.get_branch_head(REPOSITORY, "stable") != expected_stable_head
    ):
        raise PublicationError("publication identity/privacy/head changed before write")
    issue = github.create_verified_luna_issue(
        REPOSITORY,
        expected_stable_head=expected_stable_head,
        title=f"[HERMES TEST] Verified unit failure in {packet['test_nodeid'].split('::')[0]}",
        body=body,
    )
    return {
        "status": "created",
        "number": issue["number"],
        "url": issue["html_url"],
        "marker": marker,
        "repository": REPOSITORY,
    }


def handle_publish_issue(ctx: Any, args: Any) -> int:
    from .cli import _github_client, _load_policy_from_context
    from .github_client import GitHubClientError

    try:
        if args.repository != REPOSITORY:
            raise PublicationError(
                "CLI repository does not match Luna publication scope"
            )
        result = publish_issue(
            _load_policy_from_context(ctx),
            Path(args.packet),
            expected_stable_head=args.expected_stable_head,
            client_factory=_github_client,
            dry_run=args.dry_run,
        )
    except (
        ValueError,
        OSError,
        subprocess.SubprocessError,
        GitHubClientError,
    ) as error:
        # Exceptions may include paths or subprocess payloads; report only structural type.
        print(
            json.dumps({
                "status": "rejected",
                "error_class": type(error).__name__,
                "reason": str(error)
                if isinstance(error, PublicationError)
                else "publication evidence unavailable",
            })
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
