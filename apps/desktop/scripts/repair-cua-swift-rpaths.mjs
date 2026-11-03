// CUA 0.21.0 retains its producer's Xcode Swift search paths. Those paths
// fail Apple's distribution check even though macOS provides Swift itself.
// Repair only the packaged copies, before signing and payload rehashing.
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const buildSwift = /^\/Applications\/Xcode[^/]*\.app\/Contents\/Developer\/Toolchains\/XcodeDefault\.xctoolchain\/usr\/lib\/swift(?:-\d+\.\d+)?\/macosx$/

export function repairCuaSwiftRpaths(payload) {
  const directory = path.join(payload, 'tools')
  if (!fs.existsSync(directory)) return 0
  if (fs.lstatSync(directory).isSymbolicLink()) throw new Error('CUA repair refuses linked tools')
  const tools = fs.realpathSync(directory)
  const plans = []
  for (const entry of fs.readdirSync(tools, { withFileTypes: true })) {
    if (!entry.isDirectory() || !/^cua-driver-\d[^/]*-darwin-(?:arm64|x64)$/.test(entry.name)) continue
    for (const relative of ['cua-driver', 'CuaDriver.app/Contents/MacOS/cua-driver']) {
      const file = path.join(tools, entry.name, relative)
      if (!fs.existsSync(file)) continue
      if (!fs.lstatSync(file).isFile() || fs.realpathSync(file) !== file) {
        throw new Error(`CUA Swift repair refuses a linked binary: ${file}`)
      }
      const load = execFileSync('otool', ['-l', file], { encoding: 'utf8' })
      const slices = load.split(/^.*\(architecture [^)]+\):\s*$/m).filter(s => s.includes('Load command'))
      const removals = new Set()
      for (const slice of slices) {
        const paths = [...slice.matchAll(/cmd LC_RPATH\s+cmdsize \d+\s+path (.+) \(offset \d+\)/g)].map(m => m[1])
        const stale = paths.filter(p => buildSwift.test(p))
        if (!stale.length) continue
        if (!paths.includes('/usr/lib/swift')) throw new Error(`CUA repair requires system Swift rpath: ${file}`)
        const relativeLoads = [...slice.matchAll(/name (@rpath\/[^\n]+) \(offset \d+\)/g)].map(m => m[1])
        if (relativeLoads.some(name => name !== '@rpath/libswift_Concurrency.dylib')) {
          throw new Error(`CUA repair refuses unqualified rpath dependencies: ${file}`)
        }
        for (const p of stale) removals.add(p)
      }
      if (removals.size) plans.push({ file, removals: [...removals] })
    }
  }
  // Validate all inputs before touching any packaged bytes. Tool failures abort
  // packaging; no artifact may be signed after a partial repair.
  for (const { file, removals } of plans) {
    execFileSync('install_name_tool', [...removals.flatMap(p => ['-delete_rpath', p]), file], { stdio: 'pipe' })
  }
  return plans.length
}
