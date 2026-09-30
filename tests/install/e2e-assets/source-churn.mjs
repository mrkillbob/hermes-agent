// Only disposable installer E2E clones call this helper; Git retains every
// excluded contributor mapping so the historical release is still auditable.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export function normalizeInstallerChurn(root) {
  const git = (args, options = {}) => execFileSync('git', ['-C', root, ...args], options);
  const status = git(['status', '--porcelain', '--untracked-files=all'], { encoding: 'utf8' });
  if (!status.trim()) return;
  const entries = git(['ls-files', '--stage', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean);
  const groups = new Map();
  for (const entry of entries) {
    const [metadata, name] = entry.split('\t');
    if (!/^contributors\/emails\/[A-Za-z0-9@._+-]+$/.test(name)) continue;
    const key = name.toLowerCase();
    const group = groups.get(key) ?? [];
    group.push({ name, blob: metadata.split(' ')[1] });
    groups.set(key, group);
  }
  const collisions = [...groups.values()].filter(group => group.length > 1
    && group.some(({ name }) => status.split('\n').some(line => line.slice(3) === name)));
  // Validate every collided file before modifying any of them. A user edit
  // must fail; matching any indexed spelling proves checkout-generated churn.
  const files = [];
  for (const group of collisions) {
    const blobs = group.map(({ blob }) => git(['cat-file', 'blob', blob]));
    for (const { name } of group) {
      const file = path.join(root, name);
      if (!fs.existsSync(file)) continue;
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || !blobs.some(blob => blob.equals(fs.readFileSync(file)) || blob.equals(Buffer.from(fs.readFileSync(file).toString('utf8').replaceAll('\r\n', '\n'))))) {
        throw new Error(`Unverified contributor edit: ${name}`);
      }
      if (!files.some(entry => entry.stat.dev === stat.dev && entry.stat.ino === stat.ino)) {
        files.push({ file, stat, bytes: fs.readFileSync(file) });
      }
    }
  }
  if (collisions.length) {
    if (fs.existsSync(path.join(git(['rev-parse', '--absolute-git-dir'], { encoding: 'utf8' }).trim(), 'info', 'sparse-checkout'))) {
      throw new Error('Refusing to replace an existing sparse checkout');
    }
    const patterns = ['/*', ...collisions.flatMap(group => group.map(({ name }) => `!/${name}`))];
    // These files already exist verbatim in Git. Save a local rollback as well
    // before removing the single case-folded filesystem representation.
    const gitDir = git(['rev-parse', '--absolute-git-dir'], { encoding: 'utf8' }).trim();
    const backup = fs.mkdtempSync(path.join(gitDir, 'e2e-contributor-backup-'));
    files.forEach(({ bytes }, i) => fs.writeFileSync(path.join(backup, String(i)), bytes));
    try {
      files.forEach(({ file }) => fs.unlinkSync(file));
      git(['sparse-checkout', 'set', '--no-cone', '--stdin'], { input: patterns.join('\n') + '\n' });
    } catch (error) {
      files.forEach(({ file, bytes, stat }) => fs.writeFileSync(file, bytes, { mode: stat.mode }));
      throw error;
    }
  }
  const generated = ['bin/hermes.exe', 'bin/hermes-acp.exe'];
  const gitDir = git(['rev-parse', '--absolute-git-dir'], { encoding: 'utf8' }).trim();
  for (const name of generated) {
    if (!status.split('\n').includes(`?? ${name}`)) continue;
    const file = path.join(root, name);
    if (!fs.lstatSync(file).isFile() || fs.readFileSync(file).subarray(0, 2).toString() !== 'MZ') {
      throw new Error(`Unverified installer launcher: ${name}`);
    }
    fs.appendFileSync(path.join(gitDir, 'info', 'exclude'), `\n/${name}\n`);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  try { normalizeInstallerChurn(path.resolve(process.argv[2])); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
