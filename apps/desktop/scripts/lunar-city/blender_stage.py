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
import math
import re
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
    apply_staging_palette(imported)
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


STAGING_PALETTE = {
    "archive-emissive": ((0.55, 0.12, 0.95, 1.0), 0.05, 0.28),
    "bone-metal": ((0.86, 0.9, 0.94, 1.0), 0.22, 0.34),
    "charcoal-structure": ((0.055, 0.075, 0.15, 1.0), 0.08, 0.72),
    "garden-green": ((0.14, 0.72, 0.24, 1.0), 0.04, 0.5),
    "lunar-rust": ((0.86, 0.16, 0.045, 1.0), 0.12, 0.48),
    "signal-emissive": ((0.02, 0.82, 0.98, 1.0), 0.08, 0.24),
    "triage-amber": ((0.98, 0.56, 0.08, 1.0), 0.05, 0.4),
}


def apply_staging_palette(imported: list[bpy.types.Object]) -> None:
    """Give imported runtime materials the saturated colony-builder read."""
    for obj in imported:
        if obj.type != "MESH":
            continue
        for material in obj.data.materials:
            if not material:
                continue
            palette_id = material.name.rsplit(".", 1)[0]
            recipe = STAGING_PALETTE.get(palette_id)
            if not recipe:
                continue
            color, metallic, roughness = recipe
            material.diffuse_color = color
            material.use_nodes = True
            material.metallic = metallic
            material.roughness = roughness
            bsdf = material.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = color
                bsdf.inputs["Metallic"].default_value = metallic
                bsdf.inputs["Roughness"].default_value = roughness
                if palette_id in {"archive-emissive", "signal-emissive"}:
                    bsdf.inputs["Emission Color"].default_value = color
                    bsdf.inputs["Emission Strength"].default_value = 1.8


def add_concave_world_surface(target: bpy.types.Collection) -> bpy.types.Object:
    """Add the Blender-only planetary ground surrounding the colony island."""
    radius = 180.0
    center_y = 3.0  # Babylon terrain z=3 becomes Blender y=3.
    center_z = -5.8
    rim_rise = 8.0
    rings = 20
    segments = 96
    vertices = [(0.0, center_y, center_z)]
    faces = []
    for ring in range(1, rings + 1):
        t = ring / rings
        ring_radius = radius * t
        z = center_z + rim_rise * t * t
        for segment in range(segments):
            angle = segment / segments * 2.0 * 3.141592653589793
            vertices.append((ring_radius * math.cos(angle), center_y + ring_radius * math.sin(angle), z))
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((0, 1 + next_segment, 1 + segment))
    for ring in range(1, rings):
        current = 1 + (ring - 1) * segments
        next_ring = current + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            faces.append((current + segment, next_ring + segment, next_ring + next_segment))
            faces.append((current + segment, next_ring + next_segment, current + next_segment))
    mesh = bpy.data.meshes.new("LunarWorldSurfaceMesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    surface = bpy.data.objects.new("LUNAR_CITY::WORLD_SURFACE", mesh)
    target.objects.link(surface)
    regolith = make_material("Lunar World Regolith", (0.26, 0.085, 0.035, 1), metallic=0.05, roughness=0.96)
    bsdf = regolith.node_tree.nodes.get("Principled BSDF")
    if bsdf and bsdf.inputs.get("Emission Color"):
        bsdf.inputs["Emission Color"].default_value = (0.08, 0.018, 0.006, 1)
        bsdf.inputs["Emission Strength"].default_value = 0.12
    surface.data.materials.append(regolith)
    surface["lunarCityRole"] = "planetary-ground"
    surface["lunarCityRadius"] = radius
    surface["lunarCityConcave"] = True
    return surface


def add_skybox(target: bpy.types.Collection) -> tuple[bpy.types.Object, int]:
    """Add a dark interior sphere and deterministic stars for staging renders."""
    sky_material = make_material("Lunar Skybox", (0.008, 0.014, 0.05, 1), metallic=0.0, roughness=1.0)
    sky_material.use_backface_culling = False
    sky_bsdf = sky_material.node_tree.nodes.get("Principled BSDF")
    if sky_bsdf and sky_bsdf.inputs.get("Emission Color"):
        sky_bsdf.inputs["Emission Color"].default_value = (0.006, 0.01, 0.04, 1)
        sky_bsdf.inputs["Emission Strength"].default_value = 0.35
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=500.0, location=(0, 0, 0))
    skybox = bpy.context.object
    skybox.name = "LUNAR_CITY::SKYBOX"
    skybox.data.materials.append(sky_material)
    move_to(skybox, target)
    skybox["lunarCityRole"] = "skybox"
    skybox["lunarCityInterior"] = True
    skybox.hide_select = True
    for polygon in skybox.data.polygons:
        polygon.flip()

    star_material = make_material("Lunar Skybox Stars", (0.62, 0.74, 1.0, 1), metallic=0.0, roughness=0.45)
    star_bsdf = star_material.node_tree.nodes.get("Principled BSDF")
    if star_bsdf and star_bsdf.inputs.get("Emission Color"):
        star_bsdf.inputs["Emission Color"].default_value = (0.55, 0.7, 1.0, 1)
        star_bsdf.inputs["Emission Strength"].default_value = 3.0
    star_count = 48
    for index in range(star_count):
        # A deterministic spherical distribution keeps renders byte-stable
        # while avoiding a hand-authored texture dependency.
        y = 1.0 - 2.0 * (index + 0.5) / star_count
        radial = max(0.0, 1.0 - y * y) ** 0.5
        angle = index * 2.399963229728653
        location = (240.0 * radial * math.cos(angle), 240.0 * radial * math.sin(angle), 240.0 * y)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.8 if index % 5 else 2.6, location=location)
        star = bpy.context.object
        star.name = f"LUNAR_CITY::STAR::{index:02d}"
        star.data.materials.append(star_material)
        move_to(star, target)
        star["lunarCityRole"] = "skybox-star"
    return skybox, star_count


