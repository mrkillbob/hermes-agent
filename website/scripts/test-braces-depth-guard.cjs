const test = require('node:test');
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const micromatch = require('micromatch');
const chokidar = require('chokidar');
const fastGlob = require('fast-glob');
const bracesFor = name => createRequire(require.resolve(name))('braces');
const consumers = ['micromatch', 'chokidar'].map(bracesFor);

test('brace matching and file consumers preserve ordinary patterns', async () => {
  for (const braces of consumers) {
    assert.deepEqual(braces.expand('file-{a,b}-{1..2}.md'), ['file-a-1.md', 'file-a-2.md', 'file-b-1.md', 'file-b-2.md']);
    const ast = braces.parse('file-{a,b}.md');
    assert.equal(braces.stringify(ast), 'file-{a,b}.md');
    assert.ok(new RegExp(`^${braces.compile(ast)}$`).test('file-a.md'));
  }
  assert.deepEqual(micromatch(['a.md', 'b.mdx', 'c.txt'], '*.{md,mdx}'), ['a.md', 'b.mdx']);
  const cwd = await fs.mkdtemp(path.join(os.tmpdir(), 'hermes-braces-'));
  let watcher;
  try {
    await Promise.all(['a.md', 'b.mdx', 'c.txt'].map(file => fs.writeFile(path.join(cwd, file), 'test')));
    assert.deepEqual((await fastGlob('*.{md,mdx}', {cwd})).sort(), ['a.md', 'b.mdx']);
    const found = [];
    watcher = chokidar.watch('*.{md,mdx}', {cwd});
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('watcher did not become ready')), 5000);
      watcher.on('add', file => found.push(file)).once('error', error => {clearTimeout(timeout); reject(error);});
      watcher.once('ready', () => {clearTimeout(timeout); resolve();});
    });
    assert.deepEqual(found.sort(), ['a.md', 'b.mdx']);
  } finally {
    if (watcher) await watcher.close();
    await fs.rm(cwd, {recursive: true, force: true});
  }
});

test('all public processors reject excessive nesting and invalid numeric limits', () => {
  for (const braces of consumers) {
    const nested = '{'.repeat(101) + 'a,b' + '}'.repeat(101);
    for (const fn of [braces, braces.parse, braces.compile, braces.expand, braces.stringify]) {
      assert.throws(() => fn(nested), /depth/i);
    }
    const ast = {type: 'root', nodes: [{type: 'text', value: 'x'}]};
    let node = ast;
    for (let i = 0; i < 101; i++) {node.nodes = [{type: 'brace', nodes: []}]; node = node.nodes[0];}
    for (const fn of [braces.compile, braces.expand, braces.stringify]) assert.throws(() => fn(ast), /depth/i);
    for (const fn of [braces.parse, braces.compile, braces.expand, braces.stringify]) {
      assert.throws(() => fn('a{b,c}', {maxDepth: -1}), /maxDepth/);
    }
    assert.throws(() => braces.parse('x'.repeat(10001), {maxLength: NaN}), /maxLength/);
    assert.throws(() => braces.parse(nested, {maxDepth: Infinity}), /depth/i);
    assert.throws(() => braces.parse(nested, {maxDepth: 1000}), /depth/i);
  }
});
