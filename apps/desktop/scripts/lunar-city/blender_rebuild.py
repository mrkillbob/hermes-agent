"""Rebuild the Lunar City authoring world from an empty Blender scene.

Existing runtime GLBs are contracts and references only; this script does not
import them. It creates a new authored world with full-district massing,
readable facades, lunar infrastructure, characters, roads, and a quarantined
source gallery. Generated objects are tagged for bake/LOD review before any
runtime promotion.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import bpy  # type: ignore
from mathutils import Vector

from blender_stage import (
    add_concave_world_surface,
    add_skybox,
    babylon_to_blender_position,
    babylon_to_blender_rotation,
    collection,
    configure_scene_contract,
    import_showcase_model,
    load_scene_contract,
    manifest_transforms,
    make_material,
    select_showcase_assets,
    sha256,
)


BLUEPRINTS = {
    "library": (11.0, 7.4, 8.5, "violet", "dome", "KNOWLEDGE COMMONS"),
    "research-lab": (12.5, 8.0, 10.0, "cyan", "dish", "RESEARCH LAB"),
    "depot": (11.0, 8.5, 6.0, "amber", "tanks", "RESOURCE DEPOT"),
    "review-office": (10.5, 7.0, 7.7, "violet", "antenna", "REVIEW OFFICE"),
    "triage": (7.5, 5.5, 5.0, "amber", "antenna", "TRIAGE"),
    "garden": (10.0, 7.5, 4.0, "green", "dome", "GARDEN"),
    "council": (12.0, 8.5, 9.0, "cyan", "dish", "FEDERATION COUNCIL"),
    "arts-studio": (11.0, 7.5, 7.0, "violet", "dome", "ARTS STUDIO"),
    "engineering-workshop": (13.0, 9.0, 7.2, "amber", "tanks", "ENGINEERING GUILD"),
    "release-gatehouse": (9.5, 6.0, 6.0, "cyan", "antenna", "RELEASE GATE"),
    "archive": (10.5, 7.5, 9.2, "violet", "dome", "ARCHIVE"),
}


def materials(texture_path: Path | None) -> dict[str, bpy.types.Material]:
    mats = {
        "shell": make_material("Rebuild::Pressure Shell", (0.18, 0.24, 0.32, 1), metallic=0.42, roughness=0.34),
        "deep": make_material("Rebuild::Deep Structure", (0.025, 0.045, 0.085, 1), metallic=0.25, roughness=0.66),
        "trim": make_material("Rebuild::Ceramic Trim", (0.78, 0.83, 0.86, 1), metallic=0.48, roughness=0.28),
        "glass": make_material("Rebuild::Solar Glass", (0.015, 0.12, 0.2, 1), metallic=0.38, roughness=0.16),
        "rock": make_material("Rebuild::Regolith", (0.32, 0.09, 0.035, 1), metallic=0.04, roughness=0.94),
        "road": make_material("Rebuild::Road Composite", (0.07, 0.09, 0.13, 1), metallic=0.3, roughness=0.72),
        "metal": make_material("Rebuild::Service Metal", (0.36, 0.12, 0.06, 1), metallic=0.65, roughness=0.42),
        "cyan": make_material("Rebuild::Cyan Signal", (0.01, 0.55, 0.92, 1), metallic=0.1, roughness=0.2),
        "violet": make_material("Rebuild::Violet Signal", (0.42, 0.06, 0.92, 1), metallic=0.12, roughness=0.2),
        "amber": make_material("Rebuild::Amber Signal", (0.98, 0.38, 0.025, 1), metallic=0.14, roughness=0.24),
        "green": make_material("Rebuild::Garden Signal", (0.06, 0.68, 0.18, 1), metallic=0.04, roughness=0.38),
        "white": make_material("Rebuild::Bot Shell", (0.72, 0.78, 0.83, 1), metallic=0.32, roughness=0.3),
        "black": make_material("Rebuild::Bot Visor", (0.01, 0.02, 0.035, 1), metallic=0.5, roughness=0.2),
    }
    for key in ("cyan", "violet", "amber", "green"):
        node = mats[key].node_tree.nodes.get("Principled BSDF")
        if node and node.inputs.get("Emission Color"):
            node.inputs["Emission Color"].default_value = mats[key].diffuse_color
            node.inputs["Emission Strength"].default_value = 3.2
    if texture_path and texture_path.exists():
        try:
            image = bpy.data.images.load(str(texture_path), check_existing=True)
            texture_mat = mats["shell"]
            nodes = texture_mat.node_tree.nodes
            links = texture_mat.node_tree.links
            tex = nodes.new("ShaderNodeTexImage")
            tex.name = "RFX_4K_Concrete_Source"
            tex.image = image
            bsdf = nodes.get("Principled BSDF")
            if bsdf:
                links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            texture_mat["lunarCityTextureSource"] = str(texture_path)
            texture_mat["lunarCityTextureResolution"] = "4K source"
        except RuntimeError:
            pass
    for name, material in mats.items():
        material["lunarCityRole"] = "rebuild-authoring-material"
        material["lunarCityRuntimePromotion"] = "bake-to-2K-or-atlas"
    return mats


def tag(obj: bpy.types.Object, role: str = "rebuild-authoring-detail") -> None:
    obj["lunarCityRole"] = role
    obj["lunarCityRuntimeLod"] = "near:20k-40k;mid:5k-12k;far:500-2k"
    obj["lunarCityRuntimePromotion"] = "forbidden-until-bake-review"


def box(target, name, location, dimensions, material, parent=None, bevel=0.12, rotation=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    target.objects.link(obj)
    obj.parent = parent
    obj.location = location
    obj.rotation_euler.z = rotation
    obj.dimensions = dimensions
    obj.data.materials.append(material)
    modifier = obj.modifiers.new("RebuildBevel", "BEVEL")
    modifier.width = bevel
    modifier.segments = 4
    modifier.limit_method = "ANGLE"
    tag(obj)
    return obj


def cylinder(target, name, location, radius, depth, material, parent=None, vertices=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=(0, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    target.objects.link(obj)
    obj.parent = parent
    obj.location = location
    obj.data.materials.append(material)
    modifier = obj.modifiers.new("RebuildBevel", "BEVEL")
    modifier.width = min(0.12, radius * 0.16)
    modifier.segments = 3
    tag(obj)
    return obj


def label(target, anchor, name, text, location, material, scale=0.42):
    bpy.ops.object.text_add(location=(0, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    target.objects.link(obj)
    obj.parent = anchor
    obj.location = location
    obj.rotation_euler.x = math.radians(90)
    obj.data.body = text
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    obj.data.size = scale
    obj.data.extrude = 0.035
    obj.data.bevel_depth = 0.012
    obj.data.materials.append(material)
    tag(obj, "rebuilt-wayfinding-label")
    return obj


def build_building(target, building_id, blueprint, transform, mats):
    position, rotation, scale = transform
    anchor = bpy.data.objects.new(f"REBUILD::{building_id.upper()}_ANCHOR", None)
    target.objects.link(anchor)
    anchor.location = babylon_to_blender_position(position)
    anchor.rotation_euler = babylon_to_blender_rotation(rotation)
    anchor.scale = scale
    anchor["lunarCityRole"] = "rebuilt-authoring-building"
    anchor["lunarCityModelId"] = building_id
    anchor["lunarCitySourceReference"] = "new build from quarantined Selene ISRU, RFX, ModKit, KayKit references"
    anchor["lunarCityRuntimeLods"] = "near:20k-40k;mid:5k-12k;far:500-2k"
    width, depth, height, accent_name, roof, text = blueprint
    accent = mats[accent_name]
    parts = []
    parts.append(box(target, f"REBUILD::{building_id}::PLINTH", (0, 0, 0.42), (width + 1.4, depth + 1.2, 0.84), mats["deep"], anchor, 0.22))
    parts.append(box(target, f"REBUILD::{building_id}::BODY", (0, 0, 1.0 + height * 0.5), (width, depth, height), mats["shell"], anchor, 0.32))
    parts.append(box(target, f"REBUILD::{building_id}::ROOF", (0, 0, height + 1.18), (width + 0.9, depth + 0.9, 0.46), mats["trim"], anchor, 0.14))
    parts.append(box(target, f"REBUILD::{building_id}::FRONT_BAND", (0, -depth * 0.515, 2.0 + height * 0.54), (width * 0.82, 0.18, 0.42), accent, anchor, 0.06))
    for x in (-width * 0.43, width * 0.43):
        for y in (-depth * 0.43, depth * 0.43):
            parts.append(box(target, f"REBUILD::{building_id}::BRACE::{x}:{y}", (x, y, 1.2 + height * 0.5), (0.38, 0.38, height * 0.94), mats["trim"], anchor, 0.08))
    rows = 2 if height >= 6 else 1
    for row in range(rows):
        z = 2.3 + row * min(2.3, height * 0.34)
        for column in range(3):
            x = (column - 1) * width * 0.26
            parts.append(box(target, f"REBUILD::{building_id}::WINDOW::{row}:{column}", (x, -depth * 0.522, z), (width * 0.17, 0.13, 0.82), mats["glass"], anchor, 0.04))
            parts.append(box(target, f"REBUILD::{building_id}::SIGNAL::{row}:{column}", (x, -depth * 0.59, z), (width * 0.06, 0.04, 0.13), accent, anchor, 0.02))
    label(target, anchor, f"REBUILD::{building_id}::LABEL", text, (0, -depth * 0.60, 1.15), accent, 0.33 if len(text) > 15 else 0.42)
    if roof == "dome":
        parts.append(cylinder(target, f"REBUILD::{building_id}::DOME", (0, 0, height + 1.95), min(width, depth) * 0.23, 0.72, accent, anchor, 48))
        parts.append(cylinder(target, f"REBUILD::{building_id}::DOME_RING", (0, 0, height + 1.57), min(width, depth) * 0.34, 0.16, mats["trim"], anchor, 48))
    elif roof == "dish":
        cylinder(target, f"REBUILD::{building_id}::MAST", (0, 0, height + 2.25), 0.18, 2.5, mats["trim"], anchor, 24)
        dish = cylinder(target, f"REBUILD::{building_id}::DISH", (0, 0, height + 3.55), min(width, depth) * 0.25, 0.18, accent, anchor, 48)
        dish.rotation_euler = (math.radians(30), 0, math.radians(-18))
    elif roof == "tanks":
        for index, x in enumerate((-width * 0.22, width * 0.22)):
            cylinder(target, f"REBUILD::{building_id}::TANK::{index}", (x, 0, height + 1.8), min(width, depth) * 0.14, 2.0, mats["metal"], anchor, 32)
            box(target, f"REBUILD::{building_id}::TANK_SIGNAL::{index}", (x, -min(width, depth) * 0.14, height + 1.8), (0.7, 0.08, 0.2), accent, anchor, 0.03)
    else:
        cylinder(target, f"REBUILD::{building_id}::ANTENNA", (0, 0, height + 2.1), 0.14, 2.8, mats["trim"], anchor, 24)
        cylinder(target, f"REBUILD::{building_id}::BEACON", (0, 0, height + 3.65), 0.28, 0.28, accent, anchor, 32)
    return {"id": building_id, "parts": len(parts), "accent": accent_name, "dimensions": [width, depth, height], "runtimePromotion": "bake-and-reduce-only"}


def build_roads(target, transforms, mats):
    center = Vector((0.0, 0.0))
    positions = []
    for building_id, transform in transforms.items():
        if building_id not in BLUEPRINTS:
            continue
        x, y, _ = babylon_to_blender_position(transform[0])
        point = Vector((x, y))
        positions.append(point)
        delta = point - center
        length = delta.length
        if length < 1:
            continue
        midpoint = (point + center) * 0.5
        angle = math.atan2(delta.y, delta.x)
        road = box(target, f"REBUILD::ROAD::{building_id}", (midpoint.x, midpoint.y, 0.08), (length, 3.2, 0.24), mats["road"], None, 0.12, angle)
        road["lunarCityRole"] = "rebuilt-transit-surface"
        box(target, f"REBUILD::ROAD_SIGNAL::{building_id}", (midpoint.x, midpoint.y, 0.23), (length * 0.78, 0.08, 0.035), mats["cyan"], None, 0.02, angle)
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=8.2, depth=0.34, location=(0, 0, 0.22))
    plaza = bpy.context.object
    plaza.name = "REBUILD::CENTRAL_PLAZA"
    for owner in list(plaza.users_collection):
        owner.objects.unlink(plaza)
    target.objects.link(plaza)
    plaza.data.materials.append(mats["road"])
    tag(plaza, "rebuilt-central-plaza")
    bpy.ops.mesh.primitive_torus_add(major_radius=6.2, minor_radius=0.12, major_segments=64, minor_segments=12, location=(0, 0, 0.45))
    ring = bpy.context.object
    ring.name = "REBUILD::CENTRAL_SIGNAL_RING"
    for owner in list(ring.users_collection):
        owner.objects.unlink(ring)
    target.objects.link(ring)
    ring.data.materials.append(mats["violet"])
    tag(ring, "rebuilt-wayfinding-ring")
    return len(positions) + 2


def build_terrain(target, mats):
    ground = add_concave_world_surface(target)
    ground.name = "terrain:world-surface:mesh"
    ground.data.materials.clear()
    ground.data.materials.append(mats["rock"])
    tag(ground, "rebuilt-high-poly-terrain")
    for index, (x, y, radius, scale) in enumerate(((-52, -4, 5.0, 1.4), (-39, 39, 4.0, 1.1), (42, -33, 4.5, 1.6), (43, 33, 5.5, 1.2), (0, 52, 4.0, 1.5), (-55, 31, 3.2, 0.9))):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=radius, location=(x, y, 1.0))
        rock = bpy.context.object
        rock.name = f"REBUILD::REGOLITH_BOULDER::{index:02d}"
        rock.scale = (scale, 0.75 * scale, 0.55 * scale)
        for owner in list(rock.users_collection):
            owner.objects.unlink(rock)
        target.objects.link(rock)
        rock.data.materials.append(mats["rock"])
        tag(rock, "rebuilt-terrain-dressing")
    return 7


def build_bot(target, name, location, mats, leader=False, accent="cyan"):
    anchor = bpy.data.objects.new(f"REBUILD::{name.upper()}_ROOT", None)
    target.objects.link(anchor)
    anchor.location = location
    anchor["lunarCityRole"] = "rebuilt-leader" if leader else "rebuilt-worker"
    anchor["lunarCityAnimationHook"] = "leaders" if leader else "workers"
    box(target, f"REBUILD::{name}::BODY", (0, 0, 1.0 if leader else 0.82), (1.15 if leader else 0.9, 0.78 if leader else 0.68, 1.35 if leader else 1.05), mats["white"], anchor, 0.18)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.54 if leader else 0.42, location=(0, 0, 0))
    head = bpy.context.object
    head.name = f"REBUILD::{name}::HEAD"
    for owner in list(head.users_collection):
        owner.objects.unlink(head)
    target.objects.link(head)
    head.parent = anchor
    head.location = (0, -0.02, 2.0 if leader else 1.55)
    head.data.materials.append(mats["white"])
    tag(head, "rebuilt-character-head")
    box(target, f"REBUILD::{name}::VISOR", (0, -0.39, 2.0 if leader else 1.55), (0.54, 0.08, 0.2), mats["black"], anchor, 0.05)
    emblem = cylinder(target, f"REBUILD::{name}::EMBLEM", (0, -0.44, 1.05 if leader else 0.87), 0.16, 0.06, mats[accent], anchor, 24)
    emblem.rotation_euler.x = math.radians(90)
    cylinder(target, f"REBUILD::{name}::ANTENNA", (0, 0, 2.62 if leader else 2.15), 0.08, 0.65, mats[accent], anchor, 20)
    return anchor


def build_characters(target, mats):
    leader_locations = [(-14, -12, 0.5), (17, -15, 0.5), (25, 18, 0.5), (-27, 20, 0.5), (7, 28, 0.5), (-4, 18, 0.5)]
    worker_locations = [(-18, -5, 0.45), (-11, -2, 0.45), (-4, -6, 0.45), (5, -3, 0.45), (12, 2, 0.45), (19, 5, 0.45), (21, -4, 0.45), (-20, 9, 0.45), (-12, 14, 0.45), (-3, 12, 0.45), (6, 9, 0.45), (14, 13, 0.45), (22, 23, 0.45), (-28, -18, 0.45), (-5, -22, 0.45), (13, -26, 0.45)]
    accents = ("violet", "cyan", "amber", "green")
    for index, location in enumerate(leader_locations):
        build_bot(target, f"LEADER_{index:02d}", location, mats, True, accents[index % len(accents)])
    for index, location in enumerate(worker_locations):
        build_bot(target, f"WORKER_{index:02d}", location, mats, False, accents[index % len(accents)])
    return len(leader_locations) + len(worker_locations)


def stage_source_gallery(asset_kit_dir: Path | None, target) -> list[dict]:
    if not asset_kit_dir or not asset_kit_dir.exists():
        return []
    assets = select_showcase_assets(asset_kit_dir, 16)
    receipt = []
    positions = [(-48, 45, 0.5), (-34, 49, 0.5), (-19, 50, 0.5), (-4, 50, 0.5), (12, 49, 0.5), (28, 47, 0.5), (43, 43, 0.5), (50, 28, 0.5), (52, 12, 0.5), (51, -5, 0.5), (49, -20, 0.5), (44, -38, 0.5), (-47, -37, 0.5), (-52, -21, 0.5), (-52, 15, 0.5), (-51, 30, 0.5)]
    for index, path in enumerate(assets):
        location = positions[index % len(positions)]
        count = import_showcase_model(path, target, location, 1.8)
        receipt.append({"file": str(path), "sha256": sha256(path), "location": list(location), "importedObjects": count, "licenseReviewRequired": True})
    target["lunarCityDistribution"] = "quarantine-gallery-only"
    return receipt


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--scene-contract", type=Path, required=True)
    parser.add_argument("--asset-kit-dir", type=Path, default=None)
    parser.add_argument("--rfx-texture", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("/tmp/lunar-city-rebuilt.blend"))
    parser.add_argument("--render-output", type=Path, default=None)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


def main():
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    contract = load_scene_contract(args.scene_contract)
    root = collection("LUNAR_CITY_REBUILT")
    buildings = collection("LUNAR_CITY_REBUILT::BUILDINGS", root)
    workers = collection("LUNAR_CITY_REBUILT::CHARACTERS", root)
    terrain = collection("LUNAR_CITY_REBUILT::TERRAIN", root)
    source_gallery = collection("LUNAR_CITY_REBUILT::SOURCE_GALLERY", root)
    skybox_collection = collection("LUNAR_CITY_REBUILT::SKYBOX", root)
    add_skybox(skybox_collection)
    mats = materials(args.rfx_texture)
    transforms = manifest_transforms(args.asset_root)
    building_receipt = [build_building(buildings, building_id, blueprint, transforms[building_id], mats) for building_id, blueprint in BLUEPRINTS.items() if building_id in transforms]
    road_count = build_roads(terrain, transforms, mats)
    terrain_count = build_terrain(terrain, mats)
    character_count = build_characters(workers, mats)
    gallery_receipt = stage_source_gallery(args.asset_kit_dir, source_gallery)

    scene = bpy.context.scene
    scene.world = scene.world or bpy.data.worlds.new("RebuiltLunarCityWorld")
    scene.world.use_nodes = True
    scene.world.color = (0.004, 0.008, 0.025)
    background = scene.world.node_tree.nodes.get("Background")
    if background:
        background.inputs["Color"].default_value = (0.006, 0.01, 0.035, 1)
        background.inputs["Strength"].default_value = 0.22
    scene["lunarCityRebuild"] = "from-empty-scene"
    scene["lunarCityExistingModelsUsed"] = False
    scene["lunarCityVisualApproval"] = "new-authoring-pass-required"
    scene["lunarCitySourceRepositories"] = "Selene ISRU; RFX Blender Asset Library; ModKit; KayKit Space Base Bits"
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 70
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "MATERIAL"
    shading.background_type = "WORLD"
    shading.show_shadows = True
    shading.show_cavity = True
    shading.cavity_type = "BOTH"
    camera_data = bpy.data.cameras.new("RebuiltLunarCityCamera")
    camera = bpy.data.objects.new("RebuiltLunarCityCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (82, -86, 86)
    target = Vector((0, 0, 4.5))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 120
    scene.camera = camera
    for name, location, energy, size in (("RebuildKey", (18, -28, 55), 2200, 45), ("RebuildFill", (-45, 22, 30), 1200, 50), ("RebuildRim", (35, 40, 35), 900, 35)):
        data = bpy.data.lights.new(name, type="AREA")
        data.energy = energy
        data.shape = "DISK"
        data.size = size
        light = bpy.data.objects.new(name, data)
        scene.collection.objects.link(light)
        light.location = location
        light.rotation_euler = (target - light.location).to_track_quat("-Z", "Y").to_euler()

    scene_contract_receipt = configure_scene_contract(scene, contract, root, buildings, terrain, skybox_collection, list(buildings.objects), list(terrain.objects))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output.with_suffix(".receipt.json")
    receipt = {
        "status": "authoring-rebuild",
        "sourceAssetRoot": str(args.asset_root),
        "sceneContract": str(args.scene_contract),
        "existingModelsImported": False,
        "counts": {"buildings": len(building_receipt), "buildingParts": sum(item["parts"] for item in building_receipt), "roads": road_count, "terrain": terrain_count, "characters": character_count, "sourceGalleryObjects": sum(item["importedObjects"] for item in gallery_receipt)},
        "buildings": building_receipt,
        "sourceGallery": gallery_receipt,
        "scene": scene_contract_receipt,
        "runtimePromotion": "forbidden-until-art-direction-and-bake-review",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))
    if args.render_output:
        args.render_output.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(args.render_output)
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
    print(json.dumps({"blend": str(args.output), "receipt": str(receipt_path), "counts": receipt["counts"]}, indent=2))


if __name__ == "__main__":
    main()
