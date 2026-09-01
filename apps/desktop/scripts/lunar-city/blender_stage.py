"""Stage Lunar City assets in Blender for authored iteration.

Run from Blender's Python environment (the ``--`` separates Blender flags):

    blender --background --python blender_stage.py -- \
      --output /tmp/lunar-city-stage.blend \
      --asset-kit-dir /tmp/lunar-city-open-asset-curated

External kit files are optional and are never downloaded or copied by this
script. The directory should contain files that were downloaded and reviewed
using ``import-open-asset-pack.mjs`` or ``curate-open-asset-pack.mjs`` first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy  # type: ignore
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ASSET_ROOT = ROOT / "apps" / "desktop" / "public" / "lunar-city" / "v2" / "models"


def babylon_to_blender_position(point: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert Lunar City's Babylon Y-up coordinates into Blender Z-up."""
    x, y, z = point
    return (x, z, y)


def babylon_to_blender_rotation(rotation: tuple[float, float, float]) -> tuple[float, float, float]:
    """Map authored XYZ rotations, moving Babylon yaw (Y) onto Blender Z."""
    x, y, z = rotation
    return (x, z, y)


def collection(name: str, parent: bpy.types.Collection | None = None):
    existing = bpy.data.collections.get(name)
    if existing:
        return existing
    created = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(created)
    return created


def move_to(obj: bpy.types.Object, target: bpy.types.Collection) -> None:
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    target.objects.link(obj)


def import_model_objects(path: Path, target: bpy.types.Collection) -> list[bpy.types.Object]:
    before = set(bpy.data.objects)
    suffix = path.suffix.lower()
    if suffix == ".glb" or suffix == ".gltf":
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    else:
        return []
    imported = [obj for obj in bpy.data.objects if obj not in before]
    for obj in imported:
        move_to(obj, target)
    return imported


def import_model(path: Path, target: bpy.types.Collection) -> int:
    return len(import_model_objects(path, target))


def activate_overview_lod(imported: list[bpy.types.Object]) -> None:
    """Render only the near representation while keeping other LODs editable."""
    for obj in imported:
        if obj.name.endswith(":lod:near"):
            obj.hide_render = False
            obj["lunarCityOverviewLod"] = "near"
        elif obj.name.endswith(":lod:mid") or obj.name.endswith(":lod:far"):
            obj.hide_render = True
            obj["lunarCityOverviewLod"] = "hidden"


def apply_staging_edge_finish(imported: list[bpy.types.Object]) -> None:
    """Add a restrained bevel to Blender-only meshes for a manufactured read.

    The shipped WebGL GLBs stay untouched and asset-neutral. Blender is the
    artist-facing staging file, so a tiny two-segment bevel is a safe place to
    restore the softened pressure-shell edges visible in the approved art
    without increasing runtime draw calls or requiring a texture pack.
    """
    for obj in imported:
        if obj.type != "MESH" or not obj.data or obj.hide_render:
            continue
        dimensions = [float(value) for value in obj.dimensions if float(value) > 0]
        if not dimensions:
            continue
        width = min(0.12, max(0.025, min(dimensions) * 0.035))
        modifier = obj.modifiers.get("LunarCityEdgeFinish") or obj.modifiers.new("LunarCityEdgeFinish", "BEVEL")
        modifier.width = width
        modifier.segments = 2
        modifier.limit_method = "ANGLE"
        modifier.angle_limit = 0.52


