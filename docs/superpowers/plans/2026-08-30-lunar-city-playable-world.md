# Lunar City 3D Playable Hermes World Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build the approved Lunar City as a low-overhead, rotatable 3D Babylon.js city populated by real Hermes profiles, sessions, subagents, and Kanban work, with worker follow, persistent leader text/voice conversations, and identity-safe controls.

**Architecture:** Babylon.js owns only the lazy 3D engine, scene graph, angled camera, navigation presentation, animation, picking, occlusion, instancing, and level of detail. Focused TypeScript adapters publish immutable presentation-safe snapshots from existing desktop stores, gateway RPC, and the optional Kanban plugin; React and nanostores retain conversations, accessibility, confirmations, and every mutation.

**Tech Stack:** React 19, TypeScript 6, nanostores, Babylon.js 9.23.0, Recast Detour 1.6.4, glTF/GLB, Electron 41, Vitest, Testing Library, Playwright, existing Hermes JSON-RPC/REST/plugin boundaries.

**Spec:** docs/superpowers/specs/2026-08-30-lunar-city-playable-world-design.md

## Global Constraints

- The approved source is apps/desktop/public/lunar-city/moon-settlement-approved.jpg, SHA-256 248e8d40946b08b9f74f4b2ddd0ba17e4f17fd054260189972164c5d6ca70590, 1280x910.
- The approved image is an immutable modeling reference and must never render as a runtime background, billboard, floor, wall, or character plane.
- Preserve the approved composition, palette relationships, buildings, leaders, robot-child design, recognizable silhouettes, and warmth; generic substitute art is forbidden.
- Runtime assets are actual low-poly 3D GLB models with explicit source provenance, budgets, LODs, pivots, collision volumes, camera anchors, navigation areas, and animation clips.
- Keep Babylon.js out of the initial desktop bundle through selective dynamic imports and dispose the engine, scene, models, textures, listeners, and GPU resources when Lunar City unmounts.
- Babylon.js consumes immutable snapshots and emits typed intents; it never calls Hermes or mutates authoritative state.
- Identity keys include connection and profile plus every available session, subagent, board, task, run, worker, and repository identifier; display names are never mutation keys.
- Unknown, stale, disconnected, or partially observed state must never become working or implied progress.
- Safe actions may execute directly; interrupt, terminate, retry, reclaim, reassign, dispatch, and task-state changes require exact-identity confirmation and authoritative readback.
- Interactive rendering is capped at 30 FPS, ambient rendering at 15 FPS, and hidden, minimized, or unmounted rendering at zero frames.
- Balanced overview is limited to 180 draw calls and 1.5 million visible triangles; Balanced worker focus is limited to 220 draw calls and 2 million visible triangles; Lunar City GPU memory is limited to 256 MiB.
- Do not add continuous physics, bloom, screen-space reflections, volumetrics, expensive blur, unbounded particles, or decorative post-processing.
- Mock, asset, visual, accessibility, performance, packaged, and supervised live-Hermes evidence remain separate.

---

## File Map

### Approved baseline and 3D assets

