# Lunar City generated-asset pipeline

The approved Lunar City images remain the visual source of truth. Generated
meshes are review candidates, not runtime assets. A candidate is kept outside
`apps/desktop/public/lunar-city`, imported into Blender only after its manifest
path and SHA-256 match, and never promoted into the shipped GLBs by the staging
script.

## Candidate record

Start from `docs/lunar-city-generated-candidates.v1.example.json`. Each record
must identify:

- the target building in `world-manifest.v2.json`;
- every source image and its digest;
- the generator repository and exact model/weights label;
- the artifact format, relative path, and digest;
- artifact and license review states;
- a normalization envelope and explicit `hull`, `avoidance`, and `touch`
  constraints.

Stage a quarantined set with:

```text
apps/desktop/scripts/lunar-city/run-blender-stage.sh \
  --generated-candidate-dir /tmp/lunar-city-generated-candidates \
  --candidate-manifest /tmp/lunar-city-generated-candidates/generated-candidates.v1.json \
  --output /tmp/lunar-city-generated-review.blend \
  --render-output /tmp/lunar-city-generated-review.png
```

The result contains `LUNAR_CITY::GENERATED_CANDIDATES` and
`LUNAR_CITY::GENERATED_CANDIDATE_GUIDES`. The guide is a hidden-from-render
wire cage carrying the candidate's constraint metadata. The staging receipt
records source, generator, license state, normalization, and verified digest.

## Generator lanes

These projects are adapters or reference tools, not dependencies of Hermes:

| Lane | Use | Boundary |
| --- | --- | --- |
| [StableGen](https://github.com/sakalond/StableGen) | Blender-side image/prompt-to-3D, PBR texturing, and ComfyUI orchestration | Host addon only; the repository is GPL-3.0 and each model/weight still needs its own review. |
| [Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2) | First local experiment for image-to-shape/texture and Blender review | Keep its addon/runtime and weights outside this repository until the exact release terms are recorded. |
| [Stable Fast 3D](https://github.com/Stability-AI/stable-fast-3d) | Fast single-image mesh and UV/material candidate generation | Apple Silicon/MPS is experimental; treat local output as diagnostic until visual and topology review. |
| [InstantMesh](https://github.com/tencentarc/instantmesh) | GPU-oriented single-image candidate generation | Apache-2.0 code does not settle model/weight or source-image rights; keep the CUDA environment external. |
| [TRELLIS.2](https://github.com/microsoft/trellis.2) | High-fidelity PBR and open-surface generation on a remote NVIDIA lane | Linux/NVIDIA, large VRAM requirement; do not make it a Mac or Hermes runtime dependency. |
| [Arbor](https://github.com/Stability-AI/arbor) | Constraint-guided generation using hull/avoidance/touch geometry | External Linux/NVIDIA inference; record Stability and third-party model terms per output. |
| [Pixal3D](https://github.com/TencentARC/Pixal3D) | Multi-view/pixel-aligned refinement when a single image is ambiguous | External TRELLIS.2-based lane; keep its dependency stack and notices with the candidate receipt. |
| [3DTopia](https://github.com/3DTopia) | Research/model family for high-quality PBR asset generation | The supplied URL is an organization page; select a concrete repository and verify its license before use. |

This routing is deliberately operational rather than a ranking. The projects
have different hardware, model, and licensing boundaries; no one lane is
automatically accepted. A candidate becomes runtime-eligible only after an
independent visual comparison to both approved references, topology/clearance
checks, performance budget validation, and a human license decision.

## Review checklist

1. Preserve the approved warm regolith, cyan/violet/amber signals, open-front
   interiors, raised walkways, leaders, and robot-child scale.
2. Check that the generated shell conforms to the target footprint and does
   not intersect roads, neighboring buildings, or the concave world surface.
3. Check mesh normals, non-manifold regions, UVs, material count, draw calls,
   LODs, and animation compatibility before considering promotion.
4. Keep `artifactStatus: "candidate"` or `"review"` until those checks pass.
   `licenseStatus: "cleared"` is required in addition to visual approval.
5. Promote only through a separate, auditable runtime asset change; the
   Blender staging importer has no promotion path by design.