def stage_generated_model(
    path: Path,
    target: bpy.types.Collection,
    location: tuple[float, float, float],
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> int:
    """Import one generated GLB and place its root at the authored world anchor."""
    imported = import_model_objects(path, target)
    if not imported:
        return 0
    activate_overview_lod(imported)
    apply_staging_edge_finish(imported)
    anchor = bpy.data.objects.new(f"LUNAR_CITY::{path.stem.upper()}_ANCHOR", None)
    target.objects.link(anchor)
    anchor.location = babylon_to_blender_position(location)
    anchor.rotation_euler = babylon_to_blender_rotation(rotation)
    anchor.scale = scale
    # glTF imports can contain a conversion root plus nested mesh children.
    # Parent only top-level imported objects so their authored local hierarchy
    # remains intact while the entire district moves as one unit in Blender.
    top_level = [obj for obj in imported if obj.parent not in imported]
    for obj in top_level:
        obj.parent = anchor
    anchor["lunarCityAnchor"] = list(location)
    anchor["blenderAnchor"] = list(anchor.location)
    return len(imported)


def manifest_transforms(asset_root: Path) -> dict[str, tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]]:
    """Read authored transforms so Blender and WebGL stage identical worlds."""
    manifest_path = asset_root.parent / "world-manifest.v2.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    transforms = {}
    for model in manifest.get("models", []):
        transform = model.get("transform", {})
        position = tuple(float(value) for value in transform.get("position", (0, 0, 0)))
        rotation = tuple(float(value) for value in transform.get("rotation", (0, 0, 0)))
        scale = tuple(float(value) for value in transform.get("scale", (1, 1, 1)))
        if len(position) == len(rotation) == len(scale) == 3:
            transforms[model.get("id", "")] = (position, rotation, scale)
    return transforms


def import_showcase_model(path: Path, target: bpy.types.Collection, location: tuple[float, float, float], scale: float) -> int:
    """Import one kit model and place it as a visible, editable hero prop.

    The parent empty makes the transform deterministic while preserving the
    source model's own materials and mesh hierarchy. These are staging-only
    objects: the shipped runtime still uses the generated, asset-neutral GLBs.
    """
    before = set(bpy.data.objects)
    imported_count = import_model(path, target)
    imported = [obj for obj in bpy.data.objects if obj not in before]
    if not imported:
        return 0
    # Downloaded kits are authored in mixed unit systems. A blind multiplier
    # can make one FBX/OBJ swallow the entire overview (or leave a prop
    # invisible), which is especially confusing in a headless capture. Keep
    # each benchmark assembly inside a predictable city-scale envelope while
    # preserving the requested scale for already well-sized assets.
    extents = [max(obj.dimensions) for obj in imported if hasattr(obj, "dimensions") and max(obj.dimensions) > 0]
    source_extent = max(extents, default=0.0)
    if source_extent:
        max_extent = 12.0 if scale >= 5.0 else 5.0
        scale = min(scale, max_extent / source_extent)
    anchor = bpy.data.objects.new(f"OPEN_SOURCE::{path.stem}", None)
    target.objects.link(anchor)
    anchor.location = location
    anchor.scale = (scale, scale, scale)
    anchor["sourceExtent"] = source_extent
    anchor["appliedScale"] = scale
    for obj in imported:
        # Keep the imported mesh's authored local transform, but make the
        # collection easy to move as one authored building/prop assembly.
        obj.parent = anchor
    return imported_count


