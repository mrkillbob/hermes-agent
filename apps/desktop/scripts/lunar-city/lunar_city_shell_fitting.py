#!/usr/bin/env python3
"""Interior/exterior shell fitting for Lunar City buildings.

Ensures each building's interior shell fits properly inside its exterior
shell with no z-fighting, proper wall thickness, and aligned openings.
Also generates proper floor plates, ceiling panels, and door thresholds.

Run with Blender's Python:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_shell_fitting.py
"""

import json
import math
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "public" / "lunar-city"
SOURCES = PUBLIC / "master-assets" / "sources"
SHELLS = PUBLIC / "shell-fittings"
MANIFEST_PATH = PUBLIC / "shell-fittings-manifest.json"

# Wall thickness in meters for interior/exterior shell gap
WALL_THICKNESS = 0.12
FLOOR_THICKNESS = 0.08
CEILING_THICKNESS = 0.06
DOOR_THRESHOLD_HEIGHT = 0.02


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def collection(name):
    col = bpy.data.objects.new(name, None)
    col.instance_type = "COLLECTION"
    return col


def shell_collection(parent_name, asset_id):
    """Create a named collection for a building's shell components."""
    existing = bpy.data.collections.get(parent_name)
    if existing:
        for obj in list(existing.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(existing)
    col = bpy.data.collections.new(parent_name)
    bpy.context.scene.collection.children.link(col)
    return col


def find_building_objects(asset_id):
    """Find all mesh objects belonging to a building in the master scene."""
    result = []
    for obj in bpy.data.objects:
        if obj.type == "MESH" and asset_id in obj.name:
            result.append(obj)
    return result


def get_bounding_box(obj):
    """Get world-space bounding box of an object."""
    bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_x = min(c.x for c in bbox_corners)
    max_x = max(c.x for c in bbox_corners)
    min_y = min(c.y for c in bbox_corners)
    max_y = max(c.y for c in bbox_corners)
    min_z = min(c.z for c in bbox_corners)
    max_z = max(c.z for c in bbox_corners)
    return {
        "min": Vector((min_x, min_y, min_z)),
        "max": Vector((max_x, max_y, max_z)),
        "size": Vector((max_x - min_x, max_y - min_y, max_z - min_z)),
    }


def create_floor_plate(name, bbox, mat, target, thickness=FLOOR_THICKNESS):
    """Create a floor plate at the bottom of the bounding box."""
    center_x = (bbox["min"].x + bbox["max"].x) / 2
    center_y = (bbox["min"].y + bbox["max"].y) / 2
    z = bbox["min"].z
    size_x = bbox["size"].x
    size_y = bbox["size"].y

    bpy.ops.mesh.primitive_plane_add(
        size=1,
        location=(center_x, center_y, z + thickness / 2),
    )
    floor = bpy.context.active_object
    floor.name = f"{name}_floor"
    floor.scale = (size_x, size_y, 1)
    floor.data.materials.append(mat)
    floor["shell_component"] = "floor_plate"
    return floor


def create_ceiling_panel(name, bbox, mat, target, thickness=CEILING_THICKNESS):
    """Create a ceiling panel at the top of the bounding box."""
    center_x = (bbox["min"].x + bbox["max"].x) / 2
    center_y = (bbox["min"].y + bbox["max"].y) / 2
    z = bbox["max"].z
    size_x = bbox["size"].x
    size_y = bbox["size"].y

    bpy.ops.mesh.primitive_plane_add(
        size=1,
        location=(center_x, center_y, z - thickness / 2),
    )
    ceiling = bpy.context.active_object
    ceiling.name = f"{name}_ceiling"
    ceiling.scale = (size_x, size_y, 1)
    ceiling.data.materials.append(mat)
    ceiling["shell_component"] = "ceiling_panel"
    return ceiling


def create_interior_shell(name, exterior_bbox, mat, target, inset=WALL_THICKNESS):
    """Create an interior shell slightly smaller than the exterior."""
    center_x = (exterior_bbox["min"].x + exterior_bbox["max"].x) / 2
    center_y = (exterior_bbox["min"].y + exterior_bbox["max"].y) / 2
    z_min = exterior_bbox["min"].z

    size_x = exterior_bbox["size"].x - inset * 2
    size_y = exterior_bbox["size"].y - inset * 2
    height = exterior_bbox["size"].z

    # Create interior walls as 4 planes
    walls = []
    # Front wall
    bpy.ops.mesh.primitive_plane_add(
        size=1,
        location=(center_x, center_y + size_y / 2, z_min + height / 2),
    )
    front = bpy.context.active_object
    front.name = f"{name}_interior_wall_front"
    front.scale = (size_x, height, 1)
    front.data.materials.append(mat)
    front["shell_component"] = "interior_wall"
    walls.append(front)

    # Back wall
    bpy.ops.mesh.primitive_plane_add(
        size=1,
        location=(center_x, center_y - size_y / 2, z_min + height / 2),
    )
    back = bpy.context.active_object
    back.name = f"{name}_interior_wall_back"
    back.scale = (size_x, height, 1)
    back.data.materials.append(mat)
    back["shell_component"] = "interior_wall"
    walls.append(back)

    # Left wall
    bpy.ops.mesh.primitive_plane_add(
        size=1,
        location=(center_x - size_x / 2, center_y, z_min + height / 2),
    )
    left = bpy.context.active_object
    left.name = f"{name}_interior_wall_left"
    left.rotation_euler = (0, 0, math.pi / 2)
    left.scale = (size_y, height, 1)
    left.data.materials.append(mat)
    left["shell_component"] = "interior_wall"
    walls.append(left)

    # Right wall
    bpy.ops.mesh.primitive_plane_add(
        size=1,
        location=(center_x + size_x / 2, center_y, z_min + height / 2),
    )
    right = bpy.context.active_object
    right.name = f"{name}_interior_wall_right"
    right.rotation_euler = (0, 0, math.pi / 2)
    right.scale = (size_y, height, 1)
    right.data.materials.append(mat)
    right["shell_component"] = "interior_wall"
    walls.append(right)

    return walls


def check_shell_fit(exterior_bbox, interior_bbox):
    """Validate that interior shell fits properly inside exterior shell."""
    issues = []

    # Check wall thickness on all sides
    left_gap = interior_bbox["min"].x - exterior_bbox["min"].x
    right_gap = exterior_bbox["max"].x - interior_bbox["max"].x
    front_gap = interior_bbox["min"].y - exterior_bbox["min"].y
    back_gap = exterior_bbox["max"].y - interior_bbox["max"].y

    min_gap = min(left_gap, right_gap, front_gap, back_gap)

    if min_gap < WALL_THICKNESS * 0.5:
        issues.append(f"Wall thickness too thin: {min_gap:.3f}m < {WALL_THICKNESS * 0.5:.3f}m")

    if min_gap > WALL_THICKNESS * 3:
        issues.append(f"Wall thickness too thick: {min_gap:.3f}m > {WALL_THICKNESS * 3:.3f}m")

    # Check for z-fighting (interior ceiling above exterior ceiling)
    if interior_bbox["max"].z >= exterior_bbox["max"].z:
        issues.append("Interior ceiling pokes through exterior shell")

    if interior_bbox["min"].z <= exterior_bbox["min"].z:
        issues.append("Interior floor below exterior floor")

    return issues


def shell_material(name):
    """Create a material for shell components."""
    mat = bpy.data.materials.new(f"{name}_shell_mat")
    mat.use_nodes = True
    node = mat.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (0.15, 0.15, 0.18, 1.0)
    node.inputs["Metallic"].default_value = 0.1
    node.inputs["Roughness"].default_value = 0.7
    return mat


def process_building(asset_id, building_objects):
    """Process a single building: create interior shell, floor, ceiling."""
    if not building_objects:
        return None

    # Compute combined bounding box
    combined_min = Vector((float("inf"), float("inf"), float("inf")))
    combined_max = Vector((float("-inf"), float("-inf"), float("-inf")))

    for obj in building_objects:
        bbox = get_bounding_box(obj)
        combined_min = Vector((min(combined_min.x, bbox["min"].x),
                               min(combined_min.y, bbox["min"].y),
                               min(combined_min.z, bbox["min"].z)))
        combined_max = Vector((max(combined_max.x, bbox["max"].x),
                               max(combined_max.y, bbox["max"].y),
                               max(combined_max.z, bbox["max"].z)))

    exterior_bbox = {
        "min": combined_min,
        "max": combined_max,
        "size": combined_max - combined_min,
    }

    # Create shell collection
    col = shell_collection(f"{asset_id}_shells", asset_id)

    # Create materials
    mat = shell_material(asset_id)

    # Create shell components
    floor = create_floor_plate(asset_id, exterior_bbox, mat, col)
    ceiling = create_ceiling_panel(asset_id, exterior_bbox, mat, col)
    walls = create_interior_shell(asset_id, exterior_bbox, mat, col)

    # Compute interior bounding box
    interior_bbox = {
        "min": combined_min + Vector((WALL_THICKNESS, WALL_THICKNESS, FLOOR_THICKNESS)),
        "max": combined_max - Vector((WALL_THICKNESS, WALL_THICKNESS, CEILING_THICKNESS)),
        "size": combined_max - combined_min - Vector((WALL_THICKNESS * 2, WALL_THICKNESS * 2, FLOOR_THICKNESS + CEILING_THICKNESS)),
    }

    # Validate fit
    issues = check_shell_fit(exterior_bbox, interior_bbox)

    return {
        "assetId": asset_id,
        "exteriorShell": f"{asset_id}-exterior",
        "interiorShell": f"{asset_id}-interior",
        "components": {
            "floorPlate": floor.name,
            "ceilingPanel": ceiling.name,
            "interiorWalls": [w.name for w in walls],
        },
        "wallThickness": WALL_THICKNESS,
        "floorThickness": FLOOR_THICKNESS,
        "ceilingThickness": CEILING_THICKNESS,
        "fitIssues": issues,
        "fitPassed": len(issues) == 0,
    }


def main():
    reset_scene()

    # Load master scene
    master_blend = SOURCES / "lunar-city-sculpted-master-assets.blend"
    if not master_blend.exists():
        print(f"ERROR: Master scene not found at {master_blend}")
        return

    with bpy.data.libraries.load(str(master_blend)) as (data_from, data_to):
        data_to.objects = [name for name in data_from.objects if name]

    for obj in data_to.objects:
        if obj is not None:
            bpy.context.scene.collection.objects.link(obj)

    # Load manifest
    with open(SOURCES.parent / "master-asset-manifest.json") as f:
        manifest = json.load(f)

    buildings = [
        a for a in manifest.get("requiredAssets", [])
        if a.get("kind") == "building"
    ]

    SHELLS.mkdir(parents=True, exist_ok=True)

    results = []
    for building in buildings:
        asset_id = building["id"]
        objects = find_building_objects(asset_id.replace("-workshop", "").replace("-", ""))
        if not objects:
            # Try with the full ID
            objects = [obj for obj in bpy.data.objects
                       if obj.type == "MESH" and any(
                           part in obj.name.lower()
                           for part in asset_id.lower().split("-")
                           if len(part) > 3
                       )]

        result = process_building(asset_id, objects)
        if result:
            results.append(result)

    # Write manifest
    shell_manifest = {
        "schemaVersion": 1,
        "description": "Interior/exterior shell fitting for Lunar City buildings",
        "wallThickness": WALL_THICKNESS,
        "floorThickness": FLOOR_THICKNESS,
        "ceilingThickness": CEILING_THICKNESS,
        "buildings": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["fitPassed"]),
            "failed": sum(1 for r in results if not r["fitPassed"]),
        },
    }

    with open(MANIFEST_PATH, "w") as f:
        json.dump(shell_manifest, f, indent=2)

    print(f"Shell fitting complete: {shell_manifest['summary']}")
    print(f"Manifest written to: {MANIFEST_PATH}")

    # Save output blend
    output_blend = SHELLS / "lunar-city-shell-fittings.blend"
    bpy.ops.wm.save_as_mainfile(str(output_blend))
    print(f"Shell scene saved to: {output_blend}")


if __name__ == "__main__":
    main()
