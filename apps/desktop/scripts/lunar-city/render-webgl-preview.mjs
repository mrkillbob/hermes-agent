/**
 * Manual visual QA helper: renders a handful of the approved v2 GLB models
 * through a *real* WebGL2 context (via headless Chromium) using the same
 * lighting rig as `src/app/lunar-city/world/world-scene.ts` (key/rim/fill
 * lights, glow layer, image-processing contrast/vignette).
 *
 * `render-turntable.mjs` is fast and deterministic but re-implements its own
 * flat-shaded projector, so it cannot show lighting, glow, or material
 * changes. This script is the opposite trade: genuine PBR-lit pixels, but
 * not deterministic across GPUs/drivers, so it is not a regression check —
 * treat the output as a screenshot for a human to look at, not a test
 * fixture to assert against.
 *
 * Usage: node scripts/lunar-city/render-webgl-preview.mjs [output.png]
 */
import { mkdtemp, writeFile, cp } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { extname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from '@playwright/test'
import esbuild from 'esbuild'

const ASSET_ROOT = fileURLToPath(new URL('../../public/lunar-city/v2/', import.meta.url))
const DEFAULT_OUTPUT_URL = new URL(
  '../../../../.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/webgl-preview.png',
  import.meta.url
)

const ENTRY_SOURCE = `
import { Engine } from '@babylonjs/core/Engines/engine'
import { Scene } from '@babylonjs/core/scene'
import { ArcRotateCamera } from '@babylonjs/core/Cameras/arcRotateCamera'
import { Vector3 } from '@babylonjs/core/Maths/math.vector'
import { Color3, Color4 } from '@babylonjs/core/Maths/math.color'
import { DirectionalLight } from '@babylonjs/core/Lights/directionalLight'
import { HemisphericLight } from '@babylonjs/core/Lights/hemisphericLight'
import { GlowLayer } from '@babylonjs/core/Layers/glowLayer'
import { ShadowGenerator } from '@babylonjs/core/Lights/Shadows/shadowGenerator'
import { ImportMeshAsync } from '@babylonjs/core/Loading/sceneLoader'
import '@babylonjs/loaders/glTF'

// Mirrors the lighting rig authored in src/app/lunar-city/world/world-scene.ts.
async function main() {
  const canvas = document.getElementById('canvas')
  const engine = new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true })
  const scene = new Scene(engine)
  scene.clearColor = new Color4(0.09, 0.06, 0.07, 1)
  scene.ambientColor = new Color3(0.46, 0.32, 0.28)

  new ArcRotateCamera('cam', -Math.PI / 3.1, 1.05, 82, new Vector3(0, 3.5, 4), scene).attachControl(canvas, false)

  const keyLight = new DirectionalLight('key', new Vector3(-0.62, -0.55, 0.56), scene)
  keyLight.position = new Vector3(70, 62, -64)
  keyLight.shadowMinZ = 1
  keyLight.shadowMaxZ = 320
  keyLight.intensity = 0.85
  keyLight.diffuse = new Color3(1, 0.86, 0.68)

  const rimLight = new DirectionalLight('rim', new Vector3(0.6, -0.25, -0.55), scene)
  rimLight.intensity = 0.28
  rimLight.diffuse = new Color3(0.55, 0.72, 0.95)
  rimLight.shadowEnabled = false

  const fillLight = new HemisphericLight('fill', new Vector3(0, 1, 0), scene)
  fillLight.intensity = 0.32
  fillLight.diffuse = new Color3(0.55, 0.64, 0.82)
  fillLight.groundColor = new Color3(0.34, 0.24, 0.22)
  fillLight.specular = new Color3(0, 0, 0)
  fillLight.shadowEnabled = false

  const glow = new GlowLayer('glow', scene, { mainTextureRatio: 0.5 })
  glow.intensity = 0.42

  const shadows = new ShadowGenerator(1024, keyLight)
  shadows.usePercentageCloserFiltering = true
  shadows.filteringQuality = 0
  shadows.darkness = 0.42
  shadows.bias = 0.0018
  shadows.normalBias = 0.012

  scene.fogMode = 3
  scene.fogColor = new Color3(0.09, 0.06, 0.08)
  scene.fogStart = 84
  scene.fogEnd = 288

  if (scene.imageProcessingConfiguration) {
    scene.imageProcessingConfiguration.toneMappingEnabled = true
    scene.imageProcessingConfiguration.toneMappingType = 1
    scene.imageProcessingConfiguration.contrast = 1.34
    scene.imageProcessingConfiguration.exposure = 1.25
    scene.imageProcessingConfiguration.vignetteEnabled = true
    scene.imageProcessingConfiguration.vignetteWeight = 1.6
    scene.imageProcessingConfiguration.vignetteColor = new Color3(0.04, 0.02, 0.04)
  }

  // Placements come from the manifest itself, not a hand-copied snapshot --
  // a hardcoded position list here silently drifts from reality the next
  // time DISTRICTS/world-manifest.v2.json's layout changes (this cost real
  // debugging time once already: the preview kept showing buildings at
  // their old spots while the terrain's walkways had already moved).
  const manifest = await fetch('v2/world-manifest.v2.json').then(response => response.json())
  const placements = manifest.models
    .filter(model => model.transform)
    .map(model => ({ uri: 'v2/' + model.uri, pos: model.transform.position }))
  // The manifest places exactly one workers.glb; scatter a few extra
  // copies around the plaza purely for this preview's visual variety.
  for (const pos of [
    [-18, 0, -12],
    [17, 0, -16],
    [-19, 0, 15],
    [12, 0, 18]
  ])
    placements.push({ uri: 'v2/models/workers.glb', pos })

  for (const { uri, pos } of placements) {
    const result = await ImportMeshAsync(uri, scene)
    for (const mesh of result.meshes) {
      if (!mesh.parent) {
        mesh.position.x += pos[0]
        mesh.position.y += pos[1]
        mesh.position.z += pos[2]
      }
      mesh.receiveShadows = true
      if (!uri.includes('terrain') && mesh.getTotalVertices() > 0) {
        shadows.getShadowMap().renderList.push(mesh)
      }
    }
  }

  scene.render()
  await scene.whenReadyAsync()
  scene.render()
  const map = shadows.getShadowMap()
  console.log('SHADOWDIAG casters=' + (map && map.renderList ? map.renderList.length : 'null')
    + ' lightShadowEnabled=' + keyLight.shadowEnabled
    + ' caps.textureFloat=' + engine.getCaps().textureFloat
    + ' caps.textureHalfFloatRender=' + engine.getCaps().textureHalfFloatRender
    + ' isWebGL2=' + engine.webGLVersion
    + ' mapReady=' + (map ? map.isReady() : 'n/a'))
  window.__ready = true
}

main().catch(error => {
  window.__ready = 'error:' + error.message
  console.error(error)
})
`

const MIME_TYPES = Object.freeze({
  '.glb': 'model/gltf-binary',
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.png': 'image/png'
})

async function serveDirectories(roots) {
  const server = createServer(async (request, response) => {
    for (const [prefix, root] of roots) {
      if (!request.url.startsWith(prefix)) continue
      const relative = request.url.slice(prefix.length)
      try {
        const { readFile } = await import('node:fs/promises')
        const bytes = await readFile(join(root, relative))
        response.writeHead(200, { 'content-type': MIME_TYPES[extname(relative)] ?? 'application/octet-stream' })
        response.end(bytes)
      } catch {
        response.writeHead(404)
        response.end('not found')
      }
      return
    }
    response.writeHead(404)
    response.end('not found')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return { close: () => server.close(), port: server.address().port }
}

async function renderWebglPreview(outputUrl = DEFAULT_OUTPUT_URL) {
  const workDir = await mkdtemp(join(tmpdir(), 'lunar-city-webgl-preview-'))
  const desktopRoot = fileURLToPath(new URL('../../', import.meta.url))
  await esbuild.build({
    // resolveDir, not absWorkingDir, is what makes esbuild resolve the
    // virtual stdin entry's bare "@babylonjs/..." imports against this
    // workspace's node_modules instead of the entry's real (tmpdir) location.
    bundle: true,
    format: 'esm',
    logLevel: 'warning',
    outfile: join(workDir, 'bundle.js'),
    stdin: { contents: ENTRY_SOURCE, loader: 'js', resolveDir: desktopRoot }
  })
  await writeFile(
    join(workDir, 'index.html'),
    '<!doctype html><html><head><meta charset="utf-8">' +
      '<style>html,body{margin:0;background:#17111a}canvas{width:1400px;height:900px;display:block}</style>' +
      '</head><body><canvas id="canvas"></canvas><script type="module" src="./bundle.js"></script></body></html>'
  )

  const assetsDir = join(workDir, 'v2')
  await cp(ASSET_ROOT, assetsDir, { recursive: true })

  const server = await serveDirectories([
    ['/v2/', assetsDir],
    ['/', workDir]
  ])

  try {
    const browser = await chromium.launch({
      args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'],
      // Override only for environments whose pre-provisioned Chromium build
      // doesn't match this workspace's pinned @playwright/test version;
      // normal setups should rely on `npx playwright install` instead.
      executablePath: process.env.LUNAR_CITY_PREVIEW_CHROMIUM || undefined
    })
    try {
      const page = await browser.newPage({ viewport: { width: 1400, height: 900 } })
      // Forward page diagnostics: a silently black or unshadowed render is
      // otherwise impossible to debug from the outside.
      page.on('console', message => console.log(`[page] ${message.text()}`))
      page.on('pageerror', error => console.log(`[page error] ${error.message}`))
      await page.goto(`http://127.0.0.1:${server.port}/index.html`)
      // These callbacks run inside the page (browser context), not here in
      // Node — `window` is real there, just not a global ESLint knows about
      // for a .mjs script.
      /* eslint-disable no-undef */
      const ready = await page
        .waitForFunction(() => window.__ready !== undefined, { timeout: 60000 })
        .then(() => page.evaluate(() => window.__ready))
      /* eslint-enable no-undef */
      if (ready !== true) throw new Error(`preview scene failed to render: ${ready}`)
      await writeFile(outputUrl, await page.screenshot())
    } finally {
      await browser.close()
    }
  } finally {
    server.close()
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const target = process.argv[2] ? new URL(`file://${resolve(process.cwd(), process.argv[2])}`) : DEFAULT_OUTPUT_URL
  await renderWebglPreview(target)
  console.log(`wrote ${fileURLToPath(target)}`)
}

export { renderWebglPreview }
