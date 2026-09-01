# Lunar City — full handoff: current state to the video-reference endpoint

## Verification addendum (2026-09-01, second pass)

A large amount of work landed between the first and second passes of this
roadmap — 28 commits, all on the draft PR branch directly: the remaining
9 buildings' Phase 0 work (Depot/Council/Review Office fully solidified;
Triage audited and found already sufficient; Garden/Arts Studio given a
rear pavilion/clerestory instead of a wall; Engineering
Workshop/Release Gatehouse/Archive judged sufficient on their existing
shell), Phase 1 (idle camera drift), Phase 2 (focused-leader presentation),
Phase 4 resolution, and a substantial, disciplined body of **diagnostic-only**
tooling/research into permissively licensed (CC0) asset kits — see
`docs/lunar-city-open-asset-catalog.md` and
`docs/lunar-city-strategy-game-diagnostic.md`.

This pass independently re-verified the claims rather than trusting them:

- Reran `build-models.mjs` → `validate-assets.mjs` → the full
  `node --test` asset-contract suite (41/41, including the
  silhouette-uniqueness test) → `npx vitest run --project ui src/app/lunar-city`
  (542/542) → `npm run typecheck`. All green, matching what the commits
  claimed.
- **Independently visually inspected** the three buildings claimed to
  already be sufficient without a wall pass (Engineering Workshop, Release
  Gatehouse, Archive) via `render-webgl-preview.mjs`'s single-model mode,
  from both the open front and the back — confirmed solid enclosed volumes
  with intact open-front interiors, not just a claim in a commit message.
- **Read the actual diff** for Phase 1 and Phase 2 rather than trusting
  their commit messages: idle drift correctly gates on `reducedMotion`,
  `focusedEntityKey`, and the `efficient` quality tier (disabled there
  entirely — `setIdleEnabled(settings.tier !== 'efficient')`), resets on
  any real input, and feeds the frame scheduler's demand check so it
  doesn't force continuous rendering when inactive. Phase 2 shipped as a
  cheaper implementation than this roadmap originally suggested — a
  1.14× scale bump on the focused leader (reset to 1× on every other
  leader) rather than a rim/emissive material change — which is a
  legitimate, lower-cost way to satisfy "focus changes presentation, not
  just camera framing." **Optional future enhancement, not a gap:**
  combine the scale bump with the rim-light/glow emphasis this roadmap
  originally described, for a stronger hero-shot read closer to the
  video's translucent-glow treatment.
- Verified the CC0 open-asset research stayed diagnostic-only, as its own
  docs claim: `git show --stat` on every commit that touched Kenney/
  Quaternius/Poly Haven staging shows only scripts
  (`curate-open-asset-pack.mjs`, `blender_stage.py`,
  `import-open-asset-pack.mjs`) and docs changed — zero binary asset files
  entered `apps/desktop/public/lunar-city/`.
- Found one **pre-existing, unrelated** test failure while checking the
  Phase 5 perf harness: `node --test scripts/perf/lunar-city-runner.test.mjs`
  fails `rejects non-Hermes macOS bundles, arbitrary executables, and
nonexecutable files` (missing expected exception). That file was last
  touched by commits from before this PR existed (`f07bf0c57` and
  earlier) — none of the 28 Lunar City commits touched it. Not this
  roadmap's bug to fix, but worth knowing about before assuming the perf
  harness is fully green.

**Net effect: Phases 0, 1, 2, and 4 are genuinely done**, independently
confirmed, not just self-reported. Phase 3 remains an unstarted, explicitly
optional stretch goal. Phase 5 is the only phase with real remaining work,
and it's detailed precisely below — the perf/packaging harness already
exists in this repo, it's just never been run against a real packaged
build because that needs an environment this sandbox doesn't have.

## Full-journey roadmap

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

- **Geometry:** all 11 specialist buildings satisfy the generated enclosure,
  open-front, budget, and unique-silhouette contracts. Garden received a
  lightweight rear pavilion and Arts Studio a stepped clerestory; both preserve
  their open-front identities. Engineering Workshop, Release Gatehouse, and
  Archive retain the shared secondary-district shell plus role-specific
  massing; further wall changes require a concrete visual regression. Full
  per-building budget table and the exact loop to follow:
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

- [x] All 11 specialist buildings read as enclosed volumes from a 3/4
      overview angle (Phase 0), with the open-front interior view intact
      on every one, and `build-models.test.mjs`'s silhouette-uniqueness
      test green.
- [x] The overview camera has a slow, continuous idle motion when nothing
      is focused/followed, matching the video's unforced pan (Phase 1).
- [x] Focusing a leader (via pick, voice, or dispatched `focus` intent)
      visibly changes that leader's presentation, not just the camera
      (Phase 2).
