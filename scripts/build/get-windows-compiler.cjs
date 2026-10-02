// Loaded only by the canonical Desktop dependency install subprocess.
// npm replaces npm_config_node_gyp before dependency scripts start; select the
// pinned supplier-local compiler inside that child, before node-pre-gyp runs.
const { readFileSync } = require('node:fs')
const { createRequire } = require('node:module')
const { dirname, join } = require('node:path')

function getWindowsCompilerEnvironment(packageRoot, env = process.env) {
  const requireFromSupplier = createRequire(join(packageRoot, 'package.json'))
  const compiler = requireFromSupplier.resolve('node-gyp/bin/node-gyp.js')
  const manifest = JSON.parse(readFileSync(join(dirname(compiler), '..', 'package.json'), 'utf8'))
  if (manifest.name !== 'node-gyp' || manifest.version !== '13.0.2') {
    throw new Error(`unsupported get-windows source compiler: ${manifest.version}`)
  }
  return {
    ...Object.fromEntries(Object.entries(env).filter(([key]) => !/^npm_config_node_gyp$/i.test(key))),
    npm_config_node_gyp: compiler
  }
}

exports.getWindowsCompilerEnvironment = getWindowsCompilerEnvironment

if (process.env.npm_package_name === 'get-windows' && process.env.npm_lifecycle_event === 'install') {
  const manifestPath = process.env.npm_package_json
  if (!manifestPath) throw new Error('get-windows lifecycle package identity is missing')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  if (manifest.name !== 'get-windows' || manifest.version !== '9.3.0') {
    throw new Error('get-windows lifecycle package identity does not match the pinned supplier')
  }
  const selected = getWindowsCompilerEnvironment(dirname(manifestPath))
  for (const key of Object.keys(process.env)) {
    if (/^npm_config_node_gyp$/i.test(key)) delete process.env[key]
  }
  process.env.npm_config_node_gyp = selected.npm_config_node_gyp
}
