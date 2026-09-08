"""Resolve repository CI ownership without requiring LunaBot scripts in Hermes."""
from pathlib import Path
import json
import tomllib

HERMES_ENV_CHECK = ('uv', 'sync', '--locked', '--check', '--no-install-project',
                    '--extra', 'all', '--extra', 'dev', '--extra', 'anthropic',
                    '--extra', 'mistral', '--extra', 'fal', '--extra', 'modal',
                    '--extra', 'daytona', '--extra', 'hindsight', '--extra', 'parallel-web')

_HERMES_MANIFEST = Path(__file__).with_name('hermes_native_ci.toml')
_DESKTOP_PACKAGE = 'apps/desktop'
_DESKTOP_NATIVE_CHECKS = ('check:test:desktop:all',)


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
    commands = [
        (('git', 'diff', '--check', f'{base_sha}..{head_sha}'), worktree, {}),
        (('uv', 'lock', '--check'), worktree, {}),
        (HERMES_ENV_CHECK, worktree, {}),
        (('bash', 'scripts/run_tests.sh'), worktree, {}),
    ]
    root_lock = worktree / 'package-lock.json'
    locked_packages = json.loads(root_lock.read_text()).get('packages', {}) if root_lock.is_file() else {}
    root_installed = False
    shared_changed = any(p.startswith('apps/shared/') or p in {'package.json', 'package-lock.json'} for p in changed)
    desktop_changed = shared_changed or any(
        p == _DESKTOP_PACKAGE or p.startswith(_DESKTOP_PACKAGE + '/') for p in changed
    )
    for package in ('apps/desktop', 'apps/shared', 'ui-tui', 'web', 'website'):
        if not shared_changed and not any(p == package or p.startswith(package + '/') for p in changed):
            continue
        root = worktree / package
        if not (root / 'package.json').is_file():
            continue
        if package in locked_packages:
            if not root_installed:
                install = ('npm', 'ci') if desktop_changed else ('npm', 'ci', '--ignore-scripts')
                commands.append((install, worktree, {}))
                root_installed = True
        elif (root / 'package-lock.json').is_file():
            install = ('npm', 'ci') if package == _DESKTOP_PACKAGE else ('npm', 'ci', '--ignore-scripts')
            commands.append((install, root, {}))
        else:
            raise ValueError(f'Hermes CI package lock missing: {package}')
        scripts = json.loads((root / 'package.json').read_text()).get('scripts', {})
        if 'build:ink' in scripts:
            commands.append((('npm', 'run', 'build:ink'), root, {'CI': 'true'}))
        for name in ('lint', 'typecheck', 'test', 'build'):
            if name in scripts:
                commands.append((('npm', 'run', name), root, {'CI': 'true'}))
        if package == _DESKTOP_PACKAGE:
            for name in _DESKTOP_NATIVE_CHECKS:
                if name in scripts:
                    commands.append((('npm', 'run', name), root, {'CI': 'true'}))
    return commands


def hermes_coverage_gap(
    changed: tuple[str, ...], *, hosted_coverage_available: bool = False
) -> str | None:
    # The hosted desktop lane drives Electron under Linux/Xvfb; native CI runs
    # the package's deterministic checks but cannot replace that acceptance lane.
    if not hosted_coverage_available and any(
        p.startswith("apps/desktop/")
        or p.endswith((".rs", ".ps1", ".nix"))
        or p.startswith(".github/")
        or Path(p).name.startswith("Dockerfile")
        for p in changed
    ):
        return "additional platform or workflow coverage is required beyond host-native CI"
    return None
