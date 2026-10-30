import { useStoreSelector } from '@/lib/use-session-slice'
import { $subagentsBySession, activeSubagentCount, failedSubagentCount } from '@/store/subagents'

export function useStatusbarSubagentCounts(primaryActiveSessionId: string | null) {
  // The indicator must speak the same scope as the Spawn-tree panel it opens:
  // running/queued from every session (never background system actions), plus
  // terminal rows only for the session the user is in — the scope
  // `subagentsForPanel` derives, so the count and the tree can never disagree
  // and finished history from inactive sessions stops accumulating (#75505).
  // Only two COUNTS are read, so select scalars — a whole-map `useStore` re-ran
  // this hook (rebuilding all ~9 statusbar items) on every subagent progress
  // tick in ANY session, including background ones.
  const subagentsRunning = useStoreSelector($subagentsBySession, bySession =>
    Object.values(bySession).reduce((sum, items) => sum + activeSubagentCount(items), 0)
  )

  // Terminal rows only from the session the user is in — the panel drops other
  // sessions' finished history (#75505), so the count the indicator shows must
  // not resurrect it. Live running/queued rows stay cross-session above.
  const subagentsFailed = useStoreSelector($subagentsBySession, bySession =>
    Object.entries(bySession)
      .filter(([sid]) => sid === primaryActiveSessionId)
      .reduce((sum, [, items]) => sum + failedSubagentCount(items), 0)
  )

  return { subagentsRunning, subagentsFailed }
}
