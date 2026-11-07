# Lunar City concepts

This directory is the canonical home for Lunar City creative briefs because the
world-event and presentation contracts live alongside the Lunar City app code.

## Brief contract

Every concept brief must identify:

1. The world-event kind(s) it decorates, using the exact keys produced by
   `../world-events.ts`.
2. The presentation target: `sceneTag`, `animationTags`, and cosmetic intensity
   (`0`–`3`), following `../world-presentation.ts`.
3. The asset-manifest path it needs to extend, if any. Worker appearance belongs
   to `workerVocabulary` and declared kits in `apps/desktop/public/lunar-city/`
   (parsed in `../manifest.ts`); leader identity is constrained by `LEADER_IDS`
   and declared leader kits. Do not pitch a new NPC class, palette, or leader as
   an ingestible identity without an explicit manifest/schema and kit-extension
   plan. Presentation-only concepts may use existing assets without claiming a
   new class.
4. A tracked Kanban task for follow-up implementation or review. Do not use an
   untracked “next scene” or “next unworld” backlog pointer.

The presentation map is world-event kind → `sceneTag` + `animationTags` +
cosmetic intensity, with `WorldScope` (`worker | task | building | district |
city`) retained from the event. “Unworld” is not part of this contract.

## Fallback-scene concept frames

At the current presentation-contract baseline, the following produced kinds do
not have an explicit `SCENES` entry and fall through to `alert.unclassified`.
These frames intentionally describe desired presentation only; they do not
change runtime behavior. An implementation task must add and test the matching
`SCENES` entries separately.

| World-event kind | Concept frame | `sceneTag` direction | Animation tags | Intensity | Manifest path |
| --- | --- | --- | --- | ---: | --- |
| `worker.stale` | **Signal lost at the relay.** A quiet communications interruption: a worker's station keeps its last safe light while a nearby observer checks the relay; avoid crash/fire imagery because staleness is not proof of failure. | `crisis.signal.stale` | `worker.stale`, `signal.check`, `inspect` | 1 | Existing worker vocabulary / observer kit only; no new class required. |
| `pr.closed` | **The review dock powers down.** The completed review vessel leaves the dock and its status lamps settle; convey closure, not rejection or damage. | `activity.review.closed` | `pr.closed`, `dock.departure`, `lights.settle` | 0 | Existing worker vocabulary / dock assets; no new class required. |
| `pr.reviewRequested` | **A fresh inspection is called.** A reviewer receives a calm dispatch and approaches the PR structure with a slate; distinguish a request for review from findings or damage. | `activity.review.requested` | `pr.reviewRequested`, `review.dispatch`, `inspect` | 1 | Existing worker vocabulary / reviewer kit; no new class required. |
| `agent.success` | **A delivery reaches the archive.** A finished contribution is checked in and acknowledged with a brief, local celebration; keep the beat proportional and non-critical. | `recovery.delivery.accepted` | `agent.success`, `delivery`, `celebrate` | 1 | Existing worker vocabulary / contributor kit; no new class required. |
| `agent.info` | **A status beacon updates.** A non-urgent informational pulse appears at the relevant station; nearby activity continues without alarm or forced camera attention. | `activity.status.updated` | `agent.info`, `status.beacon`, `inspect` | 0 | Existing worker vocabulary / station assets; no new class required. |

These are concept directions, not claims that the proposed scene tags or
animation tags are already accepted by runtime assets. The implementation must
check available animation tags and scope against `world-events.ts`,
`world-presentation.ts`, and the asset manifest.

## Source of truth and work routing

- Event production and scope: [`../world-events.ts`](../world-events.ts).
- Scene mapping and presentation fields: [`../world-presentation.ts`](../world-presentation.ts).
- Worker vocabulary, kits, and leader constraints: [`../manifest.ts`](../manifest.ts)
  and `apps/desktop/public/lunar-city/` manifests.
- New work: create or use a tracked Kanban task; there is no implicit
  “next-unworld” queue.
