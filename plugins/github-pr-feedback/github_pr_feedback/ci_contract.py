"""Resolve repository CI ownership without requiring LunaBot scripts in Hermes."""
from pathlib import Path
import json
import tomllib

_HERMES_MANIFEST = Path(__file__).with_name('hermes_native_ci.toml')


def manifest_path(worktree: Path) -> Path:
    legacy = worktree / 'tests/manifests/test_lanes.toml'
    if legacy.is_file():
        return legacy
    try:
        project = tomllib.loads((worktree / 'pyproject.toml').read_text())['project']
        if project.get('name') == 'hermes-agent' and (worktree / 'scripts/run_tests.sh').is_file():
            return _HERMES_MANIFEST
    except (OSError, ValueError, KeyError):
        pass
    return legacy


def is_hermes_contract(payload: bytes) -> bool:
    return tomllib.loads(payload.decode('utf-8')).get('schema') == 'hermes-native-ci-v1'


def hermes_commands(worktree: Path, base_sha: str, head_sha: str, changed: tuple[str, ...]):
    if any(p.endswith((".rs", ".ps1", ".nix")) or p.startswith(".github/") or Path(p).name.startswith("Dockerfile") for p in changed):
        raise ValueError("Hermes local CI requires additional platform or workflow coverage for this diff")
    commands = [
        (('git', 'diff', '--check', f'{base_sha}..{head_sha}'), worktree, {}),
        (('uv', 'lock', '--check'), worktree, {}),
        (('bash', 'scripts/run_tests.sh'), worktree, {}),
    ]
    for package in ('apps/desktop', 'apps/shared', 'ui-tui', 'web', 'website'):
        if not any(p == package or p.startswith(package + '/') for p in changed):
            continue
        root = worktree / package
        if not (root / 'package.json').is_file():
            continue
        if not (root / 'package-lock.json').is_file():
            raise ValueError(f'Hermes CI package lock missing: {package}')
        scripts = json.loads((root / 'package.json').read_text()).get('scripts', {})
        commands.append((('npm', 'ci', '--ignore-scripts'), root, {}))
        for name in ('lint', 'typecheck', 'test', 'build'):
            if name in scripts:
                commands.append((('npm', 'run', name), root, {'CI': 'true'}))
    return commands
