# Lunar City — full handoff: current state to the video-reference endpoint

Full-journey roadmap from where the implementation stands today to a state
that satisfies the user's video reference. Written 2026-09-01, after two
follow-up passes (lighting rig, hero-building walls) had already landed.
This is the top-level document — it sequences and links the narrower
reports/handoffs that already exist rather than duplicating them.

## Read this first: what "the video reference" actually means

The user supplied an inspiration video (76s, 576×1024 vertical). **It is
not footage of Lunar City, and it does not use Lunar City's approved
palette.** It's a TikTok (`@jarrenrocks`, "A video game for AI agents")
showing a _different_ game: a cool blue/white hex-tile sci-fi city-builder
filmed off a monitor, plus close-ups of a translucent white robot character.

This looked like a potential conflict with `design-qa.md`'s locked-in
warm purple/orange moon-settlement approved art, so I asked the user
directly before writing this roadmap. **Their answer: adopt the video's
underlying concept, not its palette.** Keep the approved moon-settlement
characters/colors exactly as they are. Do not introduce blue/white
sci-fi materials, hex-shaped plots, or grass ground — none of that is
approved art and doing it would contradict `design-qa.md`'s explicit
guardrail against replacing the approved design without the user's
sign-off. If a future session is tempted to chase the video's literal
look, stop and re-read this section first.

### Concept elements the video actually demonstrates (grounded in frames)

- **Per-plot ownership is visually distinct.** Each hex has its own
  colored glow ring (red, purple, blue, pink, green) — a different agent
  or project owns each one, and the color reads at a glance without
  clicking anything.
- **Floating status badges.** A small glowing pin with an icon and a
  text label (e.g. "…ial site-2025") hovers above an active hex,
  independent of whatever inspector/panel UI exists elsewhere.
- **A dedicated hero/focus shot for an individual character.** One clip
  is nothing but a tight shot of a single robot: translucent
  ghostly-white material, strong rim/fresnel glow on the silhouette,
  glowing eyes, a waypoint marker overhead. This is a deliberately
  different visual treatment from the wide overview shots — a "you have
  this character's attention now" state.
- **Continuous camera motion.** The city shots pan/orbit smoothly on
  their own while the narrator talks — nothing about the framing implies
  the viewer is dragging the camera themselves.

### What Lunar City already has that covers part of this

Don't rebuild these — they exist and this session verified it by reading
the source, not by assumption:

- **Per-worker visual identity already exists.** `character-presentation.ts`
  deterministically derives a `signature` (body/head/palette/silhouette
  accessory/emblem) per worker from its canonical identity key
  (`identityAccent`). Every worker already looks visually distinct by
  who/what it represents — this is the worker-level analog of the video's
  colored hex rings.
- **Focus/follow camera already exists.** `world/camera-controller.ts`
  supports `focus`/`follow` intents with per-entity camera anchors
  (`focusAnchors` in `world-scene.ts`), and leader selection already
  opens a dedicated conversation panel (`leader-dialogue.tsx`,
  `leader-runtime.ts`). The mechanism for "give one character your full
  attention" is built; it just doesn't yet get the video's distinct
  visual treatment (see Phase 2 below).
- **Rich per-entity status already exists**, just as a docked 2D panel
  (`entity-inspector.tsx`: task/run/diagnostic/comment/event/log/subagent
  evidence) rather than a floating 3D-anchored badge. Functionally
  equivalent information, different presentation.

### What's genuinely missing

Verified by grep, not assumed — none of these terms occur anywhere in
`src/app/lunar-city` outside test files: `tour`, `cinematic`, `autoOrbit`,
`idleOrbit`, `billboard`, `tooltip`, `hover`, `nameTag`, `displayName`.

1. No idle/cinematic camera motion — the camera only moves on direct user
   input or an explicit `focus`/`follow` dispatch.
2. No distinct visual "selected/focused" material treatment on leaders —
   focusing a leader moves the camera but doesn't change how the model
   itself renders.
3. No floating 3D-anchored status badge/label system — all identity and
   status is either baked into the static scene or shown in the 2D docked
   panel after a click.

Those three gaps are Phases 1–3 below.

## Current implementation inventory (2026-09-01)

- **Geometry:** 7 of 11 specialist buildings have now been handled: Library,
  Research Lab, Depot, Council, Review Office, Garden, and Arts Studio. Triage
  was audited separately and already satisfied the enclosure/readability
  contract. Garden received a lightweight rear pavilion and Arts Studio a
  stepped clerestory; both preserve their open-front identities. The other 3
  (Engineering Workshop, Release Gatehouse, Archive) still need targeted
  enclosure or distinctive-massing review. Full per-building budget table and
  the exact loop to follow:
  **`docs/lunar-city-handoff-2026-09-01.md`** (don't duplicate it here —
  read it before touching `buildings.mjs`).
