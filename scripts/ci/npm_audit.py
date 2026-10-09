#!/usr/bin/env python3
"""Audit each changed npm lockfile; unavailable comparisons fail closed."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def changed_directories(base: str, head: str) -> list[str]:
    for sha in (base, head):
        if not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
            raise ValueError('Audit comparison requires full base and head commit SHAs')
    # Direct tree comparison needs no merge base, including in depth-1 checkouts.
    diff = subprocess.run(
        ['git', 'diff', '--no-renames', '--diff-filter=ACMT', '--name-only', '-z', base, head, '--'],
        check=True, capture_output=True, timeout=30,
    )
    directories = sorted({
        str(Path(os.fsdecode(path)).parent)
        for path in diff.stdout.split(b'\0') if path and Path(os.fsdecode(path)).name == 'package-lock.json'
    })
    if not directories:
        # Workflow edits select this lane too. A valid empty diff audits every
        # tracked lockfile, rather than silently treating root as the target.
        tracked = subprocess.run(['git', 'ls-files', '-z'], check=True, capture_output=True, timeout=30)
        directories = sorted({str(Path(os.fsdecode(path)).parent)
                              for path in tracked.stdout.split(b'\0')
                              if path and Path(os.fsdecode(path)).name == 'package-lock.json'})
    if not directories:
        raise ValueError('npm audit lane selected but no tracked lockfiles remain to audit')
    root = Path.cwd().resolve()
    for directory in directories:
        for name in ('package.json', 'package-lock.json'):
            path = Path(directory) / name
            if not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError(f'Cannot audit {directory}: missing or external {name}')
    return directories


def audit(base: str, head: str) -> dict:
    result = {'audits': [], 'high': 0, 'critical': 0, 'errors': []}
    try:
        directories = changed_directories(base, head)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result['errors'].append(f'Lockfile comparison failed: {exc}')
        return result
    for directory in directories:
        entry = {'directory': directory, 'exit_code': 1, 'report': None}
        result['audits'].append(entry)
        try:
            completed = subprocess.run(
                ['npm', 'audit', '--package-lock-only', '--ignore-scripts', '--audit-level=high', '--json'],
                cwd=directory, capture_output=True, text=True, timeout=180,
            )
            entry['exit_code'] = completed.returncode
            report = json.loads(completed.stdout)
            entry['report'] = report
            counts = report['metadata']['vulnerabilities']
            for severity in ('high', 'critical'):
                count = counts[severity]
                if type(count) is not int or count < 0:
                    raise ValueError(f'Invalid {severity} advisory count')
            if report.get('error') or not isinstance(report.get('vulnerabilities'), dict):
                raise ValueError('npm audit returned an error or incomplete report')
            for severity in ('high', 'critical'):
                result[severity] += counts[severity]
            if completed.returncode and not (counts['high'] or counts['critical']):
                result['errors'].append(f'{directory}: npm audit exited {completed.returncode}: {completed.stderr.strip()}')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            result['errors'].append(f'{directory}: npm audit failed: {exc}')
    return result


def review_status(result: dict) -> list:
    failed = result['errors'] or result['high'] or result['critical'] or any(entry['exit_code'] for entry in result['audits'])
    if not failed:
        return []
    names = sorted({name for entry in result['audits'] if isinstance(entry['report'], dict)
                    and isinstance(entry['report'].get('vulnerabilities'), dict)
                    for name in entry['report']['vulnerabilities']})
    return [{'source': 'supply chain', 'results': [{
        'kind': 'action_required',
        'title': 'npm audit requires action',
        'summary': f"npm audit found {result['high']} high and {result['critical']} critical advisories.",
        'detail': 'Affected packages: ' + ', '.join(names[:25]) + '; Errors: ' + '; '.join(result['errors']),
        'how_to_fix': 'Resolve audit coverage/errors and update affected pins or overrides, regenerate package-lock.json, and rerun npm audit --audit-level=high.',
    }]}]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--report', default='npm-audit.json')
    args = parser.parse_args()
    result = audit(args.base, args.head)
    status = review_status(result)
    exit_code = int(bool(status))
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write(f'exit_code={exit_code}\nreview_status={json.dumps(status)}\n')
    print(json.dumps(result, indent=2))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
