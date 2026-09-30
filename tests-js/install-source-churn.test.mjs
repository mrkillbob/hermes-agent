import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { afterEach, expect, test } from 'vitest';
import { normalizeInstallerChurn } from '../tests/install/e2e-assets/source-churn.mjs';

const roots = [];
afterEach(() => roots.splice(0).forEach(root => fs.rmSync(root, { recursive: true, force: true })));

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'installer-churn-'));
  roots.push(root);
  const git = (args, options = {}) => execFileSync('git', ['-C', root,
    '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
    '-c', 'commit.gpgsign=false', ...args], { encoding: 'utf8', ...options }).trim();
  git(['init']);
  const names = ['contributors/emails/agent@Agents-Mac-mini.local',
    'contributors/emails/agent@agents-Mac-mini.local'];
  for (const [i, name] of names.entries()) {
    const blob = git(['hash-object', '-w', '--stdin'], { input: `mapping ${i}\n` });
    git(['update-index', '--add', '--cacheinfo', `100644,${blob},${name}`]);
  }
  git(['commit', '-m', 'historical case collision']);
  fs.mkdirSync(path.dirname(path.join(root, names[0])), { recursive: true });
  fs.writeFileSync(path.join(root, names[1]), 'mapping 1\r\n');
  return { root, names, git };
}

test('normalizes only verified historical metadata and generated PE launchers', () => {
  const { root, names, git } = fixture();
  const head = git(['rev-parse', 'HEAD']);
  fs.mkdirSync(path.join(root, 'bin'));
  for (const name of ['hermes.exe', 'hermes-acp.exe']) {
    fs.writeFileSync(path.join(root, 'bin', name), 'MZfixture');
  }
  normalizeInstallerChurn(root);
  expect(git(['status', '--porcelain', '--untracked-files=all'])).toBe('');
  expect(git(['rev-parse', 'HEAD'])).toBe(head);
  expect(git(['show', `HEAD:${names[0]}`])).toBe('mapping 0');
  expect(git(['show', `HEAD:${names[1]}`])).toBe('mapping 1');
  expect(fs.readFileSync(path.join(root, 'bin/hermes.exe'), 'utf8')).toBe('MZfixture');
  expect(fs.readdirSync(path.join(root, '.git')).some(name => name.startsWith('e2e-contributor-backup-'))).toBe(true);
});

test('refuses modified contributor content without deleting it', () => {
  const { root, names, git } = fixture();
  const file = path.join(root, names[1]);
  fs.writeFileSync(file, 'user edit\n');
  expect(() => normalizeInstallerChurn(root)).toThrow('Unverified contributor edit');
  expect(fs.readFileSync(file, 'utf8')).toBe('user edit\n');
  expect(git(['status', '--porcelain'])).not.toBe('');
  expect(fs.existsSync(path.join(root, '.git/info/sparse-checkout'))).toBe(false);
});
