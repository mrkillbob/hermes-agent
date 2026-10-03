"""Reviewed per-cut applicability and targeted CI evidence; no full audit."""
from dataclasses import dataclass, asdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


def _path(value, *, root=False):
    if root and value == ".":
        return value
    if (not isinstance(value, str) or not value or len(value) > 1024 or "\0" in value or "\\" in value
            or value.startswith("/") or any(p in {"", ".", ".."} for p in value.split("/"))):
        raise ValueError("coverage paths must be normalized repository-relative paths")
    return value


def canonical_cwd(value):
    """Normalize portable relative cwd data; never normalize away traversal."""
    if not isinstance(value, str) or PureWindowsPath(value).drive or ":" in value:
        raise ValueError("supplementary cwd must not have a drive or escape source")
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return _path(value, root=True)


def canonical_command(argv, cwd, *, source=None):
    """One classification for plan validation, execution and receipt matching."""
    cwd = canonical_cwd(cwd)
    names = {"run_tests.sh", "run_tests_parallel.py", "run_local_ci_audit.py"}
    matches = [i for i, arg in enumerate(argv) if any(
        re.search(rf"(?:^|[/\\\s]){re.escape(name)}(?:$|\s|\.)", arg, re.IGNORECASE) for name in names)]
    if source is not None and not matches:
        root = Path(source).resolve()
        canonical_files = [root / "scripts" / name for name in names]
        for i, argument in enumerate(argv):
            candidate = root / cwd / argument
            try:
                if candidate.is_file() and any(file.is_file() and candidate.samefile(file) for file in canonical_files):
                    matches.append(i)
            except OSError as error:
                raise ValueError("canonical runner alias identity unavailable") from error
    if not matches:
        return tuple(argv), cwd, False  # Custom workloads still require semantic review.
    if len(matches) != 1:
        raise ValueError("ambiguous canonical runner aliases")
    index = matches[0]
    runner = argv[index].replace("\\", "/")
    while runner.startswith("./"):
        runner = runner[2:]
    runner = runner.lower()
    if PurePosixPath(runner).name == "run_local_ci_audit.py":
        raise ValueError("supplementary work must not replay the full local audit")
    if runner not in {"scripts/run_tests.sh", "scripts/run_tests_parallel.py"}:
        raise ValueError("unknown canonical runner alias")
    if cwd != ".":
        raise ValueError("canonical focused runner must execute at repository root")
    interpreter = PurePosixPath(argv[0].replace("\\", "/")).name
    if index not in {0, 1} or (index == 1 and (
            (runner.endswith(".sh") and interpreter not in {"bash", "bash.exe"})
            or (runner.endswith(".py") and not re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", interpreter)))):
        raise ValueError("unsupported canonical runner invocation alias")
    targets = []
    for argument in argv[index+1:]:
        argument = argument.replace("\\", "/")
        while argument.startswith("./"):
            argument = argument[2:]
        target = _path(argument)
        if not target.startswith("tests/") or not target.endswith(".py") or any(c in target for c in "*?[]"):
            raise ValueError("canonical focused runner accepts only explicit positional test files")
        targets.append(target)
    if len(set(targets)) != len(targets):
        raise ValueError("duplicate canonical focused targets")
    if not targets:
        raise ValueError("supplementary canonical tests require effective focused targets")
    if source is not None:
        root = Path(source).resolve()
        for relative in (runner, *targets):
            candidate = root / relative
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root) or not candidate.is_file() or candidate.is_symlink():
                raise ValueError("canonical focused paths must be real contained files, not directories or aliases")
    return (*argv[:index], runner, *targets), cwd, True


@dataclass(frozen=True, slots=True)
class SupplementaryCommand:
    coverage_id: str
    argv: tuple[str, ...]
    cwd: str


@dataclass(frozen=True, slots=True)
class SupplementaryPlan:
    repository: str
    pr_number: int
    base_sha: str
    head_sha: str
    changed_files: tuple[tuple[str, str, str | None], ...]
    commands: tuple[SupplementaryCommand, ...]
    actions_jobs: tuple[tuple[str, tuple[str, ...]], ...]
    runner_platform: str | None = None

    @classmethod
    def parse(cls, repository, raw, actions_jobs):
        if not isinstance(raw, dict) or not {"pr_number", "base_sha", "head_sha", "changed_files", "commands"} <= set(raw) or set(raw) - {"pr_number", "base_sha", "head_sha", "changed_files", "commands", "runner_platform"}:
            raise ValueError("supplementary plan requires an explicit exact cut, file set and command set")
        runner_platform = raw.get("runner_platform")
        if runner_platform is not None and runner_platform not in {"linux", "darwin", "win32"}:
            raise ValueError("supplementary runner platform invalid")
        number = raw["pr_number"]
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError("supplementary PR identity invalid")
        for key in ("base_sha", "head_sha"):
            if not isinstance(raw[key], str) or not re.fullmatch(r"[0-9a-f]{40}", raw[key]):
                raise ValueError("supplementary SHA identity invalid")
        files = raw["changed_files"]
        if not isinstance(files, list) or not 0 < len(files) < 300:
            raise ValueError("supplementary changed-file set incomplete")
        selected = []
        for row in files:
            if not isinstance(row, dict) or set(row) - {"path", "status", "previous_path"} or not {"path", "status"} <= set(row):
                raise ValueError("supplementary changed-file entry invalid")
            path = _path(row["path"])
            status = row["status"]
            previous = row.get("previous_path")
            if status not in {"added", "removed", "modified", "renamed"} or (status == "renamed") != (previous is not None):
                raise ValueError("supplementary rename/status coverage invalid")
            selected.append((path, status, _path(previous) if previous is not None else None))
        if len({row[0] for row in selected}) != len(selected):
            raise ValueError("duplicate supplementary file")
        commands = raw["commands"]
        if not isinstance(commands, list) or len(commands) > 32:
            raise ValueError("supplementary commands must be explicit and bounded")
        parsed = []
        for row in commands:
            if not isinstance(row, dict) or set(row) != {"id", "argv", "cwd"}:
                raise ValueError("supplementary command invalid")
            identifier, argv = row["id"], row["argv"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier):
                raise ValueError("supplementary coverage ID invalid")
            if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(not isinstance(a, str) or not a or "\0" in a for a in argv):
                raise ValueError("supplementary argv invalid")
            normalized_argv, normalized_cwd, _ = canonical_command(argv, row["cwd"])
            parsed.append(SupplementaryCommand(identifier, normalized_argv, normalized_cwd))
        if len({c.coverage_id for c in parsed}) != len(parsed) or len({(c.argv, c.cwd) for c in parsed}) != len(parsed):
            raise ValueError("duplicate supplementary commands")
        if not actions_jobs:
            raise ValueError("supplementary coverage requires Actions workload mapping")
        return cls(repository, number, raw["base_sha"], raw["head_sha"], tuple(sorted(selected)), tuple(parsed), actions_jobs, runner_platform)

    @property
    def digest(self):
        return hashlib.sha256(json.dumps({"domain": "targeted-supplementary-ci-v2", **asdict(self)},
                             sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def matches(self, scope):
        return bool(scope is not None and scope.repository == self.repository
                    and scope.pr_number == self.pr_number and scope.base_sha == self.base_sha
                    and scope.head_sha == self.head_sha and scope.changed_files == self.changed_files)


def supplementary_blockers(plan, receipt, *, now, max_age_seconds):
    if not plan.commands:
        return ()  # Explicit reviewed empty set; no fictitious empty passed receipt.
    from .ci_runner import CIAuditReceipt, CI_MODE_STANDARD
    if not isinstance(receipt, CIAuditReceipt):
        return ("supplementary_receipt_missing",)
    try:
        receipt.validate()
    except (TypeError, ValueError, AttributeError):
        return ("supplementary_receipt_invalid",)
    identity = receipt.identity
    if ((identity.repository, identity.pr_number, identity.base_sha, identity.head_sha) !=
            (plan.repository, plan.pr_number, plan.base_sha, plan.head_sha)
            or receipt.manifest_digest != plan.digest or receipt.ci_mode != CI_MODE_STANDARD):
        return ("supplementary_identity_mismatch",)
    if (receipt.started_at.tzinfo is None or receipt.completed_at.tzinfo is None
            or receipt.started_at > now or receipt.completed_at > now
            or receipt.completed_at < now - timedelta(seconds=max_age_seconds)):
        return ("supplementary_receipt_stale",)
    try:
        actual = tuple(canonical_command(c.argv, c.cwd)[:2] for c in receipt.commands)
        expected = tuple(canonical_command(c.argv, c.cwd)[:2] for c in plan.commands)
    except (TypeError, ValueError):
        return ("supplementary_receipt_not_passing",)
    if receipt.status != "passed" or actual != expected:
        return ("supplementary_receipt_not_passing",)
    return ()