def load_scene_contract(path: Path) -> dict:
    """Load the versioned Blender authoring contract beside the world manifest."""
    try:
        contract = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"unable to read Lunar City scene contract {path}: {error}") from error
    if contract.get("version") != 1 or contract.get("activeClip") != "sky-scene":
        raise RuntimeError(f"unsupported Lunar City scene contract at {path}")
    return contract


def set_keyframe_cycle(id_block, data_path: str, index: int, frame_values: tuple[tuple[int, float], ...]) -> str:
    """Animate one scalar and give its F-curve a reusable cyclic modifier."""
    for frame, value in frame_values:
        id_block.keyframe_insert(data_path=data_path, index=index, frame=frame)
        try:
            getattr(id_block, data_path.split(".")[0])[index] = value
        except (AttributeError, IndexError, TypeError):
            pass
    animation = id_block.animation_data
    action = animation.action if animation else None
    if not action:
        return ""
    action.name = "sky-scene"
    for fcurve in action_fcurves(action):
        if fcurve.data_path == data_path and fcurve.array_index == index and not fcurve.modifiers:
            fcurve.modifiers.new(type="CYCLES")
    return action.name


def action_fcurves(action):
    """Yield F-curves from both legacy and Blender 5.2 layered actions."""
    if not action:
        return
    if hasattr(action, "fcurves"):
        yield from action.fcurves
        return
    for layer in action.layers:
        for strip in layer.strips:
            for slot in action.slots:
                channelbag = strip.channelbag(slot)
                if channelbag:
                    yield from channelbag.fcurves


def add_scene_texture_and_brush(contract: dict) -> dict:
    """Create editable Blender image/texture/brush datablocks for surface work."""
    texture_spec = contract["data"]["texture"]
    image = bpy.data.images.get(texture_spec["name"]) or bpy.data.images.new(texture_spec["name"], width=8, height=2)
    image.generated_color = (*contract["world"]["surface"]["zenithColor"], 1.0)
    texture = bpy.data.textures.get(texture_spec["name"]) or bpy.data.textures.new(texture_spec["name"], type="IMAGE")
    texture.image = image
    brush_name = contract["data"]["brushes"][0]
    brush = bpy.data.brushes.get(brush_name) or bpy.data.brushes.new(brush_name, mode="SCULPT")
    if hasattr(brush, "texture"):
        brush.texture = texture
    image["lunarCityRole"] = "sky-gradient-and-surface-reference"
    texture["lunarCityRole"] = "surface-texture-space"
    brush["lunarCityRole"] = "surface-authoring-brush"
    return {"image": image.name, "texture": texture.name, "brush": brush.name}


