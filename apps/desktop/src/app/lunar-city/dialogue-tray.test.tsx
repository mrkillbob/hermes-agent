import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider, type Locale } from '@/i18n'

import { DialogueTray } from './dialogue-tray'
import type { WorldActionRunner } from './world-actions'

const localizedDialogueCopy = {
  ja: { label: '世界内の対話', inspect: 'ブロッカーを確認', close: '対話を閉じる' },
  ar: { label: 'حوار داخل العالم', inspect: 'فحص العائق', close: 'إغلاق الحوار' },
  ru: { label: 'Диалог внутри мира', inspect: 'Изучить блокер', close: 'Закрыть диалог' },
  'zh-hant': { label: '世界內對話', inspect: '檢查阻礙原因', close: '關閉對話' }
} as const

describe('DialogueTray', () => {
  it('shows source truth and only allowed actions for a blocked task', () => {
    render(
      <DialogueTray
        onAction={vi.fn<WorldActionRunner['run']>(async () => ({ kind: 'completed', ok: true }))}
        subject={{
          condition: {
            active: true,
            facts: {},
            id: 'condition-7',
            kind: 'task.blocked',
            scope: 'task',
            severity: 'warning',
            source: 'kanban',
            sourceRef: { taskId: 'task-7' },
            title: 'Fix auth'
          },
          detail: 'dependency failed',
          title: 'Fix auth'
        }}
      />
    )

    expect(screen.getByText('task.blocked')).toBeTruthy()
    expect(screen.getByText('dependency failed')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Inspect blocker' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Approve review' })).toBeNull()
  })

  it.each(Object.entries(localizedDialogueCopy))('renders dialogue controls in %s', (locale, copy) => {
    render(
      <I18nProvider configClient={null} initialLocale={locale as Locale}>
        <DialogueTray
          onAction={vi.fn<WorldActionRunner['run']>(async () => ({ kind: 'completed', ok: true }))}
          onClose={vi.fn()}
          subject={{
            condition: {
              active: true,
              facts: {},
              id: 'condition-7',
              kind: 'task.blocked',
              scope: 'task',
              severity: 'warning',
              source: 'kanban',
              sourceRef: { taskId: 'task-7' },
              title: 'Fix auth'
            },
            title: 'Fix auth'
          }}
        />
      </I18nProvider>
    )

    expect(screen.getByText(copy.label)).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.inspect })).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.close })).toBeTruthy()
  })
})