- **Lighting:** key/rim/fill light rig + glow layer + tuned PBR material
  recipes, tied into the existing `efficient`/`balanced`/`detailed`
  quality-tier degradation ladder. Details: `.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`.
- **Camera:** orbit/pan/tilt/zoom/focus/follow, occlusion handling,
  context recovery, near/mid/far LOD selection tied to camera distance.
  Purely reactive to user/dispatched intents — no autonomous motion.
- **Entities/identity:** canonical connection/profile/session/subagent
  identity encoding (`identity.ts`), deterministic per-worker visual
  signatures (`character-presentation.ts`), bounded live reconciliation
  across connections (`adapters/reconciler.ts`).
- **Interaction:** leader text/voice conversation routed through exact
  session ownership (`leader-sessions.ts`, `leader-runtime.ts`), command
  execution and confirmation (`command-broker.ts`, `command-executors.ts`,
  `command-confirmation.tsx`), Kanban/task adapters.
- **Performance governance:** adaptive quality controller with a bounded
  degradation ladder (`world/quality.ts`), frame scheduler demand-driven
  off active animation groups (`world/scheduler.ts`), perf telemetry
  channel (`perf-runtime.ts`, `perf-runtime-channel.ts`).
- **Verification tooling:** deterministic asset builds + budget/contract
  validation (`scripts/lunar-city/build-models.mjs`,
  `validate-assets.mjs`), a flat-shaded deterministic SVG turntable for
  geometry regression (`render-turntable.mjs`), and a real-WebGL
  screenshot tool for lit visual QA (`render-webgl-preview.mjs`, added
  2026-08-31).
- **Outstanding acceptance gap** (predates this session, recorded in
  `design-qa.md` since the original 3D handoff): no clean packaged
  `electron-builder` build from a current SHA, no GPU-enabled CPU/RSS/FPS
  receipt, no 30-minute stability receipt, no supervised-live receipt.
  This is Phase 5 below and is **the actual finish line** — everything
  else is necessary but not sufficient without it.

## Endpoint definition — what "done" looks like

All of the following true at once, on one clean SHA:

- [ ] All 11 specialist buildings read as enclosed volumes from a 3/4
      overview angle (Phase 0), with the open-front interior view intact
      on every one, and `build-models.test.mjs`'s silhouette-uniqueness
      test green.
- [ ] The overview camera has a slow, continuous idle motion when nothing
      is focused/followed, matching the video's unforced pan (Phase 1).
- [ ] Focusing a leader (via pick, voice, or dispatched `focus` intent)
      visibly changes that leader's presentation, not just the camera
      (Phase 2).
