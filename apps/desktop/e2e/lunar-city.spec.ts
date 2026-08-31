/**
 * Browser-level coverage for the lunar-city visualizer.
 *
 * The test deliberately uses the real desktop shell and mock backend so the
 * screenshot and interaction checks exercise the route as a user sees it.
 */
import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({ mockDispatcherReadiness: true })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('renders the live lunar city canvas and its snapshot-backed accessible controls', async ({
  page: _page
}, testInfo) => {
  const page = fixture!.page
  const lunarRequests: string[] = []

  const recordLunarRequest = (request: { url(): string }) => {
    if (request.url().includes('/lunar-city/')) {
      lunarRequests.push(request.url())
    }
  }

  await waitForAppReady(fixture!, 120_000)
  page.on('request', recordLunarRequest)
  await page.evaluate(() => {
    window.location.hash = '#/lunar-city'
  })

  await expect(page.getByRole('heading', { name: 'Lunar City' })).toBeVisible()
  const canvas = page.getByLabel('Interactive 3D Lunar City')
  await expect(canvas).toBeVisible()
  await expect(canvas).toHaveAttribute('data-world-status', 'ready', { timeout: 30_000 })
  expect(lunarRequests.some(url => /\/v2\/models\/terrain\.glb(?:[?#]|$)/.test(url))).toBe(true)
  expect(lunarRequests.some(url => /moon-settlement-approved\.jpg/i.test(url))).toBe(false)
  await expect(page.getByRole('complementary', { name: 'Lunar City accessible operations' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: /3D quality/i })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Source health' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Open (Library|Research Lab)/i })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Inspect .* worker/i })).toHaveCount(0)
  await expect(page.getByText('MISSIONS')).toHaveCount(0)

  const viewport = page.getByTestId('lunar-city-viewport')
  await expect(page.getByRole('button', { name: 'Zoom In' })).toBeVisible()
  await page.getByRole('button', { name: 'Zoom In' }).click()
  await expect(viewport).not.toHaveAttribute('data-zoom')
  await page.getByRole('button', { name: 'Return to City' }).click()
  await page.screenshot({ path: testInfo.outputPath('lunar-city-overview.png'), fullPage: true })
  page.off('request', recordLunarRequest)
})