- apps/desktop/public/lunar-city/moon-settlement-approved.jpg: immutable art bible and regression reference.
- apps/desktop/public/lunar-city/v2/source-reference.v2.json: digest, palette, landmarks, silhouettes, and default-camera composition.
- apps/desktop/public/lunar-city/v2/world-manifest.v2.json: models, transforms, budgets, navigation, occlusion groups, destinations, and camera landmarks.
- apps/desktop/public/lunar-city/v2/models/*.glb: real low-poly terrain, buildings, props, leaders, workers, and vehicles.
- apps/desktop/public/lunar-city/v2/textures/*: compact approved-palette textures and atlases.
- apps/desktop/scripts/lunar-city/build-models.mjs: deterministic 3D asset builder.
- apps/desktop/scripts/lunar-city/modeling/*.mjs: focused terrain, building, character, prop, palette, and export units.
- apps/desktop/scripts/lunar-city/validate-assets.mjs: deterministic manifest and GLB budget validator.

### Focused runtime units

- apps/desktop/src/app/lunar-city/model.ts: canonical identity, snapshot, manifest, state, quality, and intent interfaces.
- apps/desktop/src/app/lunar-city/identity.ts: collision-safe key constructors.
- apps/desktop/src/app/lunar-city/manifest.ts: manifest loader and runtime validation.
- apps/desktop/src/app/lunar-city/state-map.ts: authoritative state to destination and animation mapping.
- apps/desktop/src/app/lunar-city/store.ts: immutable snapshot, selection, camera, and quality nanostores.
- apps/desktop/src/app/lunar-city/adapters/fleet.ts: profile and connection roster normalization.
- apps/desktop/src/app/lunar-city/adapters/sessions.ts: session and subagent normalization.
- apps/desktop/src/app/lunar-city/adapters/kanban.ts: optional Kanban REST and socket normalization.
- apps/desktop/src/app/lunar-city/adapters/reconciler.ts: ordered deltas, freshness, and bounded rereads.
- apps/desktop/src/app/lunar-city/world/create-world.ts: selective Babylon.js construction and teardown.
- apps/desktop/src/app/lunar-city/world/world-scene.ts: static world and entity reconciliation.
- apps/desktop/src/app/lunar-city/world/camera-controller.ts: angled overview, orbit, pan, zoom, focus, follow, and Return to City.
- apps/desktop/src/app/lunar-city/world/occlusion.ts: roof and foreground-wall fading.
- apps/desktop/src/app/lunar-city/world/navigation.ts: Recast navigation query boundary and deterministic movement.
- apps/desktop/src/app/lunar-city/world/entities.ts: instanced and animated entity reconciliation.
- apps/desktop/src/app/lunar-city/world/scheduler.ts: 30/15/0 FPS policy and visibility handling.
- apps/desktop/src/app/lunar-city/world/quality.ts: Efficient, Balanced, and Detailed tier selection.
- apps/desktop/src/app/lunar-city/components/camera-controls.tsx: accessible camera controls and Return to City.
- apps/desktop/src/app/lunar-city/components/leader-dialogue.tsx: persistent text and voice conversation.
- apps/desktop/src/app/lunar-city/components/entity-inspector.tsx: identity, evidence, and safe actions.
- apps/desktop/src/app/lunar-city/components/command-confirmation.tsx: disruptive-action confirmation.
- apps/desktop/src/app/lunar-city/leader-sessions.ts: durable connection/profile to session mapping.
- apps/desktop/src/app/lunar-city/command-broker.ts: routed commands and receipt verification.
- apps/desktop/src/app/lunar-city/index.tsx: thin composition root.

---

### Task 1: Reconcile the approved Lunar City baseline

**Files:**
- Restore: apps/desktop/src/app/lunar-city/*
- Restore: apps/desktop/public/lunar-city/*
- Restore: apps/desktop/e2e/lunar-city.spec.ts
- Modify through replay: apps/desktop/src/app/starmap/index.tsx
- Preserve: docs/lunar-city-design-handoff.md, design-qa.md

**Interfaces:**
- Consumes: the current exact HEAD containing this plan and the approved source chain ending at 55ffea25ba.
- Produces: the approved fixture visualizer on the current desktop shell, with its source artwork and provenance intact before 3D replacement begins.

- [ ] **Step 1: Create an isolated implementation worktree and record state**

Run the superpowers:using-git-worktrees skill from the exact commit containing this plan, then run:

    git status --short --branch
    git rev-parse HEAD
    git merge-base 55ffea25bab4779cd3998744cae9975a054d2ec1 HEAD

Expected: a clean isolated worktree; the merge base is a35100ac50b7f0097dc26947e07b055c2137f5fb.

- [ ] **Step 2: Replay the complete Lunar City source history in order**

    git cherry-pick fd01048446 9ba59f07de 6cc1cd4276 cb77643208 d2f915c76c 651ba8bba8 8dcdaae576 aee1ad9ca8 55ffea25ba

If Starmap conflicts, preserve the current routing and overlay ownership while applying only the Lunar City import, render branch, callback, public assets, tests, handoff, and QA files. Do not accept unrelated changes to sessions, gateways, overlays, or desktop boot behavior.

- [ ] **Step 3: Verify the reconciled baseline**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/index.test.tsx
    npm run typecheck --workspace apps/desktop
    git diff --check

Expected: the restored Lunar City fixture tests, TypeScript, and whitespace checks pass.

- [ ] **Step 4: Record reconciliation and commit only conflict resolution**

Append the implementation worktree starting HEAD, replayed commits, resulting HEAD, and conflict decisions to docs/lunar-city-design-handoff.md. If replay required manual conflict edits:

    git add docs/lunar-city-design-handoff.md apps/desktop/src/app/starmap/index.tsx
    git commit -m "chore(desktop): reconcile approved Lunar City baseline"

### Task 2: Define and validate the 3D asset contract

**Files:**
- Modify: apps/desktop/package.json, package-lock.json
- Create: apps/desktop/public/lunar-city/v2/source-reference.v2.json
- Create: apps/desktop/public/lunar-city/v2/world-manifest.v2.json
- Create: apps/desktop/scripts/lunar-city/validate-assets.mjs
- Create: apps/desktop/scripts/lunar-city/validate-assets.test.mjs

**Interfaces:**
- Consumes: the approved source digest and the @gltf-transform/core 4.4.2 NodeIO API.
- Produces: validateAssetPack(root, io): Promise<ValidationResult>, SourceReferenceV2, and WorldManifestV2.

- [ ] **Step 1: Add the CLI-only GLB inspection dependency**

    npm install --save-dev @gltf-transform/core@4.4.2 @gltf-transform/extensions@4.4.2 --workspace apps/desktop

- [ ] **Step 2: Write the failing contract tests**

Create validate-assets.test.mjs with:

    test('rejects the approved JPG as runtime geometry or texture', async () => {
      const result = await validateAssetPack(fixture({
        models: [{ id: 'world', uri: '../moon-settlement-approved.jpg' }]
      }), fakeIo)
      assert.equal(result.ok, false)
      assert.match(result.errors.join('\n'), /approved source cannot be a runtime asset/)
    })

    test('rejects a model above its declared triangle budget', async () => {
      const result = await validateAssetPack(fixture({
        models: [{ id: 'worker', uri: 'models/worker.glb', maxTriangles: 1200 }]
      }), fakeIo.withTriangles('models/worker.glb', 1201))
      assert.match(result.errors.join('\n'), /worker exceeds 1200 triangles/)
    })

- [ ] **Step 3: Run the tests and verify failure**

    node --test apps/desktop/scripts/lunar-city/validate-assets.test.mjs

Expected: FAIL because validateAssetPack is not exported.

- [ ] **Step 4: Implement the validator and schemas**

Use this public result shape:

    export function validationResult(errors) {
      return { ok: errors.length === 0, errors: Object.freeze(errors) }
    }

    export async function validateAssetPack(root, io) {
      const errors = []
      if (root.version !== 2) errors.push('version must equal 2')
      if (root.source.sha256 !== APPROVED_SHA) errors.push('approved source digest mismatch')
      for (const model of root.models ?? []) {
        if (/moon-settlement-approved\.jpg$/i.test(model.uri)) {
          errors.push('approved source cannot be a runtime asset')
          continue
        }
        const stats = await inspectGlb(io, model.uri)
        if (stats.triangles > model.maxTriangles) {
          errors.push(model.id + ' exceeds ' + model.maxTriangles + ' triangles')
        }
        requireNamedNodes(model, stats, errors)
        requireClips(model, stats, errors)
        requireLods(model, stats, errors)
      }
      validateCameraLandmarks(root.camera, errors)
      validateNavigation(root.navigation, errors)
      return validationResult(errors)
    }

The source reference records the exact digest, dimensions, approved palette, district landmark points, recognizable silhouettes, material guide, and approved overview framing. The manifest defines models, materials, LODs, transforms, pivots, bounds, camera anchors, occlusion groups, navigation mesh URI and links, destinations, project slots, and quality budgets.

- [ ] **Step 5: Run the tests and commit**

    node --test apps/desktop/scripts/lunar-city/validate-assets.test.mjs
    git add apps/desktop/package.json package-lock.json apps/desktop/public/lunar-city/v2 apps/desktop/scripts/lunar-city
    git commit -m "feat(desktop): define Lunar City 3D asset contract"

Expected: validator tests pass and no runtime URI points to the approved JPG.

### Task 3: Build the approved low-poly 3D asset pack

**Files:**
- Modify: apps/desktop/package.json, package-lock.json
- Create: apps/desktop/scripts/lunar-city/build-models.mjs
- Create: apps/desktop/scripts/lunar-city/build-models.test.mjs
- Create: apps/desktop/scripts/lunar-city/modeling/palette.mjs
- Create: apps/desktop/scripts/lunar-city/modeling/terrain.mjs
- Create: apps/desktop/scripts/lunar-city/modeling/buildings.mjs
- Create: apps/desktop/scripts/lunar-city/modeling/characters.mjs
- Create: apps/desktop/scripts/lunar-city/modeling/props.mjs
- Create: apps/desktop/scripts/lunar-city/modeling/export.mjs
- Create: apps/desktop/public/lunar-city/v2/models/*.glb
- Create: apps/desktop/public/lunar-city/v2/textures/*
- Modify: apps/desktop/public/lunar-city/v2/world-manifest.v2.json

**Interfaces:**
- Consumes: SourceReferenceV2 plus Babylon.js NullEngine, MeshBuilder, Skeleton, AnimationGroup, and GLTF2Export.
- Produces: buildAssetPack(outputRoot): Promise<BuildReceipt>, deterministic GLB files, texture files, and updated model statistics.

- [ ] **Step 1: Add deterministic Babylon.js export dependencies**

    npm install --save-dev @babylonjs/core@9.23.0 @babylonjs/serializers@9.23.0 --workspace apps/desktop

- [ ] **Step 2: Write the failing build receipt test**

    test('builds every approved landmark and character family', async () => {
      const receipt = await buildAssetPack(tempRoot)
      assert.deepEqual(receipt.missing, [])
      assert.deepEqual(receipt.models.sort(), [
        'bus', 'council', 'depot', 'garden', 'leaders', 'library',
        'research-lab', 'review-office', 'terrain', 'triage', 'workers'
      ])
    })

    test('is byte-stable for identical source input', async () => {
      const first = await buildAssetPack(tempRootA)
      const second = await buildAssetPack(tempRootB)
      assert.deepEqual(first.sha256ByModel, second.sha256ByModel)
    })

- [ ] **Step 3: Run the build tests and verify failure**

    node --test apps/desktop/scripts/lunar-city/build-models.test.mjs

Expected: FAIL because buildAssetPack does not exist.

- [ ] **Step 4: Implement focused model builders**

Each module receives a Babylon.js Scene on a NullEngine and returns named TransformNode roots. Use only approved palette materials from palette.mjs. Use shared primitive and profile helpers instead of duplicating mesh construction.

    export function buildWorker(scene, variant) {
      const root = new TransformNode('worker:' + variant.id, scene)
      const body = MeshBuilder.CreateCapsule('body', { height: 0.7, radius: 0.22 }, scene)
      body.material = variant.bodyMaterial
      body.parent = root
      attachRobotChildHead(scene, root, variant)
      attachRobotChildLimbs(scene, root, variant)
      attachRoleAccessory(scene, root, variant)
      return rigWorker(root, WORKER_CLIPS)
    }

Create genuine 3D terrain, cliffs, walkways, Library, Research Lab, Resource Depot, Bus Stop, Review Office, Triage, Break Garden, Council, bus, telescope, portal, consoles, signs, plants, workbenches, distinct approved leader models, and a modular robot-child worker rig. Do not use billboard materials or source-image fragments.

Encode the approved palette and most light response in base color, emissive channels, and vertex color. Keep material count within each manifest budget and share materials across repeated geometry. Export an uncompressed GLB first; retain mesh compression only when the resulting file is smaller, Babylon.js loads it from the packaged app without an external decoder fetch, and turntable regression remains green.

- [ ] **Step 5: Create all required animation clips**

Workers require idle, walk, talk, listen, work, carry, handoff, queue, blocked, failed, review, triage, heartbeat, rest, and done. Leaders require idle, listen, talk, think, acknowledge, and unavailable. Doors, bus, telescope, review portal, workbenches, triage station, garden activity, and room lights receive named clips or declared state channels.

- [ ] **Step 6: Export, validate, visually inspect, and commit**

    node apps/desktop/scripts/lunar-city/build-models.mjs
    node --test apps/desktop/scripts/lunar-city/build-models.test.mjs apps/desktop/scripts/lunar-city/validate-assets.test.mjs
    node apps/desktop/scripts/lunar-city/validate-assets.mjs apps/desktop/public/lunar-city/v2/world-manifest.v2.json
    git add apps/desktop/package.json package-lock.json apps/desktop/scripts/lunar-city apps/desktop/public/lunar-city/v2
    git commit -m "feat(desktop): build approved Lunar City 3D assets"

Expected: every GLB validates, the two deterministic builds match, and turntable review confirms actual depth, recognizable approved silhouettes, no stagnant duplicates, and no generic replacement art.

### Task 4: Mount Babylon.js lazily and reconstruct the static 3D city

**Files:**
- Modify: apps/desktop/package.json, package-lock.json
- Create: apps/desktop/src/app/lunar-city/model.ts
- Create: apps/desktop/src/app/lunar-city/manifest.ts
- Create: apps/desktop/src/app/lunar-city/manifest.test.ts
- Create: apps/desktop/src/app/lunar-city/world/create-world.ts
- Create: apps/desktop/src/app/lunar-city/world/world-scene.ts
- Create: apps/desktop/src/app/lunar-city/world/create-world.test.ts
- Modify: apps/desktop/src/app/lunar-city/index.tsx

**Interfaces:**
- Consumes: WorldManifestV2 and v2 GLB URLs.
- Produces: loadWorldManifest(url): Promise<WorldManifestV2> and createLunarCityWorld(canvas, manifest, emit, modules?): Promise<LunarCityWorldHandle>.

- [ ] **Step 1: Add pinned runtime dependencies**

    npm install @babylonjs/core@9.23.0 @babylonjs/loaders@9.23.0 recast-detour@1.6.4 --workspace apps/desktop

- [ ] **Step 2: Write failing manifest and lifecycle tests**

    it('rejects the approved JPG in every runtime URI field', () => {
      expect(() => parseWorldManifest(runtimeJpgFixture)).toThrow(
        /approved source cannot be a runtime asset/
      )
    })

    it('disposes the engine and all listeners on destroy', async () => {
      const handle = await createLunarCityWorld(canvas, manifest, emit, fakeModules)
      handle.destroy()
      expect(fakeEngine.dispose).toHaveBeenCalledOnce()
      expect(listenerCount()).toBe(0)
    })

- [ ] **Step 3: Run the tests and verify failure**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/manifest.test.ts src/app/lunar-city/world/create-world.test.ts

Expected: FAIL because the manifest parser and world factory do not exist.

- [ ] **Step 4: Define the exact public model**

    export type EntityKey = string & { readonly entityKey: unique symbol }
    export type AuthorityState = 'authoritative' | 'partial' | 'stale' | 'unknown'
    export type DestinationId =
      | 'project' | 'library' | 'lab' | 'depot' | 'bus'
      | 'review' | 'triage' | 'garden' | 'council' | 'unknown'
    export type QualityTier = 'efficient' | 'balanced' | 'detailed'
    export interface Vec3 { x: number; y: number; z: number }
    export type EntityIdentity =
      | { kind: 'profile'; connectionId: string; profile: string }
      | { kind: 'session'; connectionId: string; profile: string; sessionId: string }
      | { kind: 'subagent'; connectionId: string; profile: string; sessionId: string; subagentId: string }
      | { kind: 'kanban'; connectionId: string; board: string; taskId: string; runId?: string; workerId?: string }
    export interface SourceHealth {
      source: string
      authority: AuthorityState
      observedAt: number
      error?: string
    }
    export interface CameraLandmark {
      id: string
      alpha: number
      beta: number
      radius: number
      target: Vec3
      minBeta: number
      maxBeta: number
      minRadius: number
      maxRadius: number
    }
    export interface WorldBounds { min: Vec3; max: Vec3 }
    export interface ModelManifestEntry {
      id: string
      uri: string
      maxTriangles: number
      lods: readonly { distance: number; node: string }[]
      cameraAnchor?: Vec3
      occlusionGroup?: string
      requiredClips: readonly string[]
    }
    export interface NavigationManifest {
      meshUri: string
      links: readonly { from: Vec3; to: Vec3; bidirectional: boolean }[]
    }
    export interface LunarEntity {
      key: EntityKey
      identity: EntityIdentity
      observedAt: number
      authority: AuthorityState
      destination: DestinationId
      animation: string
      projectId?: string
    }
    export interface LunarCitySnapshot {
      revision: number
      observedAt: number
      entities: ReadonlyMap<EntityKey, LunarEntity>
      sources: readonly SourceHealth[]
    }
    export interface WorldManifestV2 {
      version: 2
      source: { sha256: string }
      models: readonly ModelManifestEntry[]
      camera: { overview: CameraLandmark; bounds: WorldBounds }
      navigation: NavigationManifest
      destinations: Readonly<Record<DestinationId, Vec3>>
    }
    export interface LunarCityWorldHandle {
      applySnapshot(snapshot: LunarCitySnapshot): void
      dispatchCamera(intent: CameraIntent): void
      setQuality(tier: QualityTier): void
      destroy(): void
    }

- [ ] **Step 5: Implement selective engine creation and teardown**

    async function loadBabylonModules() {
      const [{ Engine }, { Scene }] = await Promise.all([
        import('@babylonjs/core/Engines/engine'),
        import('@babylonjs/core/scene')
      ])
      await import('@babylonjs/loaders/glTF')
      return { Engine, Scene }
    }

    export async function createLunarCityWorld(canvas, manifest, emit, modules) {
      const loaded = modules ?? await loadBabylonModules()
      const engine = new loaded.Engine(canvas, true, {
        powerPreference: 'low-power',
        preserveDrawingBuffer: false,
        stencil: false
      })
      const scene = await createWorldScene(engine, manifest, emit, loaded.Scene)
      return {
        applySnapshot: snapshot => scene.applySnapshot(snapshot),
        dispatchCamera: intent => scene.dispatchCamera(intent),
        setQuality: tier => scene.setQuality(tier),
        destroy: () => {
          scene.dispose()
          engine.dispose()
        }
      }
    }

The route component owns one canvas and one handle. It does not import Babylon.js at module scope. Unmount awaits disposal and clears nanostore subscriptions.

- [ ] **Step 6: Reconstruct the static approved composition**

Load only v2 GLBs and manifest-declared textures. Place terrain, buildings, interiors, leaders, vehicles, and props from manifest transforms. Use baked and emissive material response for the base look, ambient scene color for fill, and at most one manifest-configured directional light; only near Balanced and Detailed tiers may enable its constrained shadow generator. Freeze static world matrices and materials after setup where Babylon.js permits it. Tag every selectable node with a typed entity or landmark ID; never infer identity from mesh names at mutation time.

- [ ] **Step 7: Verify bundle isolation and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/manifest.test.ts src/app/lunar-city/world/create-world.test.ts
    npm run typecheck --workspace apps/desktop
    npm run build --workspace apps/desktop
    rg "@babylonjs" apps/desktop/src/app/lunar-city/index.tsx
    git add apps/desktop/package.json package-lock.json apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): reconstruct Lunar City in Babylon.js"

Expected: tests and build pass; the route entry has no static Babylon.js import; the approved JPG is not requested by the canvas.

### Task 5: Add the angled SimCity camera, selection, follow, and occlusion

**Files:**
- Create: apps/desktop/src/app/lunar-city/world/camera-controller.ts
- Create: apps/desktop/src/app/lunar-city/world/camera-controller.test.ts
- Create: apps/desktop/src/app/lunar-city/world/occlusion.ts
- Create: apps/desktop/src/app/lunar-city/world/occlusion.test.ts
- Create: apps/desktop/src/app/lunar-city/components/camera-controls.tsx
- Create: apps/desktop/src/app/lunar-city/components/camera-controls.test.tsx
- Modify: apps/desktop/src/app/lunar-city/world/world-scene.ts
- Modify: apps/desktop/src/app/lunar-city/index.tsx

**Interfaces:**
- Consumes: CameraLandmark, manifest camera anchors, typed pick metadata, and CameraIntent.
- Produces: CameraController.dispatch(intent), OcclusionController.update(camera, selection), and accessible camera controls.

- [ ] **Step 1: Write failing camera-boundary tests**

    it('clamps orbit, tilt, radius, and target to the manifest bounds', () => {
      const controller = createCameraController(camera, overview, bounds)
      controller.dispatch({ kind: 'orbit', deltaAlpha: 99, deltaBeta: 99 })
      controller.dispatch({ kind: 'zoom', delta: -999 })
      expect(camera.beta).toBe(overview.maxBeta)
      expect(camera.radius).toBe(overview.minRadius)
      expect(bounds.contains(camera.target)).toBe(true)
    })

    it('returns exactly to the approved overview', () => {
      controller.dispatch({ kind: 'return-to-city' })
      expect(readPose(camera)).toEqual(poseFromLandmark(overview))
    })

- [ ] **Step 2: Write failing focus, follow, and occlusion tests**

    controller.dispatch({ kind: 'focus', entityKey: workerKey, follow: true })
    worker.position.set(8, 0, 4)
    controller.update(16)
    expect(camera.target).toEqualVector(workerFocusAnchor(worker))

    occlusion.update(camera, selectedIndoorWorker)
    expect(roof.material.alpha).toBeLessThan(1)
    occlusion.clear()
    expect(roof.material.alpha).toBe(1)

- [ ] **Step 3: Implement the camera intent contract**

    export type CameraIntent =
      | { kind: 'orbit'; deltaAlpha: number; deltaBeta: number }
      | { kind: 'pan'; deltaX: number; deltaZ: number }
      | { kind: 'zoom'; delta: number }
      | { kind: 'focus'; entityKey: EntityKey; follow: boolean }
      | { kind: 'clear-focus' }
      | { kind: 'return-to-city' }

Use ArcRotateCamera with custom input translation, inertia disabled during deterministic tests, manifest clamping after each input, and eased focus transitions based on elapsed time. Primary drag orbits, secondary drag pans, wheel and pinch zoom, and keyboard buttons dispatch the same intents.

- [ ] **Step 4: Implement picking and obstruction fading**

Use Babylon.js scene picking only to retrieve typed metadata and emit select intents. Empty terrain emits clear-focus. For selected entities, cast from camera to the focus anchor and fade only manifest-declared foreground roof or wall groups intersecting the ray. Restore original materials when selection changes; never move the camera through geometry.

- [ ] **Step 5: Implement accessible camera controls**

Render native buttons for Rotate Left, Rotate Right, Tilt Up, Tilt Down, Pan North, Pan South, Pan East, Pan West, Zoom In, Zoom Out, Stop Following, and Return to City. Expose follow state and focused identity in a live region without announcing every animation frame.

- [ ] **Step 6: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/world/camera-controller.test.ts src/app/lunar-city/world/occlusion.test.ts src/app/lunar-city/components/camera-controls.test.tsx
    npm run typecheck --workspace apps/desktop
    git add apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): navigate Lunar City in 3D"

### Task 6: Add truthful identity, navigation, animation, LOD, and frame scheduling

**Files:**
- Modify: apps/desktop/src/app/lunar-city/model.ts
- Create: apps/desktop/src/app/lunar-city/identity.ts
- Create: apps/desktop/src/app/lunar-city/state-map.ts
- Create: apps/desktop/src/app/lunar-city/world/navigation.ts
- Create: apps/desktop/src/app/lunar-city/world/entities.ts
- Create: apps/desktop/src/app/lunar-city/world/scheduler.ts
- Create: apps/desktop/src/app/lunar-city/world/quality.ts
- Create: apps/desktop/src/app/lunar-city/identity.test.ts
- Create: apps/desktop/src/app/lunar-city/state-map.test.ts
- Create: apps/desktop/src/app/lunar-city/world/navigation.test.ts
- Create: apps/desktop/src/app/lunar-city/world/entities.test.ts
- Create: apps/desktop/src/app/lunar-city/world/scheduler.test.ts
- Create: apps/desktop/src/app/lunar-city/world/quality.test.ts
- Modify: apps/desktop/src/app/lunar-city/world/world-scene.ts

**Interfaces:**
- Produces: entityKey(identity): EntityKey, mapObservedState(input): SpatialState, NavigationController.move(entity, destination), EntityRegistry.reconcile(snapshot), FrameScheduler.noteInteraction(now), FrameScheduler.setVisible(visible), FrameScheduler.tick(now), and qualitySettings(tier).

- [ ] **Step 1: Write failing identity and unknown-state tests**

    expect(entityKey({ kind: 'profile', connectionId: 'a', profile: 'worker' })).not.toBe(
      entityKey({ kind: 'profile', connectionId: 'b', profile: 'worker' })
    )
    expect(mapObservedState({ source: 'kanban', status: 'mystery', fresh: true })).toEqual({
      animation: 'unavailable',
      destination: 'unknown',
      authority: 'unknown'
    })

- [ ] **Step 2: Implement collision-safe keys and the exact state table**

    export interface ObservedState {
      source: 'session' | 'subagent' | 'kanban'
      status: string
      fresh: boolean
    }
    export interface SpatialState {
      animation: string
      destination: DestinationId
      authority: AuthorityState
    }

    export function entityKey(identity) {
      const fields = identity.kind === 'profile'
        ? [identity.kind, identity.connectionId, identity.profile]
        : identity.kind === 'session'
          ? [identity.kind, identity.connectionId, identity.profile, identity.sessionId]
          : identity.kind === 'subagent'
            ? [identity.kind, identity.connectionId, identity.profile, identity.sessionId, identity.subagentId]
            : [identity.kind, identity.connectionId, identity.board, identity.taskId, identity.runId ?? '', identity.workerId ?? '']
      return fields.map(value => encodeURIComponent(String(value))).join(':')
    }

Map ready to bus, working to project, resource waits to depot, review to review, failure and blocked to triage, heartbeat and idle to garden, orchestration and dependency to council, completion to project, and every unknown or stale value to unavailable at unknown.

- [ ] **Step 3: Write failing navigation and reconciliation tests**

    await navigation.move(worker, 'review')
    expect(query.computePath).toHaveBeenCalledWith(worker.position, destination('review'))
    expect(worker.animation).toBe('walk')

    registry.reconcile(snapshotWith100IdenticalWorkers)
    expect(registry.instancedGroup('worker:idle').count).toBe(100)
    registry.reconcile(snapshotWith100IdenticalWorkers)
    expect(registry.createdMeshCount).toBe(createdAfterFirstReconcile)

- [ ] **Step 4: Implement Recast navigation and entity reconciliation**

Initialize RecastJSPlugin behind a NavigationQuery interface so tests use a fake. Compute a path only when origin, destination, or walkability changes. Use instancing for visually identical static workers and props; use animation groups only for nearby moving or selected workers. Retain keyed entities in place across snapshots. Distant entities use declared LOD models or truthful project-level aggregates with exact counts and state distributions.

- [ ] **Step 5: Write and implement 30/15/0 scheduling tests**

    scheduler.noteInteraction(0)
    scheduler.tick(1000)
    expect(renderer.targetFps).toBe(30)
    scheduler.tick(6000)
    expect(renderer.targetFps).toBe(15)
    scheduler.setVisible(false)
    expect(renderer.targetFps).toBe(0)
    expect(renderer.stopRenderLoop).toHaveBeenCalledOnce()

Interactive mode lasts five seconds after camera or selection input. Ambient mode renders only when a visual deadline is due. Hidden, minimized, unmounted, or context-lost mode stops the engine render loop. Reduced motion places entities at destinations and uses declared static poses.

- [ ] **Step 6: Implement quality tiers and automatic degradation**

Define renderScale as output pixels divided by display pixels and apply it through engine.setHardwareScalingLevel(1 / renderScale). Efficient disables dynamic shadows, uses renderScale 0.70, short animation distance, and aggressive LOD. Balanced uses renderScale 0.85, near-only shadows, and normal LOD. Detailed uses renderScale 1.0, the same single directional light, and longer animation distance.

After 120 over-budget interactive frames, degrade in this exact order: renderScale 1.0 to 0.85 to 0.70; disable dynamic shadows; shorten animation distance; remove decorative meshes; advance non-selected entities to their next declared LOD. Recover one step only after 600 under-budget ambient or interactive frames. Never change identity, selection, conversation, command behavior, or the presence of an authoritative worker.

- [ ] **Step 7: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/identity.test.ts src/app/lunar-city/state-map.test.ts src/app/lunar-city/world
    git add apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): animate Lunar City efficiently"

### Task 7: Build the profile, session, and subagent live adapter

**Files:**
- Create: apps/desktop/src/app/lunar-city/store.ts
- Create: apps/desktop/src/app/lunar-city/adapters/fleet.ts
- Create: apps/desktop/src/app/lunar-city/adapters/sessions.ts
- Create: apps/desktop/src/app/lunar-city/adapters/reconciler.ts
- Create: apps/desktop/src/app/lunar-city/adapters/fleet.test.ts
- Create: apps/desktop/src/app/lunar-city/adapters/sessions.test.ts
- Create: apps/desktop/src/app/lunar-city/adapters/reconciler.test.ts
- Create: apps/desktop/src/app/lunar-city/store.test.ts
- Modify: apps/desktop/src/app/lunar-city/index.tsx

**Interfaces:**
- Consumes: $fleetRoster, session rows, $subagentsBySession, and native subagent events.
- Produces: $lunarCitySnapshot, startLunarCityReconciler(): () => void, and ordered LunarDelta values.

- [ ] **Step 1: Write failing duplicate-owner and terminal-subagent tests**

    expect(normalizeRoster(roster).map(row => row.key)).toEqual([
      'profile:local:worker',
      'profile:ssh-1:worker'
    ])
    expect(normalizeSubagent(completeEvent).authority).toBe('authoritative')
    expect(normalizeSubagent(completeEvent).destination).toBe('project')

- [ ] **Step 2: Run tests and verify failure**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters/fleet.test.ts src/app/lunar-city/adapters/sessions.test.ts

Expected: FAIL because the adapters do not exist.

- [ ] **Step 3: Implement event-first normalization and reconciliation**

Refresh the profile fleet on route mount, window focus, and registry change, never per render frame. Fold session and subagent updates by monotonic revision. Mark a source stale after its declared freshness window while retaining last-known typed identity and timestamp. A revision gap or reconnect schedules one bounded authoritative reread and deduplicates concurrent rereads.

    export interface LunarDelta {
      revision: number
      upserts: readonly LunarEntity[]
      removals: readonly EntityKey[]
      sources: readonly SourceHealth[]
    }

    export function shouldReconcile(currentRevision, incomingRevision) {
      return incomingRevision > currentRevision + 1
    }

- [ ] **Step 4: Apply snapshots without React animation renders**

The route subscribes once to $lunarCitySnapshot and calls world.applySnapshot(snapshot) imperatively. React state contains only selection, camera control state, dialogue, confirmation, and source-health UI. Snapshot changes must not recreate the Babylon.js scene.

- [ ] **Step 5: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters src/app/lunar-city/store.test.ts
    npm run typecheck --workspace apps/desktop
    git add apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): populate Lunar City from Hermes sessions"

### Task 8: Add optional Kanban data and stable project compounds

**Files:**
- Create: apps/desktop/src/app/lunar-city/adapters/kanban.ts
- Create: apps/desktop/src/app/lunar-city/adapters/kanban.test.ts
- Modify: apps/desktop/src/app/lunar-city/adapters/reconciler.ts
- Modify: apps/desktop/src/app/lunar-city/world/world-scene.ts
- Modify: apps/desktop/public/lunar-city/v2/world-manifest.v2.json

**Interfaces:**
- Consumes: pluginRest('kanban', ...), pluginSocket('kanban', ...), and manifest project-compound slots.
- Produces: createKanbanCitySource(): KanbanCitySource, task/run/worker entities, and stable connection/project compounds.

- [ ] **Step 1: Write failing unavailable-plugin and event-gap tests**

    await expect(source.read()).resolves.toMatchObject({
      health: 'unavailable',
      entities: []
    })
    expect(source.onFrame({ events: [{ id: 9 }] }).needsReconcile).toBe(true)

- [ ] **Step 2: Run tests and verify failure**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters/kanban.test.ts

Expected: FAIL because createKanbanCitySource is not exported.

- [ ] **Step 3: Implement bounded reads and socket invalidation**

Read boards, the selected board, and active workers at initial reconciliation. Read a full task only when it is selected or an event invalidates its visible diagnostic state. Subscribe through pluginSocket to Kanban events; use socket frames only to invalidate, and bounded REST reads as authority. Missing plugin or endpoint closes Kanban-specific buildings and actions without failing profiles, sessions, conversations, or camera movement.

- [ ] **Step 4: Build stable 3D project compounds**

Key compounds by connection plus canonical project or repository ID. Allocate manifest compound slots deterministically. Overflow expands to declared outer-ring slots; it never overlaps geometry or creates a second city. Navigation links connect each new compound to the shared city mesh. Keep task, run, blocker, diagnostic, comment, log, and durable event evidence separate.

    export function compoundKey(connectionId, projectId) {
      return [connectionId, projectId].map(encodeURIComponent).join('::')
    }

- [ ] **Step 5: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters/kanban.test.ts src/app/lunar-city/world/navigation.test.ts
    git add apps/desktop/src/app/lunar-city apps/desktop/public/lunar-city/v2/world-manifest.v2.json
    git commit -m "feat(desktop): map Kanban work into Lunar City"

### Task 9: Add persistent leader text and voice conversations

**Files:**
- Create: apps/desktop/src/app/lunar-city/leader-sessions.ts
- Create: apps/desktop/src/app/lunar-city/leader-sessions.test.ts
- Create: apps/desktop/src/app/lunar-city/components/leader-dialogue.tsx
- Create: apps/desktop/src/app/lunar-city/components/leader-dialogue.test.tsx
- Modify: apps/desktop/src/app/lunar-city/world/entities.ts
- Modify: apps/desktop/src/app/lunar-city/index.tsx

**Interfaces:**
- Produces: resolveLeaderSession(owner): Promise<LeaderSession> and a dialogue component that uses standard prompt.submit, transcript events, and existing voice hooks.

- [ ] **Step 1: Write failing ownership, persistence, and camera-continuity tests**

    export interface LeaderOwner { connectionId: string; profile: string }
    export interface LeaderSession { storedId: string; runtimeId: string }

    expect(await resolveLeaderSession({ connectionId: 'a', profile: 'owl' })).toEqual({
      storedId: 's1',
      runtimeId: 'r1'
    })
    expect(requestGatewayForAgent).not.toHaveBeenCalledWith(
      'b',
      expect.anything(),
      expect.anything(),
      expect.anything()
    )
    fireEvent.click(screen.getByRole('button', { name: 'Rotate Left' }))
    expect(screen.getByRole('dialog', { name: /Owl leader/i })).toBeVisible()

- [ ] **Step 2: Implement durable exact-owner mapping**

Persist version 1 with a leaders record keyed by encoded connection ID plus canonical profile identity. Verify a stored session row still belongs to that exact connection and profile before resuming it. A missing, deleted, or mismatched row creates a normal profile-scoped session and replaces the mapping; it never adopts a similarly named foreign session.

- [ ] **Step 3: Reuse the standard streamed text path**

Reuse the standard session state slice and requestForSessionProfile. The overlay shows transcript, composer, listening, thinking, speaking, interrupted, unavailable, and error states plus Open Full Chat. It does not introduce another conversation protocol or replace the desktop chat surface.

- [ ] **Step 4: Reuse existing voice behavior**

Mount the existing useVoiceConversation hook with the leader session composer scope and routed submit and interrupt callbacks. Reuse the current transcription, playback, wake-word, barge-in, stop-phrase, and text-fallback paths without copying their implementations.

- [ ] **Step 5: Connect truthful leader animation**

Map actual dialogue state to the leader model clips idle, listen, talk, think, acknowledge, and unavailable. Camera motion continues while the panel is open. A voice or transport failure changes the leader to unavailable only when the authoritative conversation state says so; it never marks an unsent message delivered.

- [ ] **Step 6: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/leader-sessions.test.ts src/app/lunar-city/components/leader-dialogue.test.tsx src/app/chat/composer/hooks/use-voice-conversation.test.tsx
    git add apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): talk with Lunar City leaders"

### Task 10: Add evidence inspectors and identity-safe command brokerage

**Files:**
- Create: apps/desktop/src/app/lunar-city/command-broker.ts
- Create: apps/desktop/src/app/lunar-city/command-broker.test.ts
- Create: apps/desktop/src/app/lunar-city/components/entity-inspector.tsx
- Create: apps/desktop/src/app/lunar-city/components/entity-inspector.test.tsx
- Create: apps/desktop/src/app/lunar-city/components/command-confirmation.tsx
- Create: apps/desktop/src/app/lunar-city/components/command-confirmation.test.tsx

**Interfaces:**
- Produces: planCommand(intent, snapshot): CommandPlan and executeCommand(plan): Promise<CommandReceipt>.

- [ ] **Step 1: Write failing safety and ambiguity tests**

    export type CommandVerification =
      | 'verified' | 'rejected' | 'timed_out' | 'verification_required'
    export interface ReadbackPlan {
      kind: 'session' | 'subagent' | 'kanban-task' | 'kanban-run'
      id: string
    }
    export interface CommandPlan {
      entityKey: EntityKey
      owner: LeaderOwner
      method: string
      params: Record<string, unknown>
      consequence: string
      confirmation: boolean
      readback: ReadbackPlan
    }
    export interface CommandReceipt {
      verification: CommandVerification
      identity: EntityIdentity
      response?: unknown
      error?: string
    }

    expect(planCommand({ kind: 'open-session', entityKey }, snapshot).confirmation).toBe(false)
    expect(planCommand({ kind: 'terminate-run', entityKey }, snapshot).confirmation).toBe(true)
    expect(() => planCommand(ambiguousIntent, ambiguousSnapshot)).toThrow(
      /owner is ambiguous/
    )

- [ ] **Step 2: Run tests and verify failure**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/command-broker.test.ts

Expected: FAIL because planCommand does not exist.

- [ ] **Step 3: Implement pure planning and the direct allowlist**

Every CommandPlan contains exact entity identity, owning route, method, typed parameters, consequence text, confirmation requirement, and authoritative readback. Direct actions are limited to opening the owning session, inspecting evidence, and sending ordinary guidance. Every supported mutation outside that list is confirmed.

- [ ] **Step 4: Route only through existing Hermes operations**

Use prompt.submit, session.steer, session.interrupt, subagent.steer, and subagent.interrupt through the owning gateway route. Use existing Kanban plugin endpoints for retry, reclaim, reassign, dispatch, patch, and run termination. Do not construct shell commands or create a new model tool.

- [ ] **Step 5: Require authoritative readback**

After a mutation, reread the owning session, subagent registry, Kanban task, or Kanban run. Return verified, rejected, timed_out, or verification_required. Only verified readback changes authoritative world animation. Timeout and ambiguous write outcome never trigger a blind retry.

- [ ] **Step 6: Render exact accessible confirmations**

The confirmation names connection, profile, session, project, board, task, run, and worker IDs when present; current state; requested operation; and consequence. The confirmation is a React dialog outside canvas with focus trapping, cancel as the safe default, and no acceptance based on display name.

- [ ] **Step 7: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/command-broker.test.ts src/app/lunar-city/components/entity-inspector.test.tsx src/app/lunar-city/components/command-confirmation.test.tsx
    git add apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): control Lunar City workers safely"

### Task 11: Add accessibility, renderer fallback, and multi-angle visual regression

**Files:**
- Create: apps/desktop/src/app/lunar-city/components/entity-list.tsx
- Create: apps/desktop/src/app/lunar-city/components/source-health.tsx
- Create: apps/desktop/src/app/lunar-city/components/quality-control.tsx
- Create: apps/desktop/e2e/fixtures/lunar-city-overview-mask.png
- Modify: apps/desktop/src/app/lunar-city/index.tsx
- Modify: apps/desktop/src/app/lunar-city/lunar-city.css
- Modify: apps/desktop/src/app/lunar-city/index.test.tsx
- Modify: apps/desktop/e2e/lunar-city.spec.ts

**Interfaces:**
- Produces: keyboard and screen-reader equivalents for every selected 3D entity and camera action, plus a React-only operational fallback when WebGL cannot render.

- [ ] **Step 1: Write failing keyboard, quality, and context-loss tests**

    expect(screen.getByRole('button', {
      name: /Pip.*working.*Research Lab/i
    })).toBeVisible()
    fireEvent(canvas, new Event('webglcontextlost', { cancelable: true }))
    expect(screen.getByText(/3D world renderer unavailable/i)).toBeVisible()
    expect(screen.getByRole('button', { name: /Talk to Fox Scientist/i })).toBeEnabled()
    expect(screen.getByRole('combobox', { name: /3D quality/i })).toHaveValue('efficient')

- [ ] **Step 2: Implement synchronized accessible controls**

Use native entity controls ordered by manifest district and current camera order. Pair every color, animation, and source-health state with text. Expose camera buttons from Task 5. Reduced motion places entities at destinations, disables eased camera travel and looping clips, and retains selection and conversations.

- [ ] **Step 3: Implement context-loss recovery and React-only fallback**

On webglcontextlost, prevent default, stop the scheduler, dispose scene resources that remain valid to dispose, and attempt one engine restoration from the latest immutable snapshot. If restoration fails, leave the entity list, leader dialogue, evidence inspector, quality control, and authorized commands mounted. Do not reload the whole Electron renderer.

- [ ] **Step 4: Add deterministic visual scenarios**

Capture the approved overview with dynamic inhabitants disabled, then capture north, east, south, and west rotations; every major district; an indoor worker with roof and wall fading; each leader family; and near worker views on Efficient and Balanced quality. The overview comparison masks only explicitly dynamic source regions and evaluates composition, palette relationships, landmark placement, and silhouettes rather than requiring pixel identity between 2D reference and 3D render.

Assert request logs never contain moon-settlement-approved.jpg after the route loads.

- [ ] **Step 5: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city
    npm run build --workspace apps/desktop
    npm exec --workspace apps/desktop -- playwright test e2e/lunar-city.spec.ts
    git add apps/desktop/src/app/lunar-city apps/desktop/e2e/lunar-city.spec.ts apps/desktop/e2e/fixtures/lunar-city-overview-mask.png
    git commit -m "test(desktop): verify accessible Lunar City 3D world"

### Task 11C: Represent the complete Hermes Bots roster

**Files:**
- Create: apps/desktop/src/app/lunar-city/adapters/bot-roster-details.ts
- Create: apps/desktop/src/app/lunar-city/adapters/bot-roster-details.test.ts
- Modify: apps/desktop/src/app/lunar-city/model.ts
- Modify: apps/desktop/src/app/lunar-city/adapters/reconciler.ts
- Modify: apps/desktop/src/app/lunar-city/world/entities.ts
- Modify: apps/desktop/src/app/lunar-city/components/entity-list.tsx

**Interfaces:**
- Produces: immutable, exact-source title/group presentation metadata and deterministic bounded placement for every enumerated profile, without changing profile identity or command authority.

- [ ] **Step 1: Write failing full-roster and collision tests**

Cover every profile returned by the fleet source, multiple group memberships, a profile with no group, same-named profiles on two connections, retained unavailable profiles, malformed metadata, and deterministic overflow beyond a district's near-worker budget.

- [ ] **Step 2: Read exact-source profile metadata**

Use the existing scoped standard profile request path for each enumerated `{connectionId, profile}` source. Normalize only presentation-safe configured title and group fields. Do not import Hermes Bots plugin internals into the Lunar City route, use ambient active-profile state, or make title/group values authoritative identifiers.

- [ ] **Step 3: Place and expose every profile**

Map declared groups to manifest districts through an explicit table, then assign stable slots by canonical entity key with bounded overflow/aggregate LOD. Profiles in several groups receive one primary physical placement plus all memberships in the accessible inspector. Profiles with no valid group use the general garden; unavailable profiles remain visible in the unavailable district.

- [ ] **Step 4: Verify and commit**

    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters/bot-roster-details.test.ts src/app/lunar-city/adapters/fleet.test.ts src/app/lunar-city/adapters/reconciler.test.ts src/app/lunar-city/components/entity-list.test.tsx src/app/lunar-city/world/entities.test.ts
    npm run typecheck --workspace apps/desktop
    npm run lint --workspace apps/desktop
    git add apps/desktop/src/app/lunar-city
    git commit -m "feat(desktop): represent the complete Hermes Bots roster"

### Task 11D: Add collision-free low-power leader and worker identities

**Files:**
- Modify: apps/desktop/scripts/lunar-city/build-models.mjs
- Modify: apps/desktop/scripts/lunar-city/build-models.test.mjs
- Modify: apps/desktop/scripts/lunar-city/modeling/characters.mjs
- Modify: apps/desktop/scripts/lunar-city/validate-assets.mjs
- Modify: apps/desktop/scripts/lunar-city/validate-assets.test.mjs
- Modify: apps/desktop/public/lunar-city/v2/world-manifest.v2.json
- Modify: apps/desktop/src/app/lunar-city/manifest.ts
- Modify: apps/desktop/src/app/lunar-city/world/entities.ts
- Modify: focused Lunar City asset/entity tests as required

**Interfaces:**
- Produces: six non-reused leader visual identities, 19 distinct group worker kits, and a deterministic collision-free near signature for every exact-source profile while retaining shared low-poly rigs, GPU buffers, atlases, materials, clips, and bounded LOD cost.

- [ ] **Step 1: Write failing identity and budget tests**

Cover duplicate leader visual IDs, missing or reused group kits, complete worker-signature collisions across exact profile keys, deterministic signatures across reorder/reconnect, and a roster larger than the available near slots. Prove the validator rejects a design that achieves uniqueness by allocating a heavyweight mesh, skeleton, material, or texture set per profile.

- [ ] **Step 2: Build bounded modular variation**

Keep one shared low-poly robot-child rig and shared GPU buffers. Define a bounded body/head/accessory/palette/emblem vocabulary, one visibly distinct kit for each of the 19 declared groups, and six genuinely distinct leader species/silhouettes. Use baked-light texture atlases, a bounded shared material set, hardware/thin instances, and per-instance palette/emblem data. Reuse of invisible internals is expected; reuse of a complete presented signature for two different exact profiles is forbidden.

- [ ] **Step 3: Connect three-tier character LOD**

Selected and near characters may evaluate full animation. Mid-distance characters use reduced clips or poses. Far characters use static low-poly instances or truthful aggregates with exact counts and state distributions. Hidden, minimized, idle, and route-unmounted behavior must preserve the existing frame scheduler. Balanced remains capped at 30 FPS with real-time shadows and expensive post-processing disabled by default.

- [ ] **Step 4: Validate and commit**

    node --test apps/desktop/scripts/lunar-city/build-models.test.mjs apps/desktop/scripts/lunar-city/validate-assets.test.mjs
    node apps/desktop/scripts/lunar-city/validate-assets.mjs apps/desktop/public/lunar-city/v2/world-manifest.v2.json
    npm run test:ui --workspace apps/desktop -- src/app/lunar-city/manifest.test.ts src/app/lunar-city/world/entities.test.ts
    npm run typecheck --workspace apps/desktop
    npm run lint --workspace apps/desktop
    git diff --check
    git add apps/desktop/scripts/lunar-city apps/desktop/public/lunar-city/v2 apps/desktop/src/app/lunar-city/world
    git commit -m "feat(desktop): diversify Lunar City characters efficiently"

### Task 12: Enforce packaged-Electron 3D performance and live acceptance

**Files:**
- Create: apps/desktop/scripts/perf/lunar-city.mjs
- Create: apps/desktop/scripts/perf/lunar-city.test.mjs
- Modify: apps/desktop/package.json
- Create: apps/desktop/e2e/lunar-city-live-sources.spec.ts
- Modify: docs/lunar-city-design-handoff.md
- Modify: design-qa.md

**Interfaces:**
- Produces: a versioned JSON receipt containing evidence class, hardware, OS, Electron, power state, display scale, quality tier, internal scale, FPS, frame and update times, CPU delta, GPU-memory delta, resident-memory drift, draw calls, visible triangles, active animations, listeners, entities, textures, and timers.

- [ ] **Step 1: Write failing receipt-schema and budget tests**

    assert.equal(validateReceipt({
      scenario: 'hidden',
      renderFrames: 1
    }).ok, false)

    assert.match(validateReceipt({
      scenario: 'balanced-overview',
      drawCalls: 181,
      visibleTriangles: 1_000_000
    }).errors.join('\n'), /draw calls exceed 180/)

    assert.match(validateReceipt({
      scenario: 'balanced-worker-focus',
      drawCalls: 180,
      visibleTriangles: 2_000_001
    }).errors.join('\n'), /triangles exceed 2000000/)

- [ ] **Step 2: Run the tests and verify failure**

    node --test apps/desktop/scripts/perf/lunar-city.test.mjs

Expected: FAIL because validateReceipt does not exist.

- [ ] **Step 3: Implement measured packaged scenarios**

Add scenarios for route-unmounted, hidden, minimized, visible idle, 25, 100, and 250 inhabitants; Balanced overview; Balanced worker focus; continuous orbit and zoom; indoor occlusion; leader text and fake voice conversation while moving the camera; every quality tier; WebGL loss and recovery; and 30-minute stability. Capture raw samples before evaluating budgets.

Add this desktop package script:

    "perf:lunar-city": "node scripts/perf/lunar-city.mjs"

The validator enforces:

- Hidden, minimized, and unmounted: zero render frames and at most 0.5 additional CPU percentage points.
- Visible idle: at most 3 additional CPU percentage points.
- 100 active inhabitants: p95 frame time at most 33.3 ms, p95 world update at most 6 ms, and at most 12 additional CPU percentage points.
- 250 observed inhabitants with LOD: p95 frame time at most 33.3 ms and at most 18 additional CPU percentage points.
- Incremental GPU memory: at most 256 MiB.
- Balanced overview: at most 180 draw calls and 1.5 million visible triangles.
- Balanced worker focus: at most 220 draw calls and 2 million visible triangles.
- Thirty-minute stability: at most 75 MiB resident-memory drift and no monotonic entity, texture, listener, animation, or timer growth.

- [ ] **Step 4: Run complete deterministic desktop verification**

    npm run typecheck --workspace apps/desktop
    npm run lint --workspace apps/desktop
    npm run test:ui --workspace apps/desktop -- src/app/lunar-city
    node --test apps/desktop/scripts/lunar-city/build-models.test.mjs apps/desktop/scripts/lunar-city/validate-assets.test.mjs apps/desktop/scripts/perf/lunar-city.test.mjs
    node apps/desktop/scripts/lunar-city/validate-assets.mjs apps/desktop/public/lunar-city/v2/world-manifest.v2.json
    npm run build --workspace apps/desktop
    npm exec --workspace apps/desktop -- playwright test e2e/lunar-city.spec.ts e2e/lunar-city-live-sources.spec.ts
    npm run perf:lunar-city --workspace apps/desktop
    git diff --check

Expected: deterministic, asset, visual, accessibility, packaged, and performance lanes pass and emit separately labeled receipts.

- [ ] **Step 5: Capture supervised live Hermes acceptance separately**

With explicit operator supervision, capture real receipts for profile enumeration, leader session persistence, text, voice, subagent state, Kanban events, one safe guidance action, one confirmed disruptive action, worker focus and follow, disconnect and reconnect, and authoritative readback. Do not weaken identity, freshness, capability, authority, or confirmation gates to obtain green evidence.

- [ ] **Step 6: Update handoff and commit**

Record exact HEAD, clean state, build stamp, source and model digests, dependency versions, commands, test counts, receipt paths, evidence classes, performance hardware, known constraints, and any live-evidence gaps.

    git add apps/desktop/scripts/perf apps/desktop/package.json apps/desktop/e2e/lunar-city-live-sources.spec.ts docs/lunar-city-design-handoff.md design-qa.md
    git commit -m "test(desktop): certify playable Lunar City 3D world"
    git status --short --branch
    git rev-parse HEAD

Expected: clean worktree and an exact final commit. Push, pull request, and merge remain operator-controlled unless separately requested.
