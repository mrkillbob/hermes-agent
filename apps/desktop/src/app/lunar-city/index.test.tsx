import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider, type Locale } from '@/i18n'
import { $worldEnabled, $worldOnboardingDismissed, WORLD_ONBOARDING_DISMISSED_STORAGE_KEY } from '@/store/lunar-city'

import { LunarCity } from './index'

const renderWorld = (initialLocale: Locale = 'en') => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider configClient={null} initialLocale={initialLocale}>
        <MemoryRouter>
          <LunarCity />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>
  )
}

const localizedSurfaceCopy = {
  ja: {
    setup: 'Lunar Cityワールドのセットアップ',
    useDefaults: 'Hermesのデフォルトを使用',
    skip: '今はスキップ',
    scene: 'Lunar Cityシーン',
    dispatcher: 'ディスパッチャーコンパニオン',
    newTask: '新しいタスク'
  },
  ar: {
    setup: 'إعداد عالم مدينة القمر',
    useDefaults: 'استخدم إعدادات Hermes الافتراضية',
    skip: 'تخطَّ الآن',
    scene: 'مشهد مدينة القمر',
    dispatcher: 'مساعد الموزّع',
    newTask: 'مهمة جديدة'
  },
  ru: {
    setup: 'Настройка мира Лунного города',
    useDefaults: 'Использовать настройки Hermes',
    skip: 'Пропустить пока',
    scene: 'Сцена Лунного города',
    dispatcher: 'Помощник диспетчера',
    newTask: 'Новая задача'
  },
  'zh-hant': {
    setup: '月城世界設定',
    useDefaults: '使用 Hermes 預設設定',
    skip: '暫時略過',
    scene: '月城場景',
    dispatcher: '派遣助手',
    newTask: '新增任務'
  }
} as const

beforeEach(() => {
  localStorage.clear()
  $worldEnabled.set(true)
  $worldOnboardingDismissed.set(false)
})

afterEach(cleanup)

describe('LunarCity', () => {
  it('renders its surface copy through the active locale', () => {
    renderWorld('ja')
    fireEvent.click(screen.getByRole('button', { name: localizedSurfaceCopy.ja.useDefaults }))

    expect(screen.getByRole('heading', { name: 'Hermesの世界' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Your Hermes world' })).toBeNull()
  })

  it.each(Object.entries(localizedSurfaceCopy))('renders the full Lunar City surface in %s', (locale, copy) => {
    localStorage.clear()
    $worldEnabled.set(true)
    $worldOnboardingDismissed.set(false)
    renderWorld(locale as Locale)

    expect(screen.getByRole('dialog', { name: copy.setup })).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.skip })).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.useDefaults })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: copy.useDefaults }))

    expect(screen.getByRole('region', { name: copy.scene })).toBeTruthy()
    expect(screen.getByRole('region', { name: copy.dispatcher })).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.newTask })).toBeTruthy()
    cleanup()
  })

  it('shows first-open onboarding with Hermes defaults', () => {
    renderWorld()

    expect(screen.getByRole('dialog', { name: 'Lunar City world setup' })).toBeTruthy()
    expect(screen.getByText('Hermes built-in pets and assets')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Use Hermes defaults' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Use Hermes defaults' }))

    expect(screen.queryByRole('dialog', { name: 'Lunar City world setup' })).toBeNull()
    expect(localStorage.getItem(WORLD_ONBOARDING_DISMISSED_STORAGE_KEY)).toBe('true')
  })

  it('accepts local files as review-only asset metadata', () => {
    renderWorld()
    const dropZone = screen.getByTestId('world-asset-drop-zone')
    const file = new File(['placeholder'], 'moon.glb', { type: 'model/gltf-binary' })

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })

    expect(screen.getByText('moon.glb')).toBeTruthy()
    expect(screen.getAllByText(/Review only/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/no importer is connected/i).length).toBeGreaterThan(0)
  })

  it('makes the world unavailable when Enable World is disabled', () => {
    $worldEnabled.set(false)

    renderWorld()

    expect(screen.getByRole('status', { name: 'World disabled' })).toBeTruthy()
    expect(screen.queryByRole('dialog', { name: 'Lunar City world setup' })).toBeNull()
    expect(screen.queryByTestId('world-asset-drop-zone')).toBeNull()
  })
})