def ensure_mesh_data_contract(obj: bpy.types.Object, vertex_group_name: str, shape_key_name: str, remesh_spec: dict | None = None) -> dict:
    """Attach editable geometry data to one terrain/building mesh."""
    if obj.type != "MESH" or not obj.data:
        return {"object": obj.name, "vertices": 0, "edges": 0, "vertexGroup": False, "shapeKey": False, "remesh": False}
    mesh = obj.data
    group = obj.vertex_groups.get(vertex_group_name) or obj.vertex_groups.new(name=vertex_group_name)
    if mesh.vertices:
        group.add([vertex.index for vertex in mesh.vertices], 1.0, "REPLACE")
    basis = obj.shape_key_add(name="Basis") if not obj.data.shape_keys else obj.data.shape_keys.key_blocks.get("Basis")
    shape_key = obj.data.shape_keys.key_blocks.get(shape_key_name) if obj.data.shape_keys else None
    if not shape_key:
        shape_key = obj.shape_key_add(name=shape_key_name)
    shape_key.value = 0.0
    mesh.use_auto_texspace = True
    remesh_name = "LunarCityVoxelRemeshPreview"
    modifier = obj.modifiers.get(remesh_name)
    if not modifier and remesh_spec:
        modifier = obj.modifiers.new(remesh_name, "REMESH")
    remesh_enabled = False
    if modifier and remesh_spec:
        if hasattr(modifier, "mode"):
            modifier.mode = remesh_spec.get("mode", "VOXEL")
        if hasattr(modifier, "voxel_size"):
            modifier.voxel_size = float(remesh_spec.get("voxelSize", 0.18))
        modifier.show_viewport = bool(remesh_spec.get("showViewport", False))
        modifier.show_render = bool(remesh_spec.get("showRender", False))
        remesh_enabled = True
    return {
        "object": obj.name,
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "vertexGroup": group.name,
        "shapeKey": shape_key.name,
        "textureSpace": bool(mesh.use_auto_texspace),
        "remesh": remesh_enabled,
    }


def first_mesh(objects: list[bpy.types.Object], *, exclude_roles: set[str] | None = None) -> bpy.types.Object | None:
    exclude_roles = exclude_roles or set()
    return next(
        (
            obj
            for obj in objects
            if obj.type == "MESH" and obj.data and obj.get("lunarCityRole") not in exclude_roles and len(obj.data.vertices) > 0
        ),
        None,
    )


