#!/usr/bin/env node
// Exercise the worker API consumed by Docusaurus even when its SSG pool is disabled.
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import Tinypool from "tinypool";

const deadline = setTimeout(() => {
  console.error("Docusaurus Tinypool compatibility check exceeded 30 seconds");
  process.exit(1);
}, 30_000);
deadline.unref();

const directory = await mkdtemp(join(tmpdir(), "hermes-tinypool-compat-"));
let pool;
try {
  const workerPath = join(directory, "worker.mjs");
  await writeFile(
    workerPath,
    `import { isMainThread, workerData } from "node:worker_threads";
export default function executeTask(task) {
  return {
    task,
    params: workerData?.[1]?.params,
    workerId: process.__tinypool_state__?.workerId,
    isMainThread,
  };
}
`,
  );
  const params = { locale: "en", compatibilityReceipt: true };
  pool = new Tinypool({
    filename: pathToFileURL(workerPath).pathname,
    minThreads: 2,
    maxThreads: 2,
    concurrentTasksPerWorker: 1,
    runtime: "worker_threads",
    isolateWorkers: false,
    workerData: { params },
    maxMemoryLimitBeforeRecycle: 512 * 1024 * 1024,
    resourceLimits: {},
  });
  const tasks = [
    { id: 1, pathnames: ["/docs/intro"] },
    { id: 2, pathnames: ["/docs/setup"] },
  ];
  const results = await Promise.all(tasks.map((task) => pool.run(task)));
  for (const [index, result] of results.entries()) {
    assert.deepEqual(result.task, tasks[index]);
    assert.deepEqual(result.params, params);
    assert.equal(result.isMainThread, false);
    assert.ok(Number.isInteger(result.workerId) && result.workerId > 0);
  }
} finally {
  try {
    if (pool) {
      await pool.destroy();
      assert.equal(pool.threads.length, 0);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
    clearTimeout(deadline);
  }
}
console.log("Docusaurus Tinypool compatibility passed: run, workerData, worker ID, destroy");
