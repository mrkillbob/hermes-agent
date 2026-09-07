#!/usr/bin/env node

/**
 * Quarantined asset-pack importer. It never writes into public/lunar-city.
 * Use this to stage a CC0 or separately licensed pack for benchmark review.
 */
import { cp, mkdir, readdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { basename, join, resolve } from 'node:path'

const source = process.argv[2]
const destination = process.argv[3] ?? resolve('/tmp/lunar-city-asset-benchmarks', basename(source ?? 'missing-pack'))

if (!source) {
  console.error('usage: node import-open-asset-pack.mjs <source-pack> [quarantine-destination]')
  process.exitCode = 2
} else {
  const sourcePath = resolve(source)
  const destinationPath = resolve(destination)

  if (destinationPath.includes(`${join('apps', 'desktop', 'public', 'lunar-city')}/`)) {
    throw new Error('refusing to import third-party assets into the shipped Lunar City bundle')
  }

  await mkdir(destinationPath, { recursive: true })
  await cp(sourcePath, destinationPath, { recursive: true, force: true })

  const files = []
  const visit = async directory => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) await visit(path)
      else {
        const bytes = await readFile(path)
        files.push({
          bytes: bytes.byteLength,
          path: path.slice(destinationPath.length + 1),
          sha256: createHash('sha256').update(bytes).digest('hex')
        })
      }
    }
  }

  await visit(destinationPath)
  const receipt = {
    source: sourcePath,
    destination: destinationPath,
    importedAt: new Date().toISOString(),
    files,
    reviewRequired: true,
    notes: 'Record exact license, creator, URL, modification and redistribution rights before use.'
  }
  await writeFile(join(destinationPath, 'asset-receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`)
  console.log(JSON.stringify(receipt, null, 2))
}