- [ ] (Stretch, only if Phases 0–2 land clean and there's budget left)
      Active work is visible as a floating status indicator near the
      entity doing it, not only in the docked inspector panel (Phase 3).
- [x] The remaining non-geometry items from the 2026-08-31 report (warm
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

### Phase 0 — Geometry solidification (complete; all 11 contract checks green)

Already fully specified: **`docs/lunar-city-handoff-2026-09-01.md`**.
The per-building loop and budget table are complete. Any future geometry change
must still run the
"check the silhouette-uniqueness test after every building" rule — it's
there because skipping it cost a full extra iteration on the hero-building
pass.

### Phase 1 — Cinematic idle camera (complete)

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

### Phase 2 — Focus/hero-shot visual treatment (complete, simpler than originally scoped — see verification addendum)

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

### Phase 4 — Remaining fidelity items from the 2026-08-31 report (complete)

Carried over, not re-specified here — see
`.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`'s
follow-up plan:

- Warm window-glow palette color — **explicitly deferred** to preserve the
  approved eight-color palette; adding a 9th `APPROVED_PALETTE` entry remains
  a separate design decision.
- Roofline variety — addressed by the Library roof cap and Arts Studio
  stepped clerestory; the uniqueness contract remains green.
- Leader/character scale — addressed with the focused-leader presentation
  treatment and existing camera-anchor framing; no global mesh rescale was
  introduced.

### Phase 5 — Packaging/acceptance (the actual finish line)

This has been open since the original 3D handoff
(`docs/lunar-city-design-handoff.md`'s very first addendum) and nothing
in Phases 0–4 satisfies it on its own. **The tooling for this already
exists in the repo** (`scripts/perf/README.md`'s "Lunar City packaged
acceptance" section) — it has just never been run against a real
packaged build, because that needs a real GPU and Electron packaging,
neither of which this sandbox has. This is a from-scratch environment
requirement for whoever picks this up, not a coding task.

**Step 1 — build a real package** (not a dev build; every receipt so far,
including both prior passes' evidence, has been build-only or
dev-Electron evidence, which the acceptance tooling explicitly refuses to
accept):

```bash
cd apps/desktop
npm run dist:linux   # or dist:mac / dist:mac:dmg / dist:win, matching the target platform
```

**Step 2 — run the acceptance capture** against that package, from the
exact git SHA it was built from:

```bash
npm run perf:lunar-city:accept -- \
  --binary /absolute/path/to/the/built/binary \
  --sha <the exact git SHA the package was built from> \
  --output /absolute/path/lunar-city-receipts \
  --scenario visible-idle          # then repeat with --scenario 30-minute-stability, --scenario balanced-overview
```

This is fail-closed by design: it rejects a dev build, a build whose
embedded stamp doesn't match `--sha` exactly, or a skipped/mocked
Playwright run standing in for real evidence. It captures GPU adapter/
state, CPU/RSS, FPS, Electron/Chromium versions, window bounds, and
display scale directly from the launched package and host — not from
operator-supplied metadata (an optional `--metadata` JSON is
supplemental annotation only, and cannot itself satisfy the gate).
`visible-idle` always measures ≥60s; `30-minute-stability` always
measures ≥1,800,000ms — these are real wall-clock runs, not something a
unit test's injected clock can substitute for.

**Known blocker that Electron access alone does not fix:** the exact
25-active / 100-active / 250-LOD-population and 30-minute-stability
_population_ scenarios additionally require three fixture gateways
"started and owned by the same run" emitting authenticated observed
`subagent.start` evidence against the canonical
`lunar-city-population-v3` fixture. Per `scripts/perf/README.md`: _"The
repository currently has no supported lifecycle that both owns all three
fixture gateways and emits authenticated observed `subagent.start`
evidence. Therefore exact-population package acceptance remains
deliberately blocked."_ That's separate infrastructure work (a real
gateway-preseed API), not something Phase 5 can complete just by having
a GPU and Electron available. The non-population scenarios
(`visible-idle`, `30-minute-stability`, `balanced-overview`) are not
blocked by this and can be captured as soon as a real package exists.

**Before trusting a green run:** `node --test scripts/perf/lunar-city.test.mjs
scripts/perf/lunar-city-runner.test.mjs` currently has one pre-existing
failure (`rejects non-Hermes macOS bundles...` in
`lunar-city-runner.test.mjs`) unrelated to any Lunar City visual work —
see the verification addendum above. Don't let it block Phase 5 progress,
but don't assume it's caused by anything in this roadmap either.

## Sequencing guidance

**Status as of 2026-09-01 (second pass): Phases 0, 1, 2, and 4 are done and
independently verified.** Only two phases have real remaining work:

- **Phase 3** (floating status badges) is still unstarted and still
  optional/stretch. If picked up, measure its per-frame cost in the
  `efficient` tier before writing the feature, not after — the existing
  docked inspector panel already covers the same information without the
  cost, so this only earns its place if it's cheap.
- **Phase 5** (packaging/acceptance) is the only phase blocking "done" and
  needs an environment with a real GPU and Electron packaging — this
  sandbox has neither. Follow the precise steps above; the tooling already
  exists and doesn't need to be built, only run.

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
