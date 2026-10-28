import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import type { InventoryOwner, SessionInventoryRead } from './attached-session-read'
import { readCurrentAttachedSessions } from './attached-session-source'

export interface AttachedSessionInventoryProps {
  owner: InventoryOwner
}

/** Opt-in host surface: mounting performs no request and does not open or activate a session. */
export function AttachedSessionInventory({ owner }: AttachedSessionInventoryProps) {
  return <AttachedSessionInventoryForOwner key={JSON.stringify([owner.connectionId, owner.profile])} owner={owner} />
}

function AttachedSessionInventoryForOwner({ owner }: AttachedSessionInventoryProps) {
  const { t } = useI18n()
  const copy = t.lunarCityInventory
  const [reading, setReading] = useState(false)
  const [result, setResult] = useState<SessionInventoryRead | null>(null)
  const pending = useRef<AbortController | null>(null)
  const generation = useRef(0)
  useEffect(
    () => () => {
      generation.current += 1
      pending.current?.abort()
    },
    []
  )

  const refresh = async () => {
    pending.current?.abort()
    const controller = new AbortController()
    const attempt = ++generation.current
    pending.current = controller
    setResult(null)
    setReading(true)
    const next = await readCurrentAttachedSessions(owner, controller.signal)

    if (attempt !== generation.current || controller.signal.aborted) {
      return
    }

    setResult(next)
    setReading(false)
  }

  return (
    <section aria-label={copy.title}>
      <h2>{copy.title}</h2>
      <p>{copy.description}</p>
      <Button disabled={reading} onClick={() => void refresh()} type="button">
        {reading ? copy.reading : copy.read}
      </Button>
      <div aria-live="polite">
        {result?.status === 'unavailable' ? (
          <p>{copy.unavailable}</p>
        ) : null}
        {result?.status === 'read' ? (
          <>
            <p>{copy.count(result.entries.length)}</p>
            <ul>
              {result.entries.map(entry => (
                <li key={entry.sessionId}>{entry.sessionId}</li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
    </section>
  )
}
