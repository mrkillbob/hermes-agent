/**
 * Browser-level coverage for the lunar-city visualizer.
 *
 * The test deliberately uses the real desktop shell and mock backend so the
 * screenshot and interaction checks exercise the route as a user sees it.
 */
import { expect, test } from './test'

import { setupMockBackend, waitForAppReady, type MockBackendFixture } from './fixtures'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('renders the lunar city and opens a building detail view', async ({}, testInfo) => {
  const page = fixture!.page

  await waitForAppReady(fixture!, 120_000)
  await page.evaluate(() => {
    window.location.hash = '#/starmap'
  })

  await expect(page.getByRole('heading', { name: 'Lunar City' })).toBeVisible()
  await expect(page.getByAltText(/isometric lunar settlement/i)).toBeVisible()
  await expect(page.getByText('Library')).toBeVisible()
  await expect(page.getByText('Research Lab')).toBeVisible()

  await page.getByRole('button', { name: /Research Lab/i }).click()
  await expect(page.getByText('Enter building')).toBeVisible()
  await page.getByRole('button', { name: 'Enter building' }).click()
  await expect(page.getByText('Lunabot Research Lab Director')).toBeVisible()
  await expect(page.getByText('Observatory')).toBeVisible()

  await page.screenshot({ path: testInfo.outputPath('lunar-city-building.png'), fullPage: true })
})
