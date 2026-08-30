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
  fixture = await setupMockBackend({
    startGateway: true
  })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('renders the lunar city and opens a building detail view', async ({ page: _page }, testInfo) => {
  const page = fixture!.page

  await waitForAppReady(fixture!, 120_000)
  await page.evaluate(() => {
    window.location.hash = '#/starmap'
  })

  await expect(page.getByRole('heading', { name: 'Lunar City' })).toBeVisible()
  await expect(page.getByAltText(/isometric lunar settlement/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /Open Library/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /Open Research Lab/i })).toBeVisible()
  await expect(page.getByText('MISSIONS')).toBeVisible()
  await expect(page.getByRole('button', { name: /Inspect .* worker/ }).first()).toBeVisible()
  await expect(page.getByText('Worker states')).toHaveCount(0)

  const viewport = page.getByTestId('lunar-city-viewport')
  await expect(page.getByRole('button', { name: 'Zoom in' })).toBeVisible()
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await expect(viewport).toHaveAttribute('data-zoom', '1')
  await page.getByRole('button', { name: 'Reset camera' }).click()
  await expect(viewport).toHaveAttribute('data-zoom', '0')
  await page.screenshot({ path: testInfo.outputPath('lunar-city-overview.png'), fullPage: true })

  await page.getByRole('button', { name: /Research Lab/i }).click()
  await expect(page.getByText('Enter building')).toBeVisible()
  await page.getByRole('button', { name: 'Enter building' }).click()
  await expect(page.getByText('Fox Scientist is managing this shift')).toBeVisible()
  await expect(page.getByText('Observatory', { exact: true })).toBeVisible()

  await page.screenshot({ path: testInfo.outputPath('lunar-city-building.png'), fullPage: true })
})
