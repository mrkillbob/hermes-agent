#!/usr/bin/env node

/** Curate a large downloaded asset folder into a small Blender benchmark set. */
import { cp, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";

const [sourceArg, destinationArg = "/tmp/lunar-city-open-asset-curated", additionalRootArg] = process.argv.slice(2);
if (!sourceArg) {
  console.error("Usage: node curate-open-asset-pack.mjs <download-root> [destination] [additional-root]");
  process.exit(2);
}

const source = path.resolve(sourceArg);
const destination = path.resolve(destinationArg);
const additionalRoot = additionalRootArg ? path.resolve(additionalRootArg) : null;
const allowedDirectories = [
  "kenney_space-station-kit", "kenney_modular-space-kit_1", "kenney_space-kit",
  "kenney_city-kit-roads", "kenney_city-kit-industrial_2", "kenney_factory-kit_3",
  "kenney_modular-buildings", "kenney_building-kit", "kenney_nature-kit",
  "kenney_retro-fantasy-kit", "kenney_furniture-kit", "kenney_3d-road-tiles",
  "Cyberpunk Game Kit - Quaternius", "Ultimate Fantasy RTS - Aug 2022",
  "Ultimate Stylized Nature - May 2022", "Updated Modular Dungeon - May 2019",
];
const keywords = /(road|pavement|station|factory|industrial|building|wall|roof|window|door|lamp|light|rock|tree|plant|grass|crystal|crate|barrel|furniture|bench|terminal|pipe|bridge|tower|dungeon|space|sci.?fi)/i;
const extensions = new Set([".glb", ".gltf", ".fbx", ".obj"]);
const maxFiles = 220;

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(full)));
    else if (extensions.has(path.extname(entry.name).toLowerCase())) out.push(full);
  }
  return out;
}

const candidates = [];
for (const name of allowedDirectories) {
  const dir = path.join(source, name);
  try {
    for (const file of await walk(dir)) {
      const stem = path.basename(file, path.extname(file));
      const score = (keywords.test(stem) ? 10 : 0) + (path.extname(file).toLowerCase() === ".glb" ? 3 : 0);
      candidates.push({ file, score, source: path.relative(source, file) });
    }
  } catch {
    // A missing optional kit is fine; the receipt records only selected files.
  }
}
// Optional focused source (for example Kenney's Starter Kit City Builder).
// Keep it additive and deterministic so existing large-pack curation remains
// backwards compatible while preserving the source root in the receipt.
if (additionalRoot) {
  try {
    for (const file of await walk(additionalRoot)) {
      const stem = path.basename(file, path.extname(file));
      const score = 100 + (keywords.test(stem) ? 10 : 0) + (path.extname(file).toLowerCase() === ".glb" ? 3 : 0);
      candidates.push({ file, score, source: path.join(path.basename(additionalRoot), path.relative(additionalRoot, file)) });
    }
  } catch {
    // An optional focused kit is allowed to be absent.
  }
}
candidates.sort((a, b) => b.score - a.score || a.source.localeCompare(b.source));
const selected = candidates.slice(0, maxFiles);
await mkdir(destination, { recursive: true });
const receipt = [];
for (const [index, item] of selected.entries()) {
  const safeName = `${String(index + 1).padStart(3, "0")}__${item.source.replaceAll(path.sep, "__")}`;
  const target = path.join(destination, safeName);
  await cp(item.file, target);
  const digest = createHash("sha256").update(await readFile(target)).digest("hex");
  receipt.push({ source: item.source, stagedFile: safeName, sha256: digest, licenseReviewRequired: true });
}
await writeFile(path.join(destination, "curation-receipt.json"), JSON.stringify({ source, additionalRoot, selected: receipt.length, maxFiles, receipt }, null, 2) + "\n");
console.log(JSON.stringify({ destination, selected: receipt.length, availableCandidates: candidates.length, additionalRoot }, null, 2));
