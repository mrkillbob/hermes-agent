# Lunar City art-direction reference pass — report

## Why

After the Library bevel/AO-bake pass and the terrain mixed-texture pass
(both this session), the user set the actual visual bar explicitly: as
close to BG3-tier richness as the project can afford, but running and
building like SC2 or BG2 — and pointed at four concrete references to
calibrate against: Ratchet & Clank (Rift Apart), Studio Ghibli
(Spirited Away / My Neighbor Totoro), Arc Raiders, and Dark and Darker.
Their own framing: those games are *ginormous* compared to Lunar City's
twelve districts, and they get there without "just adding triangles and
polygons" — so the same headroom should exist here. This report is the
requested step-back before touching more buildings: what each reference
actually teaches, what transfers to this project's real constraints, and
concrete rules for `palette.mjs` and the bake pipeline going forward.

This project's hard constraint, unchanged by any of this: **zero shipped
textures until this session** (confirmed at the very start of this
session's work — no UVs, no diffuse/normal/roughness maps at all), real-
time Babylon.js WebGL, and a fixed 8-color approved palette
(`APPROVED_PALETTE` in `palette.mjs`). The Library and terrain passes
just built the first working baked-texture pipeline this project has ever
had (`authored.mjs` + `injectBakedDetailTexture` in `export.mjs`). Every
recommendation below assumes that pipeline as the delivery mechanism —
nothing here requires a new architecture, only using the one just built
more deliberately.

## What each reference actually teaches

### Ratchet & Clank: Rift Apart

Saturated complementary lighting (warm key against a magenta/purple rim
and ambient), heavy bloom on emissive surfaces, chunky beveled hard-
surface panels whose edges visibly catch a specular highlight, particle
atmosphere (dust, sparks, bokeh) doing a lot of the "expensive-looking"
work. Environment reads as *stylized*, not photoreal — flat-ish base
colors, but with real edge definition and glow carrying the richness.

**Transfers directly:** this project's lighting rig already has a rim
light and a `GlowLayer` (from the 2026-08-31 lighting pass) and now has
real bevel geometry (Library, terrain, this session). The lever not yet
pulled: **per-zone color grading**. Right now every district uses the
same key/rim/fill regardless of zone. R&C's environments shift their
dominant accent hue scene-to-scene (industrial bays run cyan/orange,
alien overgrowth runs magenta/green). This project's zones from the city-
planning pass (`terrain.mjs`'s `DISTRICTS`) already have an implicit
accent-per-zone: civic buildings lean `archive-emissive` (violet), the
pipeline zone leans `signal-emissive` (cyan), care leans `garden-green`/
`triage-amber`. That's real design language already in the approved
palette — it just isn't being read as a *system* yet. Worth stating as an
explicit rule (below) rather than leaving it as thirteen independent
choices.

**Doesn't transfer:** R&C is a AAA-budget real-time renderer with full
deferred lighting, SSR, and per-pixel material response this WebGL
pipeline doesn't have. Chasing its exact look (not just its *technique*)
would mean rebuilding the renderer, not the asset pipeline.

### Studio Ghibli (Spirited Away)

The bathhouse shot that keeps coming up in searches for this film is the
whole lesson in one image: warm lantern-lit windows read as the entire
focal point against a cool, painterly twilight-to-night sky gradient.
Architecture is tiered/layered (roofline silhouette does a lot of the
"this place has history" work), and the sky is never flat — it's a
gradient with a few soft color bands.

**Transfers directly:** this project already does glowing accent windows
per building (that's literally what `archive-emissive`/`signal-emissive`
window strips already are), and the local-session handoff document
already put a warm-vs-cool-sky decision in place ("the runtime scene
receives an explicit warm-dark space clear color, while the CSS
atmosphere supplies a few procedural stars and an Earth-like orb"). The
gap: the warm/cool *contrast* between lit windows and the sky/shadow
around them isn't pushed nearly as far as Ghibli pushes it. This is a
lighting-value tune, not new geometry or a new texture — cheap to try.

**Doesn't transfer:** Ghibli's backgrounds are hand-painted 2D mattes.
There's no real-time equivalent; the closest honest analog is exactly
what the baked-texture pipeline already does (pixel-resolution detail
baked once, sampled cheaply at runtime), not brush texture.

### Arc Raiders

Muted, earthy, desaturated palette; heavy atmospheric fog/haze; richness
comes from **material weathering** — dirt in crevices, rust bleeding from
seams, moss in corners — not from color saturation or geometry density.
This is the most directly relevant reference for what this project can
actually build next, because it's proof that "weathered surface detail"
reads as expensive even in a muted, low-saturation palette.

**Transfers directly:** this is almost exactly the `injectBakedDetailTexture`
pipeline already built, aimed slightly differently. The Library/terrain
bakes this session multiply pixel-resolution *AO* (and, for terrain, a
patchy color variation) into `baseColorTexture`. Arc Raiders suggests the
next iteration of that same bake should lean into **directional grime**:
darker streaks running from seams/corners rather than uniform patchy
occlusion, mimicking how real dust and rust actually accumulate (along
edges and in shadowed crevices, not randomly). Same pipeline, same
budget, different Voronoi/noise composition in the Blender bake graph.

**Doesn't transfer:** Arc Raiders' materials respond to light via real
roughness/normal maps (that's what makes rust and dirt read as physically
present, not painted-on). This pipeline currently only injects
`baseColorTexture` — no normal or roughness channel. Adding those would
be a genuinely new pipeline extension (`injectBakedDetailTexture` would
need a second/third texture slot, `materialSlots` budgets would need a
texture-count review), not a rerun of the existing technique. Worth
flagging as a real future option, not something to casually add mid-
building-pass.

### Dark and Darker

Extreme light/shadow contrast: small, warm torch-lit pools surrounded by
near-total darkness. This isn't a brightness problem to fix — it's the
entire point of the genre's atmosphere.

**The important finding this reference produces:** this project's own
`render-webgl-preview.mjs` lighting rig is *already* quite dark by design
(`contrast: 1.34`, heavy vignette, dark clear color) — confirmed
empirically this session while debugging the Library AO bake, where most
visible surfaces measured 15–35/255 even before any AO was applied. At
the time that felt like a problem to fight around (raising bake lift
values to compensate). Dark and Darker reframes it: **a mostly-dark scene
with a few crisp, bright, intentional light pools is a legitimate,
established style**, not a bug to brighten away. The actual bar to hit
isn't "make the baked detail visible in a flat, evenly-lit screenshot" —
it's "make the lit focal surfaces (windows, signage, beacons, the
entrance a leader stands near) read crisply, and let genuinely unlit
surfaces stay dark." That's a different, more achievable, and more
correct target than what this session was implicitly chasing.

## Concrete rules going forward

1. **Per-zone accent color is a system, not a per-building choice.**
   civic → `archive-emissive` (violet), pipeline → `signal-emissive`
   (cyan), care → `garden-green`/`triage-amber`, plaza → whichever reads
   as "neutral/transit" (`bone-metal` trim, no strong emissive). When
   building the next specialist, its accent should come from its zone in
   `DISTRICTS`, not be picked independently. This costs nothing — it's a
   convention on top of the palette that already exists.

2. **Bevel + baked detail texture is the delivery mechanism for every
   remaining building**, using the exact `authored.mjs` /
   `injectBakedDetailTexture` pipeline proven on Library and terrain this
   session. Each subsequent building is now much faster: the real bugs
   (Babylon dropping UV for textureless materials, sRGB/lift tuning,
   double-AO from the old vertex-subdivision pass, Blender's
   `area_weight` UV default) are already found and fixed once, not per-
   building.

3. **Bake directional grime, not just uniform AO**, for the next pass —
   darker value concentrated at seams/corners/crevices (Arc Raiders),
   rather than the patchy-but-undirected Voronoi mix used this session.
   Same Blender bake-graph technique, different node composition.

4. **Judge every bake against its own lit focal surfaces, not the whole
   dark scene.** A close-up render of a building's sign, entrance, or
   window cluster is the right test image. A wide, evenly-exposed
   screenshot of the whole dark scene will make good detail work look
   invisible even when it's correct — that's expected (Dark and Darker),
   not a signal to keep raising lift values.

5. **Push the sky/atmosphere warm-cool gradient harder** (Ghibli) — this
   is a lighting/shader-level change with no geometry or texture budget
   cost, and is currently the single cheapest lever available that hasn't
   been pulled yet.

6. **"Ginormous and optimized" scales through texture *reuse*, not more
   unique bakes.** R&C/Arc Raiders-scale worlds stay cheap by tiling a
   shared detail atlas across many similar surfaces, not baking a unique
   texture per object. This project's current one-atlas-per-building
   approach is fine at 12 districts; if the city ever grows well past
   that, the next architectural step is a small set of *shared* tileable
   detail atlases (a "civic wall" atlas, a "pipeline floor" atlas) rather
   than continuing to bake one full atlas per new building. Not urgent
   now — noted so it doesn't need rediscovering later.

## What this report deliberately does not do

No code changed in this pass. This is the reference/analysis step the
user asked for before touching more buildings, so the next building pass
(research-lab, depot, or whichever is picked next) starts from explicit
rules instead of the same ad-hoc, one-off tuning this session did for
Library and terrain.
