import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from 'vitest'
import { repairCuaSwiftRpaths } from './repair-cua-swift-rpaths.mjs'

const buildPath = '/Applications/Xcode_26.6.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/macosx'

function fixture(systemSwift) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cua-rpath-test-'))
  const dir = path.join(root, 'tools', 'cua-driver-0.21.0-darwin-arm64')
  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, 'cua-driver')
  const flags = ['-Wl,-rpath,' + buildPath, '-Wl,-rpath,@executable_path/../Frameworks']
  if (systemSwift) flags.push('-Wl,-rpath,/usr/lib/swift')
  execFileSync('xcrun', ['clang', '-x', 'c', '-', '-o', file, ...flags], {
    input: 'int main(void) { return 0; }\n', stdio: ['pipe', 'pipe', 'pipe']
  })
  return { root, file }
}

test.runIf(process.platform === 'darwin')('removes only builder Swift rpaths before signing, idempotently', () => {
  const { root, file } = fixture(true)
  try {
    const other = path.join(root, 'other-arch')
    execFileSync('xcrun', ['clang', '-arch', process.arch === 'arm64' ? 'x86_64' : 'arm64',
      '-x', 'c', '-', '-o', other, '-Wl,-rpath,' + buildPath,
      '-Wl,-rpath,/usr/lib/swift', '-Wl,-rpath,@executable_path/../Frameworks'], {
      input: 'int main(void) { return 0; }\n', stdio: ['pipe', 'pipe', 'pipe']
    })
    const combined = path.join(root, 'combined')
    execFileSync('lipo', ['-create', file, other, '-output', combined])
    fs.copyFileSync(combined, file)
    const bundled = path.join(path.dirname(file), 'CuaDriver.app/Contents/MacOS/cua-driver')
    fs.mkdirSync(path.dirname(bundled), { recursive: true })
    fs.copyFileSync(file, bundled)
    expect(repairCuaSwiftRpaths(root)).toBe(2)
    for (const binary of [file, bundled]) {
      const load = execFileSync('otool', ['-l', binary], { encoding: 'utf8' })
      expect(load).not.toContain(buildPath)
      expect(load).toContain('path /usr/lib/swift ')
      expect(load).toContain('path @executable_path/../Frameworks ')
    }
    expect(repairCuaSwiftRpaths(root)).toBe(0)
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
})

test.runIf(process.platform === 'darwin')('validates both universal slices before any mutation', () => {
  const { root, file } = fixture(true)
  try {
    const other = path.join(root, 'other-arch')
    execFileSync('xcrun', ['clang', '-arch', process.arch === 'arm64' ? 'x86_64' : 'arm64',
      '-x', 'c', '-', '-o', other, '-Wl,-rpath,' + buildPath], {
      input: 'int main(void) { return 0; }\n', stdio: ['pipe', 'pipe', 'pipe']
    })
    const combined = path.join(root, 'combined')
    execFileSync('lipo', ['-create', file, other, '-output', combined])
    fs.copyFileSync(combined, file)
    const before = fs.readFileSync(file)
    expect(() => repairCuaSwiftRpaths(root)).toThrow(/system Swift/)
    expect(fs.readFileSync(file)).toEqual(before)
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
})

test.runIf(process.platform === 'darwin')('refuses to remove the only Swift search path without changing bytes', () => {
  const { root, file } = fixture(false)
  try {
    const before = fs.readFileSync(file)
    expect(() => repairCuaSwiftRpaths(root)).toThrow(/system Swift/)
    expect(fs.readFileSync(file)).toEqual(before)
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
})

test.runIf(process.platform === 'darwin')('refuses unknown relative dependencies before editing the binary', () => {
  const { root, file } = fixture(true)
  try {
    const library = path.join(root, 'libfixture.dylib')
    execFileSync('xcrun', ['clang', '-dynamiclib', '-x', 'c', '-', '-o', library,
      '-Wl,-install_name,@rpath/libfixture.dylib'], { input: 'int fixture(void) { return 0; }\n' })
    execFileSync('xcrun', ['clang', '-x', 'c', '-', '-x', 'none', library, '-o', file,
      '-Wl,-rpath,' + buildPath, '-Wl,-rpath,/usr/lib/swift'], {
      input: 'extern int fixture(void); int main(void) { return fixture(); }\n'
    })
    const before = fs.readFileSync(file)
    expect(() => repairCuaSwiftRpaths(root)).toThrow(/unqualified rpath dependencies/)
    expect(fs.readFileSync(file)).toEqual(before)
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
})