- [ ] (Stretch, only if Phases 0–2 land clean and there's budget left)
      Active work is visible as a floating status indicator near the
      entity doing it, not only in the docked inspector panel (Phase 3).
- [ ] The remaining non-geometry items from the 2026-08-31 report (warm
      window-glow color pending sign-off, roofline variety, leader/
      character scale) are resolved or explicitly deferred with the
      user's sign-off (Phase 4).
- [ ] A clean packaged Electron build, on this SHA, passes GPU-enabled
      CPU/RSS/FPS budget, a 30-minute stability run, and a supervised-live
      session (Phase 5) — this is what turns "looks right in a preview
      screenshot" into an actual shippable receipt.

## Phased roadmap

Do these in order. Each phase links its detailed doc where one already
exists instead of repeating it.

### Phase 0 — Geometry solidification (in progress, 7/11 complete + Triage audited)

Already fully specified: **`docs/lunar-city-handoff-2026-09-01.md`**.
Follow its per-building loop and budget table exactly, starting with
Engineering Workshop after the Triage/Garden/Arts Studio passes, especially the
"check the silhouette-uniqueness test after every building" rule — it's
there because skipping it cost a full extra iteration on the hero-building
pass.

### Phase 1 — Cinematic idle camera

New work, not previously scoped. Goal: when no entity is focused/followed
and the user hasn't touched camera input recently, the overview camera
drifts on its own — slow alpha rotation and/or a gentle radius/beta
breathing motion, not a fast showy orbit. This is what makes the video's
"the camera is just alive" feeling land.

Landing spots:

- `world/camera-controller.ts` — `createCameraController` already owns
  `alpha`/`beta`/`radius` state and reduced-motion handling
  (`setReducedMotion`). An idle-drift mode is a natural extension of the
  same controller, not a new system.
- Must respect `reducedMotion` (already wired end-to-end — see
  `setReducedMotion` calls in `world-scene.ts`) and the quality tier's
  `animationDistance`/frame-budget governor (`world/quality.ts`,
  `world/scheduler.ts`) — idle drift should stop demanding frames the
  same way finite leader animations already do, not add a permanent
  per-frame cost regardless of tier.
- Must yield immediately to any real user input or a dispatched
  `focus`/`follow` intent — check `bindCameraInput` in
  `camera-controller.ts` for the existing input-priority pattern.
- Add coverage in `world/camera-controller.test.ts` and
  `world/create-world.test.ts` (there's precedent for both unit-level and
  full-world-fake test coverage of camera behavior in those two files —
  follow whichever one already tests the closest existing behavior, e.g.
  `setReducedMotion`).

### Phase 2 — Focus/hero-shot visual treatment

New work. Goal: the moment a leader is focused (picked, voice-selected,
or `dispatchCamera({ kind: 'focus', ... })`), that leader's presentation
changes, not just the camera framing — closer to the video's translucent
rim-glow hero shot for its focused character, adapted to the approved
warm palette (do not copy the video's blue/white/ghost material; take the
_technique_ — stronger rim light and glow on the selected entity only —
and apply it to the existing leader materials).

Landing spots:

- `world-scene.ts` already tracks `desiredLeaderStates`/
  `activeLeaderAnimations` per leader and the `rimLight`/`glowLayer` added
  in the 2026-08-31 pass. The cheapest version of this: when a leader
  becomes the focused entity, bump that leader's material emissive/rim
  contribution (or add a thin emissive outline mesh, reusing an existing
  palette color) rather than changing scene-wide lighting.
- Must not affect any other leader's presentation, and must clear cleanly
  when focus moves away or is cleared — check `getCameraState()`'s
  `focusedEntityKey` as the source of truth rather than adding new state.
- This is a materials/rendering change, not a geometry change, so it
  doesn't touch `build-models.mjs`'s asset pipeline or the
  silhouette-uniqueness test at all.

### Phase 3 — Floating status badges (stretch goal — validate cost before committing)

Optional. The video's floating pin+label over an active hex is a nice
detail but is the highest-risk item here: it's a new per-frame
screen-space projection system (3D world position → 2D DOM overlay,
recomputed every frame the camera moves), and this app's whole
architecture is built around a strict low-power frame budget
(`world/quality.ts`, `world/scheduler.ts`, the demand-driven scheduler in
`Phase 6` history). Do not build this without first measuring its actual
per-frame cost against the existing budget in the `efficient` tier — if it
can't stay cheap, it doesn't belong here, and `entity-inspector.tsx`'s
existing docked-panel approach already covers the same information
without this cost.

If pursued: reuse `scene.pick`/camera-anchor math already used for focus
framing (`leaderCameraAnchor`, `focusAnchors` in `world-scene.ts`) to
project a world position to screen space, and gate visibility by the same
`decorations` quality flag the glow layer now uses, so it degrades with
everything else in the efficient tier's ladder.

### Phase 4 — Remaining fidelity items from the 2026-08-31 report

Carried over, not re-specified here — see
`.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`'s
follow-up plan:

- Warm window-glow palette color — **needs the user's explicit sign-off**
  before adding a 9th `APPROVED_PALETTE` entry; this is a design decision,
  not a lighting tune.
- Roofline variety across buildings once Phase 0 is further along.
- Leader/character scale — check against camera-focus framing in
  `manifest.ts` before touching mesh scale directly.

### Phase 5 — Packaging/acceptance (the actual finish line)

This has been open since the original 3D handoff
(`docs/lunar-city-design-handoff.md`'s very first addendum) and nothing
in Phases 0–4 satisfies it on its own:

- A clean `electron-builder` package built from the final SHA (not a dev
  build — every receipt so far, including this session's, has been
  build-only or dev-Electron evidence).
- A GPU-enabled CPU/RSS/FPS allocation receipt against the `efficient`
  tier's budget.
- A 30-minute stability run.
- A supervised-live session against a real Hermes gateway (the "exact
  packaged 25/100/250 scenarios" noted as blocked in the 2026-08-31
  addendum, pending an owned authenticated three-gateway lifecycle).

None of this was attempted in this session — Electron's binary isn't
provisioned in this sandbox (same gap the draft PR's own description
calls out). It needs an environment where `electron-builder` and a real
Electron runtime are available.

## Sequencing guidance

Phase 0 and Phase 1/2 are independent (geometry vs. camera/materials) and
can be worked in either order or interleaved. Phase 3 should not start
before 0–2 are stable, given its cost risk. Phase 4's palette question
should be raised with the user early since it's a standing blocker
regardless of when the geometry work reaches it. Phase 5 can only happen
in an environment with Electron packaging available — flag that
constraint immediately in any session attempting it rather than
discovering it partway through.

## Environment / verification

Same as `docs/lunar-city-handoff-2026-09-01.md`'s environment section —
not repeated here.

Frames from the video are described above but were deliberately **not**
committed into this repository — it's someone else's copyrighted TikTok
content, not project-owned reference art like
`moon-settlement-approved.jpg`. If a future session needs to re-examine
the source video directly, it will need the original upload again; the
concrete observations above (per-plot color rings, floating status pin,
the hero-shot character close-up, continuous camera motion) are what this
roadmap's Phases 1–3 are grounded in, and should be sufficient without
re-watching it.
