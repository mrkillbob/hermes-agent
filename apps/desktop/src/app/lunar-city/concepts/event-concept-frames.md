# Lunar City event concept frames

These are art-direction studies, not implemented scenes. At [Hermes commit `5c0a008a6e5b7adcfdaa49148852b797dc8ca7d2`](https://github.com/mrkillbob/hermes-agent/tree/5c0a008a6e5b7adcfdaa49148852b797dc8ca7d2), the four emitted event kinds and the `worker.stale` condition below have no dedicated `SCENES` entry in [world-presentation.ts](../world-presentation.ts). The scene converts the condition to an event-shaped projection through `conditionEvent` in [world-scene.tsx](../world-scene.tsx). Each then uses the existing generic `alert.unclassified` `sceneTag`, display-only `alert` animation tag, and cosmetic intensity **1**. Scene and NPC animation tags describe presentation; playback uses the separate state-to-clip contract in [world-animation.ts](../world-animation.ts).

| Input or emitted event | Concept frame using the existing kit | Exploratory art direction |
| --- | --- | --- |
| `worker.stale` (condition) | A `worker-operations` silhouette by the `engineering-workshop` | A dim signal pauses above the worksite; amber light and a quiet inspection communicate a missing heartbeat without depicting a crash. |
| `pr.closed` | A `worker-review` silhouette at the `review-office` | A review panel folds closed and the worker returns along a softly lit path; no merge celebration is implied. |
| Review requested (`pr.reviewRequested` in the brief; emitted kind `pr.review_requested`) | A `worker-review` silhouette approaches the `review-office` | A violet inspection marker illuminates an unopened review panel. The snake-case emitted kind is the identifier to use in implementation work. |
| `agent.success` | An existing worker near the `council-hall` | A short, local shimmer acknowledges success without implying a citywide release or merge. |
| `agent.info` | An existing worker beside a public notice area | A low-intensity cyan marker presents information without suggesting failure or action approval. |

The concrete `worker-operations`, `worker-review`, `engineering-workshop`, `review-office`, and `council-hall` IDs are listed by `LUNAR_CITY_ASSET_MANIFEST` in [world-assets.ts](../world-assets.ts). `worker` is an existing actor kind; choosing one for a frame does not imply a particular live participant is present. Existing clip declarations include `idle`, `walk`, `inspect`, `wait`, `talk` and `celebrate`, but display tags alone are not clip bindings. No new scene tag, runtime mapping, animation, actor or asset is added by these frames.

For future asset work, extend the source kit and `LUNAR_CITY_ASSET_MANIFEST` in [world-assets.ts](../world-assets.ts), including any real object bindings, LOD/texture declarations and validation needed for the asset. The manifest names `scripts/generate_lunar_city_baseline.py` as its Blender validation script. Any new event presentation mapping belongs to a separately reviewed runtime change; it is outside this docs-only brief. The [Glimmer Quarter](./2026-09-28-glimmer-quarter-shadow-weaver.md) and [Obsidian Tide](./2026-10-15-obsidian-tide-cartographer.md) named NPC concepts remain parked until an owner supplies a supported asset and kit.

Future concept work should carry the actual owning tracked-task reference when that task is assigned.
