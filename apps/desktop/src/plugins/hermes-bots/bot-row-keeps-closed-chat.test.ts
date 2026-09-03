/** A bot row click consistently lands on the canonical Bot Chat represented
 * by that row. An already-open verified canonical tab is fronted; stale or
 * unverifiable tiles fall through to the authoritative registry open. */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

const { openBotCanonicalChat, prepareBotSource, reconcileBotProfileSessions } = vi.hoisted(() => ({
  openBotCanonicalChat: vi.fn(),
  prepareBotSource: vi.fn(),
  reconcileBotProfileSessions: vi.fn()
}))

vi.mock('./canonical-chat', () => ({
  CANONICAL_CHAT_TITLE: 'Bot Chat',
  ensureBotMetadata: vi.fn(async () => ({})),
  notifyBotOpenFailure: vi.fn(),
  openBotCanonicalChat,
  prepareBotSource,
  PROFILE_SESSION_LIST_LIMIT: 200
}))

vi.mock('./session-sweep', () => ({ reconcileBotProfileSessions }))

const { host } = await import('@hermes/plugin-sdk')
const { $openBotChat, $selectedBot } = await import('./bot-state')
const { openRosterBot } = await import('./roster-actions')

const bot = {
  connectionId: 'local',
  name: 'alpha',
  canonical_session: { id: 'bot-chat', resolved_id: 'bot-chat-tip' }
} as RosterRow

/** Swap in a focus API for one test, restoring whatever the SDK really has —
 *  including its absence, which is the older-shell case. */
function withFocusApi(
  impl: null | ((
    key: string,
    isStaleTile?: (tile: { storedSessionId: string; workspaceTabTitle?: string }) => boolean,
    canonicalIds?: readonly string[]
  ) => null | string)
) {
  const had = Object.hasOwn(host, 'focusOpenWorkspaceSession')
  const original = host.focusOpenWorkspaceSession

  if (impl) {
    host.focusOpenWorkspaceSession = impl
  } else {
    // @ts-expect-error — modelling a Desktop old enough to lack the verb.
    delete host.focusOpenWorkspaceSession
  }

  return () => {
    if (had) {
      host.focusOpenWorkspaceSession = original
    } else {
      // @ts-expect-error — same.
      delete host.focusOpenWorkspaceSession
    }
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  prepareBotSource.mockResolvedValue(undefined)
  reconcileBotProfileSessions.mockResolvedValue(undefined)
  openBotCanonicalChat.mockResolvedValue({ openedId: 'bot-chat', registryId: 'bot-chat' })
  $openBotChat.set(null)
  $selectedBot.set('')
})

describe('a row click returns to the tabs the bot already has open', () => {
  it('fronts the remembered tab and resolves no canonical chat', async () => {
    const focus = vi.fn(() => 'thread-2')
    const restore = withFocusApi(focus)

    try {
      await expect(openRosterBot(bot)).resolves.toBe(true)

      expect(focus).toHaveBeenCalledWith(
        'bot:alpha',
        expect.any(Function),
        ['bot-chat', 'bot-chat-tip']
      )
      expect(openBotCanonicalChat).not.toHaveBeenCalled()
      // Open tabs need no source activation either — the bot is already live.
      expect(prepareBotSource).not.toHaveBeenCalled()
      expect(reconcileBotProfileSessions).toHaveBeenCalledWith(bot)
    } finally {
      restore()
    }
  })

  it('claims the fronted canonical tab with both exact identities', async () => {
    const restore = withFocusApi(() => 'thread-2')

    try {
      await openRosterBot(bot)

      expect($openBotChat.get()).toEqual({
        key: 'local::alpha',
        openedRegistryId: 'bot-chat',
        openedSessionId: 'thread-2'
      })
    } finally {
      restore()
    }
  })
})

describe('the canonical chat still opens when it is what was asked for', () => {
  it('opens it when the bot has nothing open', async () => {
    const restore = withFocusApi(() => null)

    try {
      await expect(openRosterBot(bot)).resolves.toBe(true)

      expect(openBotCanonicalChat).toHaveBeenCalled()
      expect(reconcileBotProfileSessions).toHaveBeenCalledWith(bot)
      expect($openBotChat.get()?.openedRegistryId).toBe('bot-chat')
    } finally {
      restore()
    }
  })

  it('uses the verified canonical open-tab shortcut on the explicit ask', async () => {
    const focus = vi.fn(() => 'thread-2')
    const restore = withFocusApi(focus)

    try {
      await expect(openRosterBot(bot)).resolves.toBe(true)

      expect(focus).toHaveBeenCalled()
      expect($openBotChat.get()?.openedRegistryId).toBe('bot-chat')
    } finally {
      restore()
    }
  })
})

