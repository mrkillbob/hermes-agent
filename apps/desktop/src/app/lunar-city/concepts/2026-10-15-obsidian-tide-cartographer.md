# Lunar City concept — Obsidian Tide

**Original art direction:** deep obsidian-and-teal; tidal glass architecture with flowing water channels carved into volcanic stone; bioluminescent coral accents along canal walls; low horizontal buildings that hug the shoreline.

**Palette:** `#1A2A3A` obsidian water, `#0D1B2A` deep tide, `#2E8B8B` teal glow, `#E8D5C4` coral cream, `#0A0A0F` void black.

**Mood:** quiet mystery, patient exploration, maps drawn in water and light.

The original brief named a Cartographer. Treat that figure as a visual idea, not a supported NPC class or a runtime asset. Until an asset owner supplies a manifest-backed character and kit, keep the character concept parked; use existing manifest-backed worker or leader silhouettes for exploratory compositions.

## Current presentation contract

An event's `kind` selects a presentation `sceneTag`; scene and NPC `animationTags` are display-only semantics, and `cosmetic.intensity` is a display value from 0 to 3. These tags do not promise an animation clip. The generic scene for kinds without an explicit `SCENES` entry is `alert.unclassified`, with `alert` and intensity 1. See [world-presentation.ts](../world-presentation.ts), [world-events.ts](../world-events.ts), and [world-animation.ts](../world-animation.ts).

For the four presently generic event kinds, the `worker.stale` condition, and their exploratory frames, see [Event concept frames](./event-concept-frames.md). This document changes no event or scene behavior.

## Asset and extension path

The actual asset and clip inventory is `LUNAR_CITY_ASSET_MANIFEST` in [world-assets.ts](../world-assets.ts); its declared worker, leader, dispatcher and child kinds and animation clips are the current kit. A future asset owner can extend the manifest and corresponding source kit, bindings, validation and artwork together. A scene name or display tag here does not create an asset, NPC, clip or runtime mapping.