def configure_scene_contract(
    scene: bpy.types.Scene,
    contract: dict,
    root: bpy.types.Collection,
    buildings_collection: bpy.types.Collection,
    terrain_collection: bpy.types.Collection,
    skybox_collection: bpy.types.Collection,
    building_objects: list[bpy.types.Object],
    terrain_objects: list[bpy.types.Object],
) -> dict:
    """Realize the scene contract as Blender datablocks and return a receipt."""
    start, end = contract["frameRange"]
    scene.frame_start = start
    scene.frame_end = end
    scene.frame_set(start)
    scene["lunarCityActiveClip"] = contract["activeClip"]
    scene["lunarCitySceneContractVersion"] = contract["version"]

    world = scene.world or bpy.data.worlds.new(contract["world"]["name"])
    scene.world = world
    world.name = contract["world"]["name"]
    world.color = (*contract["world"]["background"]["color"],)
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background:
        background.inputs["Color"].default_value = (*contract["world"]["background"]["color"], 1.0)
        background.inputs["Strength"].default_value = contract["world"]["background"]["strength"]
        for frame, color in (
            (start, (*contract["world"]["surface"]["zenithColor"], 1.0)),
            (end, (*contract["world"]["surface"]["horizonColor"], 1.0)),
        ):
            background.inputs["Color"].default_value = color
            background.inputs["Color"].keyframe_insert("default_value", frame=frame)
        world_animation = world.node_tree.animation_data
        if world_animation.action:
            world_animation.action.name = contract["activeClip"]
        if world_animation.action:
            for fcurve in action_fcurves(world_animation.action):
                if not fcurve.modifiers:
                    fcurve.modifiers.new(type="CYCLES")
    world["lunarCityRole"] = "animated-sky-world"
    world["lunarCitySurfaceType"] = contract["world"]["surface"]["type"]

    scene["lunarCityRaySettings"] = json.dumps(contract["world"]["raySettings"], sort_keys=True)
    if hasattr(scene, "cycles"):
        scene.cycles.samples = contract["world"]["raySettings"]["samples"]
        if hasattr(scene.cycles, "max_bounces"):
            scene.cycles.max_bounces = contract["world"]["raySettings"]["maxBounces"]
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = contract["motionBlur"]["enabled"]
    if hasattr(scene.render, "motion_blur_shutter"):
        scene.render.motion_blur_shutter = contract["motionBlur"]["shutter"]
    if hasattr(scene.render, "use_freestyle"):
        scene.render.use_freestyle = contract["lineArt"]["enabled"]
        if scene.render.use_freestyle and scene.view_layers:
            freestyle = scene.view_layers[0].freestyle_settings
            line_set = freestyle.linesets[0] if freestyle.linesets else freestyle.linesets.new("LunarCity::LineSet")
            line_style = bpy.data.linestyles.get("LunarCity::LineStyle") or bpy.data.linestyles.new("LunarCity::LineStyle")
            line_style.color = (0.015, 0.02, 0.04)
            line_style.thickness = 1.2
            line_set.linestyle = line_style
    scene["lunarCityMotionBlur"] = contract["motionBlur"]["shutter"]
    scene["lunarCityLineArt"] = contract["lineArt"]["mode"]

    collision_collection = collection("LUNAR_CITY::COLLISION", root)
    fx_collection = collection("LUNAR_CITY::FX", root)
    instance_collection = collection("LUNAR_CITY::BUILDING_INSTANCES", root)
    collision_collection.hide_viewport = contract["visibility"]["collisionViewport"] is False
    collision_collection.hide_render = True
    instance_collection.hide_render = True
    instance_collection["lunarCityRole"] = "collection-instance-source"
    instance = bpy.data.objects.get("LUNAR_CITY::BUILDING_INSTANCE")
    if not instance:
        instance = bpy.data.objects.new("LUNAR_CITY::BUILDING_INSTANCE", None)
        fx_collection.objects.link(instance)
    instance.instance_type = "COLLECTION"
    instance.instance_collection = buildings_collection
    instance.hide_viewport = True
    instance.hide_render = True
    instance["lunarCityInstanceCount"] = contract["instancing"][0]["count"]
    instance["lunarCitySourceCollection"] = contract["instancing"][0]["source"]

    skybox = bpy.data.objects.get("LUNAR_CITY::SKYBOX")
    if skybox:
        skybox.rotation_euler = (0.0, 0.0, 0.0)
        skybox.keyframe_insert("rotation_euler", frame=start)
        skybox.rotation_euler.z = math.tau
        skybox.keyframe_insert("rotation_euler", frame=end)
        skybox_animation = skybox.animation_data_create()
        if skybox_animation.action:
            skybox_animation.action.name = contract["activeClip"]
        if skybox_animation.action:
            for fcurve in action_fcurves(skybox_animation.action):
                if not fcurve.modifiers:
                    fcurve.modifiers.new(type="CYCLES")
        skybox["lunarCityMotionPath"] = True
        try:
            bpy.ops.object.select_all(action="DESELECT")
            skybox.hide_select = False
            skybox.select_set(True)
            bpy.context.view_layer.objects.active = skybox
            bpy.ops.object.paths_calculate()
            skybox.hide_select = True
        except (RuntimeError, TypeError):
            skybox["lunarCityMotionPath"] = "animation-path"

    collider = bpy.data.objects.get("LUNAR_CITY::GROUND_COLLIDER")
    if not collider:
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=180.0, depth=0.2, location=(0.0, 3.0, -5.8))
        collider = bpy.context.object
        collider.name = "LUNAR_CITY::GROUND_COLLIDER"
        move_to(collider, collision_collection)
    collider.display_type = "WIRE"
    collider["lunarCityRole"] = "passive-ground-collider"
    bpy.context.view_layer.objects.active = collider
    collider.select_set(True)
    try:
        if not collider.rigid_body:
            bpy.ops.rigidbody.object_add()
        collider.rigid_body.type = "PASSIVE"
        collider.rigid_body.collision_shape = "MESH"
    except RuntimeError:
        collider["lunarCityRigidBody"] = "PASSIVE"
    collider.select_set(False)
    collider.hide_viewport = True
    collider.hide_render = True
    guide = bpy.data.objects.get("LUNAR_CITY::TRANSIT_GUIDE")
    if not guide:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 3.0, 0.0))
        guide = bpy.context.object
        guide.name = "LUNAR_CITY::TRANSIT_GUIDE"
        move_to(guide, collision_collection)
    guide.hide_viewport = False
    guide.hide_render = True
    guide.display_type = "WIRE"
    guide["lunarCityRole"] = "passive-transit-guide"
    bpy.context.view_layer.objects.active = guide
    guide.select_set(True)
    try:
        if not guide.rigid_body:
            bpy.ops.rigidbody.object_add()
        guide.rigid_body.type = "PASSIVE"
        guide.rigid_body.collision_shape = "BOX"
    except RuntimeError:
        guide["lunarCityRigidBody"] = "PASSIVE"
    guide.select_set(False)
    guide.hide_viewport = True
    constraint = bpy.data.objects.get(contract["physics"]["constraints"][0]["name"])
    if not constraint:
        constraint = bpy.data.objects.new(contract["physics"]["constraints"][0]["name"], None)
        collision_collection.objects.link(constraint)
    constraint.hide_viewport = False
    constraint.hide_render = True
    constraint["lunarCityConstraintType"] = contract["physics"]["constraints"][0]["type"]
    try:
        bpy.context.view_layer.objects.active = constraint
        constraint.select_set(True)
        bpy.ops.rigidbody.constraint_add()
        constraint.rigid_body_constraint.type = contract["physics"]["constraints"][0]["type"]
        constraint.rigid_body_constraint.object1 = collider
        constraint.rigid_body_constraint.object2 = guide
        constraint.select_set(False)
    except RuntimeError:
        constraint["lunarCityConstraint"] = "FIXED"
    constraint.hide_viewport = True
    rigid_body_world = scene.rigidbody_world
    if rigid_body_world:
        rigid_body_world.substeps_per_frame = contract["physics"]["rigidBodyWorld"]["substeps"]
        rigid_body_world.point_cache.frame_start = start
        rigid_body_world.point_cache.frame_end = end
        scene["lunarCityRigidBodyFrameRate"] = contract["physics"]["rigidBodyWorld"]["frameRate"]

    terrain_mesh = next(
        (obj for obj in terrain_objects if obj.name == "terrain:world-surface:mesh" and obj.type == "MESH"),
        None,
    ) or first_mesh(terrain_objects, exclude_roles={"skybox", "skybox-star"}) or bpy.data.objects.get("LUNAR_CITY::WORLD_SURFACE")
    building_mesh = first_mesh(building_objects)
    geometry_receipt = {}
    if terrain_mesh:
        geometry_receipt["terrain"] = ensure_mesh_data_contract(
            terrain_mesh,
            contract["geometry"]["vertexGroups"][0],
            contract["geometry"]["shapeKeys"][0],
            contract["geometry"]["remesh"],
        )
    if building_mesh:
        geometry_receipt["building"] = ensure_mesh_data_contract(
            building_mesh,
            contract["geometry"]["vertexGroups"][1],
            contract["geometry"]["shapeKeys"][1],
        )
    data_receipt = add_scene_texture_and_brush(contract)
    return {
        "activeClip": contract["activeClip"],
        "frameRange": [start, end],
        "world": world.name,
        "raySettings": contract["world"]["raySettings"],
        "collections": [child.name for child in root.children],
        "instancing": {"object": instance.name, "source": instance.instance_collection.name if instance.instance_collection else None, "count": instance.get("lunarCityInstanceCount", 0)},
        "motionPaths": [skybox.name] if skybox and skybox.get("lunarCityMotionPath") else [],
        "motionBlur": {"enabled": bool(getattr(scene.render, "use_motion_blur", contract["motionBlur"]["enabled"])), "shutter": contract["motionBlur"]["shutter"]},
        "lineArt": {"enabled": bool(getattr(scene.render, "use_freestyle", contract["lineArt"]["enabled"])), "mode": contract["lineArt"]["mode"]},
        "physics": {"rigidBodyWorld": bool(scene.rigidbody_world), "constraints": [constraint.name], "bodies": [collider.name, guide.name]},
        "geometry": geometry_receipt,
        "data": data_receipt,
    }


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


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value, re.IGNORECASE))