describe('the fronted-tab shortcut reconciles with the canonical registry (#90102)', () => {
  // The stuck shape: a persisted "Bot Chat" tile names a session the
  // registry no longer resolves to (superseded pointer-era row, re-minted
  // canonical chat, stale finished session). The roster click must judge
  // that tile against the server-resolved canonical_session and fall
  // through to the authoritative registry open instead of fronting it.
  const staleBot = {
    connectionId: 'local',
    name: 'alpha',
    canonical_session: { id: 'bot-chat', resolved_id: 'bot-chat-tip' }
  } as RosterRow

  /** The probe openRosterBot hands the focus verb, captured. */
  function captureProbe() {
    let probe: ((tile: { storedSessionId: string; workspaceTabTitle?: string }) => boolean) | undefined

    const focus = vi.fn((_key: string, isStaleTile?: typeof probe) => {
      probe = isStaleTile

      return null
    })

    return { focus, probe: () => probe }
  }

  it('classifies a canonical-titled tile at a foreign id as stale', async () => {
    const { focus, probe } = captureProbe()
    const restore = withFocusApi(focus)

    try {
      await openRosterBot(staleBot)

      const isStale = probe()!
      expect(isStale({ storedSessionId: 'old-finished-session', workspaceTabTitle: 'Bot Chat' })).toBe(true)
    } finally {
      restore()
    }
  })

  it('keeps the tile that matches the registry row or its lineage tip', async () => {
    const { focus, probe } = captureProbe()
    const restore = withFocusApi(focus)

    try {
      await openRosterBot(staleBot)

      const isStale = probe()!
      expect(isStale({ storedSessionId: 'bot-chat', workspaceTabTitle: 'Bot Chat' })).toBe(false)
      expect(isStale({ storedSessionId: 'bot-chat-tip', workspaceTabTitle: 'Bot Chat' })).toBe(false)
    } finally {
      restore()
    }
  })

  it('never judges side-chat tabs — only canonical-titled tiles carry registry identity', async () => {
    const { focus, probe } = captureProbe()
    const restore = withFocusApi(focus)

    try {
      await openRosterBot(staleBot)

      const isStale = probe()!
      expect(isStale({ storedSessionId: 'scratch-thread', workspaceTabTitle: 'Group: writers' })).toBe(false)
      expect(isStale({ storedSessionId: 'scratch-thread' })).toBe(false)
    } finally {
      restore()
    }
  })

  it('an older gateway without canonical_session skips tile judgment', async () => {
    const { focus, probe } = captureProbe()
    const restore = withFocusApi(focus)
    const olderBot = { connectionId: 'local', name: 'alpha' } as RosterRow

    try {
      await openRosterBot(olderBot)

      expect(probe()).toBeUndefined()
      expect(focus).not.toHaveBeenCalled()
      expect(openBotCanonicalChat).toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('falls through to the authoritative canonical open when the stale tile was the only tab', async () => {
    // The store discards the stale tile and reports null; the click must
    // then resolve the registry — the backend-truth path — not give up.
    const restore = withFocusApi(() => null)

    try {
      await expect(openRosterBot(staleBot)).resolves.toBe(true)

      expect(openBotCanonicalChat).toHaveBeenCalled()
      expect($openBotChat.get()?.openedRegistryId).toBe('bot-chat')
    } finally {
      restore()
    }
  })
})

describe('a shell that cannot report open tabs behaves as it did before', () => {
  it('opens the canonical chat when the verb is missing', async () => {
    const restore = withFocusApi(null)

    try {
      await expect(openRosterBot(bot)).resolves.toBe(true)

      expect(openBotCanonicalChat).toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('opens the canonical chat when the verb throws', async () => {
    const restore = withFocusApi(() => {
      throw new Error('no tree yet')
    })

    try {
      await expect(openRosterBot(bot)).resolves.toBe(true)

      expect(openBotCanonicalChat).toHaveBeenCalled()
    } finally {
      restore()
    }
  })
})

describe('the claim records exact canonical identity', () => {
  it('fills the registry id for a fronted canonical tab', async () => {
    const restore = withFocusApi(() => 'thread-2')

    try {
      await openRosterBot(bot)

      expect($openBotChat.get()?.openedRegistryId).toBe('bot-chat')
      expect($openBotChat.get()?.openedSessionId).toBeTruthy()
    } finally {
      restore()
    }
  })

  it('fills the registry id for a real canonical open', async () => {
    const restore = withFocusApi(() => null)

    try {
      await openRosterBot(bot)

      expect($openBotChat.get()?.openedRegistryId).toBeTruthy()
    } finally {
      restore()
    }
  })
})
