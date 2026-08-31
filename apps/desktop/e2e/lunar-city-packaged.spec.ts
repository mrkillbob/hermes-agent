import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import * as fs from 'node:fs'
import * as path from 'node:path'

import { _electron, type ElectronApplication, type Page } from '@playwright/test'

import { PACKAGED_BINARY_PATH } from './fixtures'
import {
  assertGpuPackagedEligibility,
  createLunarCityPopulationFixture,
  gpuPackagedLaunchOptions,
  type LunarCityPopulationFixture
} from './lunar-city-fixtures'
import { expect, test } from './test'

const DESKTOP_ROOT = path.resolve(import.meta.dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')
const OPT_IN = process.env.HERMES_LUNAR_CITY_PACKAGED_E2E === '1'

let app: ElectronApplication | undefined
let page: Page | undefined
let fixture: LunarCityPopulationFixture | undefined

test.beforeAll(async () => {
  test.skip(!OPT_IN, 'Packaged Lunar City E2E is explicit opt-in and a skip is not acceptance evidence.')
  test.skip(!fs.existsSync(PACKAGED_BINARY_PATH), `Packaged binary is missing: ${PACKAGED_BINARY_PATH}`)

  const stampPath = packagedStampPath(PACKAGED_BINARY_PATH)
  test.skip(!fs.existsSync(stampPath), `Packaged bridge/build stamp is missing: ${stampPath}`)

  const stamp = JSON.parse(fs.readFileSync(stampPath, 'utf8')) as { commit: string; dirty: boolean; source: string }
  const headSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: REPO_ROOT, encoding: 'utf8' }).trim()

  const eligible = assertGpuPackagedEligibility({
    binaryExists: true,
    binaryPath: PACKAGED_BINARY_PATH,
    headSha,
    stamp
  })

  fixture = createLunarCityPopulationFixture(250)
  const nonce = `lunar-city-${randomUUID()}`

  const launch = gpuPackagedLaunchOptions({
    executablePath: eligible.executablePath,
    userDataDir: fixture.userDataDir,
    env: {
      ...stringEnvironment(process.env),
      HERMES_HOME: fixture.hermesHome,
      HERMES_DESKTOP_APP_NAME: `Hermes Lunar City Packaged ${nonce}`,
      HERMES_DESKTOP_SKIP_QUIT_CONFIRM: '1',
      HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1',
      HERMES_LUNAR_CITY_PERF_NONCE: nonce
    }
  })

  app = await _electron.launch(launch)
  page = await app.firstWindow()

  const bridgePresent = await page.evaluate(() =>
    Boolean((window as unknown as Record<string, unknown>).__LUNAR_CITY_PERF__)
  )

  test.skip(!bridgePresent, 'Versioned packaged Lunar City bridge is absent; this run is not acceptance evidence.')
})

test.afterAll(async () => {
  await app?.close().catch(() => undefined)
  fixture?.cleanup()
})

test('observes the exact 250-entity standard-adapter population', async () => {
  const snapshot = await page!.evaluate(async () => {
    const bridge = (
      window as unknown as {
        __LUNAR_CITY_PERF__?: { mountCity(): Promise<void>; snapshot(): Promise<unknown> }
      }
    ).__LUNAR_CITY_PERF__

    if (!bridge) {
      throw new Error('bridge_unavailable')
    }

    await bridge.mountCity()

    return bridge.snapshot()
  })

  expect(snapshot).toMatchObject({
    environment: { electronMode: 'packaged', gpuEnabled: true },
    population: { observed: 250, source: 'lunar-city-snapshot-v1' }
  })
})

function packagedStampPath(binaryPath: string): string {
  if (process.platform === 'darwin') {
    return path.resolve(path.dirname(binaryPath), '..', 'Resources', 'install-stamp.json')
  }

  return path.join(path.dirname(binaryPath), 'resources', 'install-stamp.json')
}

function stringEnvironment(env: NodeJS.ProcessEnv): Record<string, string> {
  return Object.fromEntries(
    Object.entries(env).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
  )
}