def select_showcase_assets(root: Path, limit: int) -> list[Path]:
    """Select a small, varied set so Blender remains responsive on low-power GPUs."""
    candidates = sorted(
        path for path in root.rglob("*") if path.suffix.lower() in {".glb", ".gltf", ".obj", ".fbx"}
    )
    if not candidates:
        return []
    buckets = [
        ("building", "landmark", "garage", "station", "factory", "tower"),
        ("road", "pavement", "sidewalk", "bridge", "floor"),
        ("wall", "roof", "door", "window", "structure"),
        ("rock", "crystal", "tree", "plant", "grass", "nature"),
        ("light", "lamp", "pipe", "container", "crate", "terminal", "computer"),
    ]
    selected: list[Path] = []
    used: set[Path] = set()
    per_bucket = max(1, limit // len(buckets))
    for keywords in buckets:
        matches = [p for p in candidates if any(keyword in p.stem.lower() for keyword in keywords)]
        for path in matches[:per_bucket]:
            if path not in used:
                selected.append(path)
                used.add(path)
    for path in candidates:
        if len(selected) >= limit:
            break
        if path not in used:
            selected.append(path)
            used.add(path)
    return selected[:limit]


def make_material(name: str, color: tuple[float, float, float, float], metallic=0.0, roughness=0.75):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def stage_polyhaven_texture(path: Path, target: bpy.types.Collection) -> int:
    """Create a low-cost preview card for a texture, preserving the source file."""
    try:
        image = bpy.data.images.load(str(path), check_existing=True)
    except RuntimeError:
        return 0
    mat = bpy.data.materials.new(f"PolyHaven::{path.stem}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    if bsdf:
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.92
    bpy.ops.mesh.primitive_plane_add(size=3.0, location=(0, 0, 0.15))
    card = bpy.context.object
    card.name = f"PolyHavenTexture::{path.stem}"
    card.data.materials.append(mat)
    move_to(card, target)
    return 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument(
        "--asset-kit-dir",
        type=Path,
        default=None,
        help="Curated external kit directory for visible Blender-only showcase geometry",
    )
    parser.add_argument("--asset-kit-limit", type=int, default=30)
    parser.add_argument("--polyhaven-dir", type=Path, default=None)
    parser.add_argument(
        "--render-engine",
        choices=("auto", "workbench", "eevee"),
        default="auto",
        help="Render engine; auto uses Workbench to avoid Eevee render-time GPU work, eevee is opt-in",
    )
    parser.add_argument(
        "--render-output",
        type=Path,
        default=None,
        help="Optionally render the staged scene to a PNG (or Blender-supported image path)",
    )
    parser.add_argument("--output", type=Path, default=Path("/tmp/lunar-city-stage.blend"))
    parser.add_argument("--no-reset", action="store_true", help="Keep the current Blender file when running from the Python console")
    # Blender keeps its own flags in sys.argv. Only consume arguments after
    # the conventional ``--`` separator; this also makes console execution
    # safe when Blender was launched with unrelated options.
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if not args.no_reset:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    root = collection("LUNAR_CITY")
    collection("LUNAR_CITY::BUILDINGS", root)
    collection("LUNAR_CITY::WORKERS", root)
    collection("LUNAR_CITY::TERRAIN", root)
    external = collection("POLYHAVEN_BENCHMARK", root)
    kit_collection = collection("LUNAR_CITY::OPEN_SOURCE_BENCHMARK", root)

    palette = {
        "LUNAR_CITY::PALETTE::MOON": make_material("Lunar Moon", (0.09, 0.12, 0.18, 1), metallic=0.15),
        "LUNAR_CITY::PALETTE::TRIM": make_material("Lunar Trim", (0.22, 0.30, 0.38, 1), metallic=0.65),
        "LUNAR_CITY::PALETTE::CYAN": make_material("Lunar Cyan", (0.03, 0.45, 0.62, 1), metallic=0.35),
        "LUNAR_CITY::PALETTE::VIOLET": make_material("Lunar Violet", (0.35, 0.08, 0.55, 1), metallic=0.2),
        "LUNAR_CITY::PALETTE::WARM": make_material("Lunar Warm", (0.72, 0.30, 0.08, 1), metallic=0.2),
    }
    for name, mat in palette.items():
        mat["lunarCityRole"] = name.rsplit("::", 1)[-1].lower()

    counts = {"models": 0, "openSourceShowcaseModels": 0, "polyhavenModels": 0, "polyhavenTextures": 0}
    authored_transforms = manifest_transforms(args.asset_root)
    for path in sorted(args.asset_root.rglob("*")):
        if path.suffix.lower() not in {".glb", ".gltf", ".obj", ".fbx"}:
            continue
        stem = path.stem.lower()
        target_name = "LUNAR_CITY::WORKERS" if stem in {"workers", "leaders"} else "LUNAR_CITY::TERRAIN" if stem in {"terrain", "navigation"} else "LUNAR_CITY::BUILDINGS"
        location, rotation, scale = authored_transforms.get(
            stem,
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        )
        counts["models"] += stage_generated_model(path, bpy.data.collections[target_name], location, rotation, scale)

    # A curated external kit is intentionally visible in the Blender staging
    # file. This is the missing bridge between the asset-neutral runtime and
    # the richer reference silhouette: authored buildings, road pieces, walls,
    # rocks, lights, and props are arranged around the generated settlement.
    showcase_receipt = []
    if args.asset_kit_dir and args.asset_kit_dir.exists():
        showcase_assets = select_showcase_assets(args.asset_kit_dir, max(1, args.asset_kit_limit))
        showcase_positions = [
            (-27, -21, 3.2), (-13, -24, 2.6), (2, -24, 2.8), (18, -22, 3.0), (31, -16, 2.8),
            (-34, -7, 2.3), (-18, -9, 2.1), (17, -9, 2.4), (34, -4, 2.6),
            (-31, 10, 2.2), (-14, 12, 2.0), (15, 12, 2.2), (31, 13, 2.5),
            (-23, 25, 2.4), (-5, 25, 2.3), (14, 24, 2.6), (29, 24, 2.4),
        ]
        for index, path in enumerate(showcase_assets):
            location = showcase_positions[index % len(showcase_positions)]
            stem = path.stem.lower()
            # Kenney pieces are authored in a compact unit scale. Landmark
            # buildings get a little more presence; micro-props stay readable
            # without dominating the city silhouette.
            scale = 6.0 if any(token in stem for token in ("building", "factory", "station", "garage", "tower")) else 3.0 if any(token in stem for token in ("wall", "roof", "structure", "door", "window")) else 2.2
            count = import_showcase_model(path, kit_collection, location, scale)
            counts["openSourceShowcaseModels"] += count
            showcase_receipt.append({"file": str(path), "sha256": sha256(path), "stagingLocation": location, "scale": scale, "licenseReviewRequired": True})

    receipt = []
    if args.polyhaven_dir and args.polyhaven_dir.exists():
        for path in sorted(args.polyhaven_dir.rglob("*")):
            if not path.is_file():
                continue
            digest = sha256(path)
            receipt.append({"file": str(path), "sha256": digest, "license": "CC0 (verify source receipt)", "source": "Poly Haven"})
            if path.suffix.lower() in {".glb", ".gltf", ".obj", ".fbx"}:
                counts["polyhavenModels"] += import_model(path, external)
            elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".exr", ".hdr", ".tif", ".tiff"}:
                counts["polyhavenTextures"] += stage_polyhaven_texture(path, external)

    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("LunarCityWorld")
    # Workbench uses the world's display color instead of shader nodes for
    # its low-cost render path. Keep the same deep-space tone in both modes.
    scene.world.color = (0.004, 0.008, 0.025)
    scene["lunarCityStaging"] = "asset-neutral"
    scene["openSourceShowcase"] = "visible benchmark geometry; not shipped"
    scene["openSourceReviewRequired"] = bool(showcase_receipt)
    scene["polyhavenReviewRequired"] = bool(receipt)
    # Workbench Next is deterministic, low-GPU, and still shows authored
    # material colors, studio lighting, shadows, and cavity shading. Keep
    # ``auto`` on this safe path in all launch modes; Eevee is an explicit
    # opt-in for a machine with a verified Metal driver. Both engines still
    # require Blender to initialize its macOS Metal backend before Python
    # starts, so the launcher supplies ``--gpu-backend metal`` and a host
    # permission is required when the desktop sandbox blocks Metal startup.
    render_engines = {item.identifier for item in scene.bl_rna.properties["render"].fixed_type.properties["engine"].enum_items}
    # Blender 5.2 builds expose this engine as either BLENDER_EEVEE or
    # BLENDER_EEVEE_NEXT depending on the platform build. Prefer whichever is
    # present so the authored textures and PBR materials are visible; fall
    # back to Workbench only when Eevee is genuinely unavailable.
    eevee_engine = next((identifier for identifier in render_engines if identifier.startswith("BLENDER_EEVEE")), None)
    if args.render_engine in {"auto", "workbench"}:
        scene.render.engine = "BLENDER_WORKBENCH"
    elif args.render_engine == "eevee" and eevee_engine:
        scene.render.engine = eevee_engine
    else:
        scene.render.engine = eevee_engine or "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 70
    if scene.render.engine.startswith("BLENDER_WORKBENCH"):
        shading = scene.display.shading
        shading.light = "STUDIO"
        # MATERIAL keeps headless captures deterministic when an optional
        # benchmark kit references textures that are not present beside the
        # downloaded mesh. Generated GLBs still use their authored material
        # colors, while Blender-only kit previews avoid magenta placeholders
        # and GPU texture allocation failures.
        shading.color_type = "MATERIAL"
        shading.background_type = "WORLD"
        shading.show_shadows = True
        shading.show_cavity = True
        shading.cavity_type = "BOTH"
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background:
        background.inputs["Color"].default_value = (0.005, 0.008, 0.02, 1)
        background.inputs["Strength"].default_value = 0.22

    # Create datablocks directly rather than relying on bpy.ops context. This
    # works from the interactive console, the Text Editor, and --background.
    camera_data = bpy.data.cameras.new("LunarCity_StagingCamera")
    camera = bpy.data.objects.new("LunarCity_StagingCamera", camera_data)
    scene.collection.objects.link(camera)
    # Use a wide, orthographic isometric view for Blender staging.  This keeps
    # every district and the transit ring in frame while preserving the
    # approved SimCity-style overview; artists can switch to perspective in
    # Blender for close-up work without changing the authored world.
    camera.location = (70.0, -70.0, 70.0)
    camera.name = "LunarCity_StagingCamera"
    scene.camera = camera
    camera.data.lens = 48
    # Aim the staging camera at the settlement instead of leaving Blender's
    # default -Z orientation, which otherwise frames empty space. This keeps
    # the imported scene immediately useful for visual iteration.
    camera_target = Vector(babylon_to_blender_position((0.0, 2.5, 7.0)))
    camera.rotation_euler = (camera_target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 104.0
    def add_area(name: str, location: tuple[float, float, float], energy: float, size: float):
        light_data = bpy.data.lights.new(name, type="AREA")
        light_data.energy = energy
        light_data.shape = "DISK"
        light_data.size = size
        light = bpy.data.objects.new(name, light_data)
        scene.collection.objects.link(light)
        light.location = location
        light.rotation_euler = (camera_target - light.location).to_track_quat("-Z", "Y").to_euler()
        return light

    add_area("LunarCity_KeyLight", (12, -18, 46), 1800, 40)
    add_area("LunarCity_FillLight", (-38, 20, 26), 950, 48)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output.with_suffix(".staging-receipt.json")
    receipt_path.write_text(json.dumps({"assetRoot": str(args.asset_root), "assetKitDir": str(args.asset_kit_dir) if args.asset_kit_dir else None, "counts": counts, "openSourceShowcase": showcase_receipt, "polyhaven": receipt, "reviewRequired": bool(receipt) or bool(showcase_receipt)}, indent=2) + "\n")
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))
    if args.render_output:
        # Render after saving so a driver crash cannot leave an apparently
        # valid .blend without its staging receipt. Workbench is the default
        # for this path and avoids the headless Metal/Eevee crash class.
        args.render_output.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(args.render_output)
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
    print(json.dumps({"blend": str(args.output), "receipt": str(receipt_path), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
