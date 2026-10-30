import { setResumeExhaustedSessionId, setResumeFailedSessionId } from '@/store/session'
import { $sessionTiles, closeSessionTile } from '@/store/session-states'

export function preparePrimarySessionResume(storedSessionId: string): void {
  // A session is EITHER the main thread OR a tile — never both. openSessionTile
  // enforces this from the tile side (it refuses to tile the selected session);
  // this enforces it from the main side. Loading an existing session into main
  // (cold-start restore, a pasted/⌘K route, a notification jump) while it's also
  // an open tile would paint the same transcript twice — the workspace pane from
  // the route and the tile pane in parallel, both fighting one runtime. Drop the
  // now-redundant tile so main owns it. Runs before the async awaits below (and
  // before the selection listener homes focus) so the tile is gone the same tick
  // the route takes over; the warm cache/runtime binding survives for main to reuse.
  if ($sessionTiles.get().some(t => t.storedSessionId === storedSessionId)) {
    closeSessionTile(storedSessionId)
  }

  // Optimistically clear any prior resume-failure latch for this session:
  // we're attempting a fresh resume, so the self-heal in use-route-resume
  // must not keep treating it as stranded. It's re-armed below only if THIS
  // attempt fails terminally (RPC reject + REST fallback failure).
  setResumeFailedSessionId(current => (current === storedSessionId ? null : current))
  // Also clear the exhausted-latch: a fresh attempt (manual Retry, reconnect,
  // reselect) gives the bounded auto-retry counter a clean cycle, so the
  // chat view drops the error state and shows the loader again.
  setResumeExhaustedSessionId(current => (current === storedSessionId ? null : current))
}
