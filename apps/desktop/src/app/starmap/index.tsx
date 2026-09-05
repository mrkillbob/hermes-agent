import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { useI18n } from '@/i18n'
import { $starmapError, $starmapGraph, $starmapLoading, loadStarmapGraph } from '@/store/starmap'
import type { StarmapGraph } from '@/types/hermes'

import { LunarCity } from '../lunar-city'
import { Panel, PanelEmpty } from '../overlays/panel'

import { StarMap } from './star-map'

// Star map overlay: a top-down map of what Hermes has learned for a profile,
// over a radial time axis. Data is fetched on demand into the $starmap* atoms;
// the map itself lives in ./star-map. The chrome is owned by the map itself
// (timeline scrubber + legend float over the canvas), so there's no panel
// header here.
export function StarmapView({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const graph = useStore($starmapGraph)
  const loading = useStore($starmapLoading)
  const error = useStore($starmapError)

  // A pasted share code populates the map with someone else's (or an exported)
  // graph, overriding the live profile scan. Cleared by "back to my map" and
  // whenever a fresh profile graph loads in.
  const [imported, setImported] = useState<StarmapGraph | null>(null)
  const [showMemoryGraph, setShowMemoryGraph] = useState(false)

  useEffect(() => {
    void loadStarmapGraph()
  }, [])

  // Drop a stale import when the underlying profile graph changes out from under it.
  useEffect(() => {
    setImported(null)
  }, [graph])

  const shown = imported ?? graph

  return (
    <Panel closeLabel={t.starmap.close} contentClassName="px-0 pb-0" fullBleed onClose={onClose}>
      {!showMemoryGraph ? (
        <LunarCity onOpenMemoryGraph={() => setShowMemoryGraph(true)} />
      ) : error ? (
        <PanelEmpty description={error} icon="warning" title={t.starmap.loadFailed} />
      ) : !shown && loading ? (
        <PageLoader aria-label={t.starmap.loading} className="min-h-0 flex-1" />
      ) : shown && shown.nodes.length === 0 && !imported ? (
        <PanelEmpty description={t.starmap.emptyDesc} icon="lightbulb" title={t.starmap.emptyTitle} />
      ) : shown ? (
        <StarMap
          graph={shown}
          imported={imported !== null}
          onImport={setImported}
          onResetMap={() => setImported(null)}
        />
      ) : null}
      {showMemoryGraph ? (
        <div className="absolute left-4 top-3 z-30">
          <button className="text-xs text-(--ui-accent) hover:underline" onClick={() => setShowMemoryGraph(false)}>
            ← Lunar City
          </button>
        </div>
      ) : null}
    </Panel>
  )
}
