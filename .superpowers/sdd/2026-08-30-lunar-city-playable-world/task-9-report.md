# Task 9 report — live profile-leader conversations

## Result

Task 9B integrates the Task 9A session and dialogue primitives into the
dedicated Lunar City route. A live typed profile entity is now the only source
of a selected leader owner. The route resolves one persistent ordinary Hermes
session through that exact `{ connectionId, canonical profile }` owner, then
renders a non-modal standard-session transcript/composer over the running 3D
city.

Static animal/world picks remain camera-only. They cannot derive an owner,
open a chat, or select an ambient profile.

## Implementation

- The compact profile-leader rail is derived from immutable snapshot profile
  entities only, in canonical encoded-owner order. It does not rerender the
  React route for worker-only snapshot publications.
- `LeaderDialogueRuntime` reads only its resolved runtime's normal
  `$sessionStates` slice and sends `prompt.submit`/`session.interrupt` through
  `requestForSessionProfile` with a hard-rejecting ambient callback.
- The runtime mounts the established `useVoiceConversation` hook and reuses
  the normal client-direct then relay transcription ladder. It owns no
  recorder, WebSocket, transcript, or alternate transport. Voice is offered
  only when the selected owner is the current exact Desktop connection/profile;
  text remains available otherwise and after a voice failure.
- Voice starts with prior assistant messages marked consumed, so the existing
  voice hook never autoplays conversation history. New voice submissions and
  barge-in interrupts use the resolved exact session route.
- Actual session/voice lifecycle is projected to GLB-declared leader clips
  only. The Babylon bridge stops a leader's prior declared group and starts
  the selected exact state group; it does nothing when a declared group is
  absent. Button clicks and RPC ACKs do not animate a leader optimistically.
- Leader model selection is a deterministic presentation mapping from the
  entire owner key. Camera framing targets only that model's static anchor;
  the owner remains the sole identity for all conversation operations.
- Close, owner/session replacement, unavailable voice scope, and route
  unmount tear down existing hook voice state. The panel does not trap the
  camera controls.
- Open Full Chat re-publishes the exact owner hint and uses ordinary main-chat
  navigation for the same durable stored session.

## TDD evidence

Initial RED states were observed before their corresponding implementations:

- `leader-runtime.test.ts`: missing `leader-runtime` module.
- `world/create-world.test.ts`: `handle.setLeaderAnimation is not a function`.
- `components/leader-dialogue-runtime.test.tsx`: missing runtime bridge module.
- `index.test.tsx`: no `Talk to owl leader` control.
- `contribution.test.tsx`: `openLeaderFullChat is not a function`.

Each focused test passed after the minimal implementation was added.

## Verification receipts

Completed after integration:

```text
npm run test:ui --workspace apps/desktop -- \
  src/app/lunar-city/leader-sessions.test.ts \
  src/app/lunar-city/components/leader-dialogue.test.tsx \
  src/app/lunar-city/leader-runtime.test.ts \
  src/app/lunar-city/components/leader-dialogue-runtime.test.tsx \
  src/app/lunar-city/contribution.test.tsx \
  src/app/lunar-city/index.test.tsx \
  src/app/lunar-city/world/create-world.test.ts \
  src/app/chat/composer/hooks/use-voice-conversation.test.tsx
```

Final result: 8 test files, 89 passing tests.

- Exact Task 9B ESLint: passed with `--max-warnings=0`.
- Exact Task 9B Prettier check: passed.
- Scoped Task 9B TypeScript check: passed with a temporary config containing
  the seven production source files plus the Desktop declaration files; the
  temporary config was deleted immediately after the check.
- Desktop production build: passed (`vite`, Electron main/preload bundle, and
  staged native dependencies). It was a dirty-worktree build at pre-Task9B
  commit `426250270f8f`; it is build evidence, not a clean packaged receipt.
- Full desktop typecheck passed: renderer, Electron, and E2E TypeScript
  projects all completed after the concurrent Task 10 surface settled.

These receipts are deterministic unit/build evidence. They do not establish a
live Hermes profile/session/voice interaction or supervised Task 12 acceptance.

## Fix round 1 — scheduler continuation and owner-pinned transcription

The review findings are resolved without adding a second animation loop or a
private voice protocol.

- The Task 6 scheduler now treats an actual active leader GLB group's
  `isPlaying` state as frame demand. Listening, thinking, and talking use the
  existing throttled scheduler; idle stops the active group and parks after a
  final dirty render. A finite group clears its demand when Babylon reports
  completion. World disposal stops every active leader group before disposing
  the scheduler, so queued callbacks cannot render late.
- Voice configuration, provider-direct STT, and relay STT accept an explicit
  `{ connectionId, profile }` scope. Leader dialogue captures a frozen exact
  owner scope and an abortable generation before transcription begins. A
  leader/session/availability change aborts or rejects the old capture; every
  post-await boundary verifies that generation before audio can continue to a
  relay or return a transcript. Text remains on the existing exact-owner
  session route throughout.

### Fix-round TDD evidence

The following RED failures were observed before the corresponding production
changes:

- `world/create-world.test.ts`: listening, thinking, and talking each rendered
  only one frame instead of continuing while their active GLB group played;
  disposal also failed to stop the active group.
- `lib/voice-client-direct.test.ts`: an explicit leader owner still fetched
  voice configuration from the newly ambient connection/profile.
- `hermes-profile-scope.test.ts`: relay transcription sent audio to the newly
  ambient connection/profile rather than the leader owner.
- `components/leader-dialogue-runtime.test.tsx`: direct STT was called with
  only the audio blob, without the owner scope or abort signal.

Focused cases subsequently passed, including a same-runtime-id switch from
`source-a/owl` to `source-b/fox` while direct or relay transcription was in
flight. Stale completions are rejected, no relay begins after a stale direct
completion, and text sends to the new exact owner.

### Fix-round verification receipts

```text
npm run test:ui --workspace apps/desktop -- \
  src/app/lunar-city/leader-sessions.test.ts \
  src/app/lunar-city/components/leader-dialogue.test.tsx \
  src/app/lunar-city/leader-runtime.test.ts \
  src/app/lunar-city/components/leader-dialogue-runtime.test.tsx \
  src/app/lunar-city/index.test.tsx \
  src/app/lunar-city/world/create-world.test.ts \
  src/app/lunar-city/world/scheduler.test.ts \
  src/app/chat/composer/hooks/use-voice-conversation.test.tsx \
  src/app/chat/composer/hooks/use-voice-conversation-rearm.test.tsx \
  src/lib/voice-client-direct.test.ts \
  src/hermes-profile-scope.test.ts
```

- The preceding focused command passed 11 files / 130 tests. The later finite
  group scheduler case also passed in `world/create-world.test.ts` (21 tests).
- Full Desktop typecheck passed: renderer, Electron, and E2E TypeScript
  projects.
- Full Desktop ESLint passed with `--max-warnings=0`; the exact Task 9B
  Prettier check passed. The broad formatter check still reports concurrent
  Task 10 WIP in `command-broker.test.ts`, which this task did not modify.
- Desktop production build passed at dirty checkout `438faec7098d`; it remains
  build evidence only, not clean packaged acceptance.

## Fix round 2 — finite clips and synchronous owner-change revocation

The second review round keeps all leader animation demand inside the existing
Task 6 scheduler and makes owner/session capture revocation synchronous with
the React commit.

- The world has an explicit playback policy: `listening`, `thinking`, and
  `talking` start looping; finite states, including `acknowledging`, start
  non-looping. The existing scheduler continues only while Babylon reports an
  active group and parks after natural finite completion, idle, or disposal.
- The world fake now follows the requested loop flag. Acknowledging completes
  over multiple rendered frames without a test mutating `isPlaying` itself.
- Each leader voice capture holds a frozen owner/session route token with a
  unique generation. Route cleanup moved from a passive effect to the layout
  commit phase, aborting the old capture before a parent can observe a newly
  committed owner/session. Every direct and relay await boundary verifies that
  immutable token and the active capture identity; stale audio cannot begin a
  relay or return text under a replacement owner.
- The client-direct configuration in-flight cleanup now uses a pre-created
  symbol token instead of closing over a not-yet-initialized promise.

### Fix-round-2 TDD evidence

Observed RED before the corresponding minimal changes:

- The finite acknowledging regression expected `start(false)` but the prior
  playback path called `start(true)`.
- Direct and relay owner-switch layout tests observed the old abort signal as
  `false`; a passive effect had not run yet. They now observe `true`, reject
  the stale completion, and prove the direct path never starts relay STT.

### Fix-round-2 verification receipts

```text
npm run test:ui --workspace apps/desktop -- \
  src/app/lunar-city/leader-sessions.test.ts \
  src/app/lunar-city/components/leader-dialogue.test.tsx \
  src/app/lunar-city/leader-runtime.test.ts \
  src/app/lunar-city/components/leader-dialogue-runtime.test.tsx \
  src/app/lunar-city/contribution.test.tsx \
  src/app/lunar-city/index.test.tsx \
  src/app/lunar-city/world/create-world.test.ts \
  src/app/lunar-city/world/scheduler.test.ts \
  src/app/lunar-city/world/world-scene.test.ts \
  src/app/chat/composer/hooks/use-voice-conversation.test.tsx \
  src/app/chat/composer/hooks/use-voice-conversation-rearm.test.tsx \
  src/lib/voice-client-direct.test.ts \
  src/hermes-profile-scope.test.ts
```

Result: **13 files / 144 tests passed**. Exact Task 9 files pass ESLint with
`--max-warnings=0`, Prettier check, and `git diff --check`.

`npm run build --workspace apps/desktop` also passed at dirty checkout
`b07f1edd5b28`; this is build-only evidence, not a clean packaged or live
acceptance receipt. Full desktop typecheck and full lint were attempted but
are blocked by concurrent Task 10/11C WIP outside this task (notably
untracked `adapters/bot-roster-details.ts` type errors and Electron lint
errors); no Task 9 error remained in those commands.