def _safe_candidate_artifact(root: Path, raw_path: object) -> Path | None:
    """Resolve a candidate artifact without allowing path traversal or symlinks out."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("/") or normalized in {".", ".."} or normalized.startswith("../") or "/../" in normalized:
        return None
    root = root.resolve(strict=True)
    artifact = (root / Path(normalized)).resolve(strict=True)
    try:
        artifact.relative_to(root)
    except ValueError:
        return None
    return artifact if artifact.is_file() else None


def load_generated_candidate_manifest(path: Path, candidate_root: Path, known_targets: set[str]) -> dict:
    """Validate and hash-lock quarantined image-to-3D outputs before import."""
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"valid": False, "errors": [f"unable to read generated candidate manifest: {error}"]}

    errors: list[str] = []
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": ["manifest must be an object"]}
    if manifest.get("version") != 1:
        errors.append("version must be 1")
    reference = manifest.get("reference")
    if not isinstance(reference, dict):
        errors.append("reference is required")
    else:
        if not isinstance(reference.get("design"), str) or not reference["design"].strip():
            errors.append("reference.design is required")
        if not isinstance(reference.get("images"), list) or not reference["images"]:
            errors.append("reference.images must contain at least one image")
    if not isinstance(manifest.get("candidates"), list):
        errors.append("candidates must be an array")
        return {"valid": False, "errors": errors}

    seen_ids: set[str] = set()
    for index, candidate in enumerate(manifest["candidates"]):
        prefix = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in ("id", "targetModelId"):
            if not isinstance(candidate.get(key), str) or not candidate[key].strip():
                errors.append(f"{prefix}.{key} is required")
        candidate_id = candidate.get("id")
        if isinstance(candidate_id, str):
            if candidate_id in seen_ids:
                errors.append(f"{prefix}.id must be unique")
            seen_ids.add(candidate_id)
        target = candidate.get("targetModelId")
        if isinstance(target, str) and target not in known_targets:
            errors.append(f"{prefix}.targetModelId does not exist in world-manifest.v2.json")
        artifact = candidate.get("artifact")
        if not isinstance(artifact, dict):
            errors.append(f"{prefix}.artifact is required")
        else:
            suffix = str(artifact.get("format", "")).lower()
            if suffix not in {"glb", "gltf", "obj", "fbx"}:
                errors.append(f"{prefix}.artifact.format is invalid")
            if not _is_sha256(artifact.get("sha256")):
                errors.append(f"{prefix}.artifact.sha256 must be a 64-character SHA-256 digest")
            artifact_path = _safe_candidate_artifact(candidate_root, artifact.get("path"))
            if artifact_path is None:
                errors.append(f"{prefix}.artifact.path must resolve to a file inside the candidate directory")
            elif sha256(artifact_path).lower() != str(artifact["sha256"]).lower():
                errors.append(f"{prefix} artifact SHA-256 does not match the manifest")
        source = candidate.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("images"), list) or not source["images"]:
            errors.append(f"{prefix}.source.images must contain at least one image reference")
        else:
            for image_index, image in enumerate(source["images"]):
                image_prefix = f"{prefix}.source.images[{image_index}]"
                if not isinstance(image, dict) or not isinstance(image.get("path"), str) or not image["path"].strip():
                    errors.append(f"{image_prefix}.path is required")
                if not isinstance(image, dict) or not _is_sha256(image.get("sha256")):
                    errors.append(f"{image_prefix}.sha256 must be a 64-character SHA-256 digest")
        if not isinstance(source, dict) or not isinstance(source.get("designReference"), str) or not source["designReference"].strip():
            errors.append(f"{prefix}.source.designReference is required")
        generator = candidate.get("generator")
        if not isinstance(generator, dict):
            errors.append(f"{prefix}.generator is required")
        else:
            for key in ("id", "repository", "model"):
                if not isinstance(generator.get(key), str) or not generator[key].strip():
                    errors.append(f"{prefix}.generator.{key} is required")
        review = candidate.get("review")
        if not isinstance(review, dict):
            errors.append(f"{prefix}.review is required")
        else:
            if review.get("artifactStatus") not in {"candidate", "review", "approved", "rejected"}:
                errors.append(f"{prefix}.review.artifactStatus is invalid")
            if review.get("licenseStatus") not in {"pending", "cleared", "restricted"}:
                errors.append(f"{prefix}.review.licenseStatus is invalid")
        normalization = candidate.get("normalization")
        if not isinstance(normalization, dict) or normalization.get("anchor") != "manifest-transform":
            errors.append(f"{prefix}.normalization.anchor must be manifest-transform")
        elif not isinstance(normalization.get("maxExtent"), (int, float)) or normalization["maxExtent"] <= 0:
            errors.append(f"{prefix}.normalization.maxExtent must be positive")
        constraints = candidate.get("constraints")
        if not isinstance(constraints, dict) or not isinstance(constraints.get("hull"), list) or not constraints["hull"]:
            errors.append(f"{prefix}.constraints.hull must contain at least one boundary")
        for key in ("avoidance", "touch"):
            if not isinstance(constraints, dict) or not isinstance(constraints.get(key), list):
                errors.append(f"{prefix}.constraints.{key} must be an array")

    if errors:
        return {"valid": False, "errors": errors}
    candidates = []
    root = candidate_root.resolve(strict=True)
    for candidate in manifest["candidates"]:
        artifact_path = _safe_candidate_artifact(root, candidate["artifact"]["path"])
        assert artifact_path is not None
        candidates.append({
            **candidate,
            "artifact": {
                **candidate["artifact"],
                "absolutePath": str(artifact_path),
                "sha256Verified": True,
            },
        })
    return {"valid": True, "errors": [], "version": 1, "reference": reference, "candidates": candidates}


def stage_generated_candidate(
    candidate: dict,
    target: bpy.types.Collection,
    guides: bpy.types.Collection,
    transform: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> dict:
    """Stage a hash-locked generated asset as a review-only, non-runtime assembly."""
    path = Path(candidate["artifact"]["absolutePath"])
    imported = import_model_objects(path, target)
    if not imported:
        raise RuntimeError(f"generated candidate {candidate['id']} imported no objects")
    apply_staging_palette(imported)
    activate_overview_lod(imported)
    apply_staging_edge_finish(imported)
    source_extent = max((max(float(value) for value in obj.dimensions) for obj in imported if obj.dimensions.length > 0), default=0.0)
    max_extent = float(candidate["normalization"]["maxExtent"])
    normalization_scale = min(1.0, max_extent / source_extent) if source_extent else 1.0
    location, rotation, scale = transform
    anchor = bpy.data.objects.new(f"LUNAR_CITY::CANDIDATE::{candidate['id']}::ANCHOR", None)
    target.objects.link(anchor)
    anchor.location = babylon_to_blender_position(location)
    anchor.rotation_euler = babylon_to_blender_rotation(rotation)
    anchor.scale = tuple(float(value) * normalization_scale for value in scale)
    top_level = [obj for obj in imported if obj.parent not in imported]
    for obj in top_level:
        obj.parent = anchor
    cage = bpy.data.objects.new(f"LUNAR_CITY::CANDIDATE::{candidate['id']}::WIRE_CAGE", None)
    guides.objects.link(cage)
    bpy.ops.mesh.primitive_cube_add(size=max_extent, location=(0.0, 0.0, 0.0))
    cage_mesh = bpy.context.object
    cage_mesh.name = f"LUNAR_CITY::CANDIDATE::{candidate['id']}::WIRE_CAGE_MESH"
    move_to(cage_mesh, guides)
    cage_mesh.parent = anchor
    cage_mesh.location = (0.0, 0.0, 0.0)
    cage_mesh.display_type = "WIRE"
    cage_mesh.hide_render = True
    for key, values in candidate["constraints"].items():
        cage[key] = json.dumps(values, sort_keys=True)
    cage["lunarCityRole"] = "generated-candidate-constraint-guide"
    anchor["lunarCityRole"] = "generated-candidate-review"
    anchor["lunarCityCandidateId"] = candidate["id"]
    anchor["lunarCityTargetModelId"] = candidate["targetModelId"]
    anchor["lunarCityGenerator"] = candidate["generator"]["id"]
    anchor["lunarCityLicenseStatus"] = candidate["review"]["licenseStatus"]
    anchor["lunarCityArtifactStatus"] = candidate["review"]["artifactStatus"]
    anchor["lunarCityRuntimePromotion"] = "forbidden-until-independent-approval"
    return {
        "id": candidate["id"],
        "targetModelId": candidate["targetModelId"],
        "generator": candidate["generator"],
        "source": candidate["source"],
        "review": candidate["review"],
        "artifact": candidate["artifact"],
        "normalizationScale": normalization_scale,
        "anchor": anchor.name,
        "constraintGuide": cage.name,
        "importedObjects": len(imported),
        "runtimePromotion": "forbidden-until-independent-approval",
    }


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
    parser.add_argument(
        "--generated-candidate-dir",
        type=Path,
        default=None,
        help="Quarantined image-to-3D output directory; never copied into shipped assets",
    )
    parser.add_argument(
        "--candidate-manifest",
        type=Path,
        default=None,
        help="Versioned generated-candidates.v1.json; requires --generated-candidate-dir",
    )
    parser.add_argument("--polyhaven-dir", type=Path, default=None)
    parser.add_argument(
        "--scene-contract",
        type=Path,
        default=None,
        help="Versioned Blender world/authoring contract; defaults beside world-manifest.v2.json",
    )
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
    scene_contract_path = args.scene_contract or args.asset_root.parent / "scene-contract.v1.json"
    scene_contract = load_scene_contract(scene_contract_path)
    if not args.no_reset:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    root = collection("LUNAR_CITY")
    collection("LUNAR_CITY::BUILDINGS", root)
    collection("LUNAR_CITY::WORKERS", root)
    terrain_collection = collection("LUNAR_CITY::TERRAIN", root)
    skybox_collection = collection("LUNAR_CITY::SKYBOX", root)
    _, skybox_star_count = add_skybox(skybox_collection)
    external = collection("POLYHAVEN_BENCHMARK", root)
    kit_collection = collection("LUNAR_CITY::OPEN_SOURCE_BENCHMARK", root)
    candidate_collection = collection("LUNAR_CITY::GENERATED_CANDIDATES", root)
    candidate_guides = collection("LUNAR_CITY::GENERATED_CANDIDATE_GUIDES", root)
    candidate_collection["lunarCityDistribution"] = "quarantine-only"
    candidate_guides["lunarCityDistribution"] = "quarantine-only"

    palette = {
        "LUNAR_CITY::PALETTE::MOON": make_material("Lunar Moon", (0.09, 0.12, 0.18, 1), metallic=0.15),
        "LUNAR_CITY::PALETTE::TRIM": make_material("Lunar Trim", (0.22, 0.30, 0.38, 1), metallic=0.65),
        "LUNAR_CITY::PALETTE::CYAN": make_material("Lunar Cyan", (0.03, 0.45, 0.62, 1), metallic=0.35),
        "LUNAR_CITY::PALETTE::VIOLET": make_material("Lunar Violet", (0.35, 0.08, 0.55, 1), metallic=0.2),
        "LUNAR_CITY::PALETTE::WARM": make_material("Lunar Warm", (0.72, 0.30, 0.08, 1), metallic=0.2),
    }
    for name, mat in palette.items():
        mat["lunarCityRole"] = name.rsplit("::", 1)[-1].lower()

    counts = {"models": 0, "generatedCandidates": 0, "openSourceShowcaseModels": 0, "polyhavenModels": 0, "polyhavenTextures": 0, "skyboxStars": skybox_star_count}
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

    generated_candidate_receipt = []
    if args.candidate_manifest:
        if not args.generated_candidate_dir:
            raise RuntimeError("--candidate-manifest requires --generated-candidate-dir")
        candidate_contract = load_generated_candidate_manifest(args.candidate_manifest, args.generated_candidate_dir, set(authored_transforms))
        if not candidate_contract["valid"]:
            raise RuntimeError("invalid generated candidate manifest:\n- " + "\n- ".join(candidate_contract["errors"]))
        for candidate in candidate_contract["candidates"]:
            if candidate["review"]["artifactStatus"] == "rejected":
                generated_candidate_receipt.append({
                    "id": candidate["id"],
                    "targetModelId": candidate["targetModelId"],
                    "review": candidate["review"],
                    "runtimePromotion": "forbidden-until-independent-approval",
                    "staged": False,
                })
                continue
            transform = authored_transforms[candidate["targetModelId"]]
            generated_candidate_receipt.append(stage_generated_candidate(candidate, candidate_collection, candidate_guides, transform))
            counts["generatedCandidates"] += 1

    # New terrain GLBs carry the same planetary surface used by WebGL. Keep a
    # fallback for older authored files so this staging script remains useful
    # while a local asset folder is being refreshed, but never overlap two
    # copies of the ground in the current scene.
    if not bpy.data.objects.get("terrain:world-surface"):
        add_concave_world_surface(terrain_collection)

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
        shading.background_type = "WORLD"
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

    building_objects = list(bpy.data.collections["LUNAR_CITY::BUILDINGS"].objects)
    terrain_objects = list(bpy.data.collections["LUNAR_CITY::TERRAIN"].objects)
    scene_contract_receipt = configure_scene_contract(
        scene,
        scene_contract,
        root,
        bpy.data.collections["LUNAR_CITY::BUILDINGS"],
        terrain_collection,
        skybox_collection,
        building_objects,
        terrain_objects,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output.with_suffix(".staging-receipt.json")
    receipt_path.write_text(json.dumps({"assetRoot": str(args.asset_root), "sceneContract": str(scene_contract_path), "scene": scene_contract_receipt, "assetKitDir": str(args.asset_kit_dir) if args.asset_kit_dir else None, "generatedCandidateDir": str(args.generated_candidate_dir) if args.generated_candidate_dir else None, "candidateManifest": str(args.candidate_manifest) if args.candidate_manifest else None, "counts": counts, "generatedCandidates": generated_candidate_receipt, "openSourceShowcase": showcase_receipt, "polyhaven": receipt, "reviewRequired": bool(receipt) or bool(showcase_receipt) or bool(generated_candidate_receipt)}, indent=2) + "\n")
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
