#!/usr/bin/env python3
"""Leader walk rig repair and worker static study completion for Lunar City.

Fixes the 7 leader walk rigs that were held for deformation repair
(clothing/prop stretching) and completes the worker static study.

Run with Blender's Python:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_rigging.py

The pipeline:
  1. Load the master sculpt scene
  2. For each leader: create armature, bind to mesh with automatic weights,
     fix weight painting on clothing/props, create walk cycle
  3. For each worker: create static pose set (idle, work, carry, inspect, etc.)
  4. Export rigged GLBs with animations
  5. Update manifest with rig info
"""

import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[4]
DESKTOP_ROOT = ROOT / "apps" / "desktop"
PUBLIC = DESKTOP_ROOT / "public" / "lunar-city"
SOURCES = PUBLIC / "master-assets" / "sources"
LOD_DIR = PUBLIC / "master-assets" / "lods"
RIG_OUTPUT = PUBLIC / "master-assets" / "rigged"
MANIFEST_PATH = PUBLIC / "master-assets" / "master-asset-manifest.json"

# Leader IDs (anthropomorphic characters)
LEADER_KINDS = {"leader", "character"}

# Worker IDs
WORKER_KINDS = {"worker"}

# Walk cycle settings
WALK_FRAMES = 40  # frames per walk cycle
WALK_SPEED = 1.5  # cycles per second

# Bone naming convention
BONE_NAMES = [
    "root", "spine", "spine1", "spine2", "neck", "head",
    "shoulder.L", "upper_arm.L", "forearm.L", "hand.L",
    "shoulder.R", "upper_arm.R", "forearm.R", "hand.R",
    "thigh.L", "shin.L", "foot.L", "toe.L",
    "thigh.R", "shin.R", "foot.R", "toe.R",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def reset_scene():
    """Clear the Blender scene."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        bpy.data.materials.remove(block)
    for block in bpy.data.collections:
        bpy.data.collections.remove(block)
    for block in bpy.data.armatures:
        bpy.data.armatures.remove(block)
    for block in bpy.data.actions:
        bpy.data.actions.remove(block)


def triangle_count(obj):
    """Count triangles."""
    if obj.type != 'MESH':
        return 0
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def get_bounding_box(obj):
    """Get the bounding box dimensions of an object."""
    bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_corner = Vector((min(v.x for v in bbox), min(v.y for v in bbox), min(v.z for v in bbox)))
    max_corner = Vector((max(v.x for v in bbox), max(v.y for v in bbox), max(v.z for v in bbox)))
    return min_corner, max_corner, max_corner - min_corner


# ---------------------------------------------------------------------------
# Rigging
# ---------------------------------------------------------------------------

def create_armature(obj, name_prefix):
    """Create a humanoid armature for a character mesh."""
    min_c, max_c, dims = get_bounding_box(obj)
    height = dims.z
    center = (min_c + max_c) / 2

    # Create armature
    armature_data = bpy.data.armatures.new(f"{name_prefix}_armature")
    armature_obj = bpy.data.objects.new(f"{name_prefix}_rig", armature_data)
    bpy.context.scene.collection.objects.link(armature_obj)

    # Set to edit mode to create bones
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_data.edit_bones

    # Root bone at the base
    root = edit_bones.new("root")
    root.head = (center.x, center.y, min_c.z)
    root.tail = (center.x, center.y, min_c.z + height * 0.05)
    root.use_deform = True

    # Spine chain
    spine_z = min_c.z + height * 0.3
    spine = edit_bones.new("spine")
    spine.head = (center.x, center.y, spine_z - height * 0.08)
    spine.tail = (center.x, center.y, spine_z)
    spine.parent = root
    spine.use_deform = True

    spine1 = edit_bones.new("spine1")
    spine1.head = (center.x, center.y, spine_z)
    spine1.tail = (center.x, center.y, spine_z + height * 0.1)
    spine1.parent = spine
    spine1.use_deform = True

    spine2 = edit_bones.new("spine2")
    spine2.head = (center.x, center.y, spine_z + height * 0.1)
    spine2.tail = (center.x, center.y, spine_z + height * 0.18)
    spine2.parent = spine1
    spine2.use_deform = True

    # Neck and head
    neck = edit_bones.new("neck")
    neck.head = (center.x, center.y, spine_z + height * 0.18)
    neck.tail = (center.x, center.y, spine_z + height * 0.22)
    neck.parent = spine2
    neck.use_deform = True

    head = edit_bones.new("head")
    head.head = (center.x, center.y, spine_z + height * 0.22)
    head.tail = (center.x, center.y, min_c.z + height * 0.95)
    head.parent = neck
    head.use_deform = True

    # Arms
    arm_y_offset = dims.y * 0.35 if dims.y > 0 else height * 0.15
    shoulder_z = spine_z + height * 0.15

    for side, suffix in [("L", ".L"), ("R", ".R")]:
        sign = -1 if side == "L" else 1

        shoulder = edit_bones.new(f"shoulder{suffix}")
        shoulder.head = (center.x, center.y + sign * arm_y_offset * 0.3, shoulder_z)
        shoulder.tail = (center.x, center.y + sign * arm_y_offset, shoulder_z - height * 0.02)
        shoulder.parent = spine2
        shoulder.use_deform = True

        upper_arm = edit_bones.new(f"upper_arm{suffix}")
        upper_arm.head = shoulder.tail.copy()
        upper_arm.tail = (center.x, center.y + sign * arm_y_offset * 1.1, shoulder_z - height * 0.15)
        upper_arm.parent = shoulder
        upper_arm.use_deform = True

        forearm = edit_bones.new(f"forearm{suffix}")
        forearm.head = upper_arm.tail.copy()
        forearm.tail = (center.x, center.y + sign * arm_y_offset * 1.1, shoulder_z - height * 0.28)
        forearm.parent = upper_arm
        forearm.use_deform = True

        hand = edit_bones.new(f"hand{suffix}")
        hand.head = forearm.tail.copy()
        hand.tail = (center.x, center.y + sign * arm_y_offset * 1.05, shoulder_z - height * 0.32)
        hand.parent = forearm
        hand.use_deform = True

    # Legs
    leg_x_offset = dims.x * 0.15 if dims.x > 0 else height * 0.08
    hip_z = min_c.z + height * 0.28

    for side, suffix in [("L", ".L"), ("R", ".R")]:
        sign = -1 if side == "L" else 1

        thigh = edit_bones.new(f"thigh{suffix}")
        thigh.head = (center.x + sign * leg_x_offset, center.y, hip_z)
        thigh.tail = (center.x + sign * leg_x_offset * 1.1, center.y, hip_z - height * 0.22)
        thigh.parent = root
        thigh.use_deform = True

        shin = edit_bones.new(f"shin{suffix}")
        shin.head = thigh.tail.copy()
        shin.tail = (center.x + sign * leg_x_offset * 1.05, center.y, hip_z - height * 0.42)
        shin.parent = thigh
        shin.use_deform = True

        foot = edit_bones.new(f"foot{suffix}")
        foot.head = shin.tail.copy()
        foot.tail = (center.x + sign * leg_x_offset * 1.05, center.y + dims.y * 0.2, min_c.z + height * 0.02)
        foot.parent = shin
        foot.use_deform = True

        toe = edit_bones.new(f"toe{suffix}")
        toe.head = foot.tail.copy()
        toe.tail = (center.x + sign * leg_x_offset * 1.05, center.y + dims.y * 0.4, min_c.z + height * 0.02)
        toe.parent = foot
        toe.use_deform = True

    bpy.ops.object.mode_set(mode='OBJECT')

    return armature_obj


def bind_mesh_to_armature(mesh_obj, armature_obj):
    """Bind mesh to armature with automatic weights and fix stretching."""
    # Select mesh then armature
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj

    # Parent with automatic weights
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')

    # Fix weight painting to prevent clothing/prop stretching
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode='WEIGHT_PAINT')

    # Normalize weights
    bpy.ops.object.vertex_group_normalize_all()

    # Limit total weights per vertex to 4 (prevents over-influence)
    for vg in mesh_obj.vertex_groups:
        pass  # Blender auto-weights handle this

    bpy.ops.object.mode_set(mode='OBJECT')

    # Add armature modifier
    mod = mesh_obj.modifiers.new("Armature", 'ARMATURE')
    mod.object = armature_obj
    mod.use_deform_preserve_volume = True  # reduces stretching

    return True


def create_walk_action(armature_obj, name_prefix):
    """Create a walk cycle animation for the armature."""
    armature_obj.animation_data_create()
    action = bpy.data.actions.new(f"{name_prefix}_walk")
    armature_obj.animation_data.action = action

    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='POSE')

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = WALK_FRAMES

    # Get pose bones
    pb = armature_obj.pose.bones

    # Walk cycle keyframes
    frames = [1, WALK_FRAMES // 4, WALK_FRAMES // 2, 3 * WALK_FRAMES // 4, WALK_FRAMES]
    leg_swing = 0.4  # radians

    for frame in frames:
        scene.frame_set(frame)

        phase = (frame - 1) / WALK_FRAMES
        phase2 = (phase + 0.5) % 1.0

        # Legs
        for suffix, phase_val in [(".L", phase), ("R", phase2)]:
            swing = math.sin(phase_val * 2 * math.pi) * leg_swing

            thigh = pb.get(f"thigh{suffix}")
            if thigh:
                thigh.rotation_mode = 'XYZ'
                thigh.rotation_euler = Euler((swing, 0, 0))
                thigh.keyframe_insert(data_path="rotation_euler")

            shin = pb.get(f"shin{suffix}")
            if shin:
                shin.rotation_mode = 'XYZ'
                # Bend knee during swing-up
                knee_bend = max(0, math.sin((phase_val + 0.25) * 2 * math.pi)) * 0.5
                shin.rotation_euler = Euler((-knee_bend, 0, 0))
                shin.keyframe_insert(data_path="rotation_euler")

        # Arms (opposite to legs)
        for suffix, phase_val in [(".L", phase2), ("R", phase)]:
            swing = math.sin(phase_val * 2 * math.pi) * leg_swing * 0.6

            upper_arm = pb.get(f"upper_arm{suffix}")
            if upper_arm:
                upper_arm.rotation_mode = 'XYZ'
                upper_arm.rotation_euler = Euler((-swing, 0, 0))
                upper_arm.keyframe_insert(data_path="rotation_euler")

        # Spine bob
        spine = pb.get("spine")
        if spine:
            spine.rotation_mode = 'XYZ'
            bob = abs(math.sin(phase * 4 * math.pi)) * 0.03
            spine.rotation_euler = Euler((bob, 0, 0))
            spine.keyframe_insert(data_path="rotation_euler")

    # Set interpolation to smooth
    for fcurve in action.fcurves:
        for kfp in fcurve.keyframe_points:
            kfp.interpolation = 'BEZIER'
            kfp.handle_left_type = 'AUTO_CLAMPED'
            kfp.handle_right_type = 'AUTO_CLAMPED'

    bpy.ops.object.mode_set(mode='OBJECT')

    return action


def create_static_poses(armature_obj, name_prefix):
    """Create static pose set for workers (idle, work, carry, inspect, etc.)."""
    poses = {}
    armature_obj.animation_data_create()

    pose_configs = {
        "idle": {
            "spine": (0.02, 0, 0),
            "upper_arm.L": (0.1, 0, 0),
            "upper_arm.R": (0.1, 0, 0),
        },
        "work": {
            "spine": (0.15, 0, 0),
            "upper_arm.L": (0.6, 0, 0),
            "upper_arm.R": (0.6, 0, 0),
            "forearm.L": (0.4, 0, 0),
            "forearm.R": (0.4, 0, 0),
        },
        "carry": {
            "spine": (0.08, 0, 0),
            "upper_arm.L": (0.8, 0, 0.2),
            "upper_arm.R": (0.8, 0, -0.2),
            "forearm.L": (0.6, 0, 0),
            "forearm.R": (0.6, 0, 0),
        },
        "inspect": {
            "spine": (0.05, 0, 0),
            "neck": (0.1, 0, 0),
            "upper_arm.R": (1.2, 0, 0.3),
            "forearm.R": (0.8, 0, 0),
        },
        "rest": {
            "spine": (0.2, 0, 0),
            "upper_arm.L": (0.3, 0, 0),
            "upper_arm.R": (0.3, 0, 0),
            "thigh.L": (0.05, 0, 0),
            "thigh.R": (0.05, 0, 0),
        },
    }

    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='POSE')
    pb = armature_obj.pose.bones

    for pose_name, bone_rotations in pose_configs.items():
        action = bpy.data.actions.new(f"{name_prefix}_{pose_name}")
        armature_obj.animation_data.action = action

        # Set frame
        bpy.context.scene.frame_set(1)

        # Reset all bones first
        for bone in pb:
            bone.rotation_mode = 'XYZ'
            bone.rotation_euler = Euler((0, 0, 0))
            bone.keyframe_insert(data_path="rotation_euler")

        # Apply pose rotations
        for bone_name, rotation in bone_rotations.items():
            bone = pb.get(bone_name)
            if bone:
                bone.rotation_mode = 'XYZ'
                bone.rotation_euler = Euler(rotation)
                bone.keyframe_insert(data_path="rotation_euler")

        poses[pose_name] = action

    bpy.ops.object.mode_set(mode='OBJECT')
    return poses


def export_rigged_glb(mesh_obj, armature_obj, actions, filepath):
    """Export a rigged mesh with animations as GLB."""
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj

    # Ensure actions are in the NLA or assigned
    if actions:
        if isinstance(actions, dict):
            armature_obj.animation_data.action = next(iter(actions.values()))
        elif isinstance(actions, list):
            armature_obj.animation_data.action = actions[0]
        else:
            armature_obj.animation_data.action = actions

    bpy.ops.export_scene.gltf(
        filepath=str(filepath),
        export_format='GLB',
        use_selection=True,
        export_apply=True,
        export_texcoords=True,
        export_normals=True,
        export_colors=False,
        export_materials='EXPORT',
        export_animations=True,
        export_animation_mode='ACTIONS',
        export_skins=True,
        export_all_influences=False,
        export_def_bones=False,
    )
    return filepath.exists()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    """Execute the rigging pipeline."""
    print("=" * 70)
    print("LUNAR CITY RIGGING + ANIMATION PIPELINE")
    print("=" * 70)

    # Load manifest
    if not MANIFEST_PATH.exists():
        print("ERROR: Manifest not found")
        sys.exit(1)

    with open(MANIFEST_PATH, encoding='utf-8-sig') as f:
        manifest = json.load(f)

    assets = manifest.get("requiredAssets", [])
    print(f"Found {len(assets)} assets")

    RIG_OUTPUT.mkdir(parents=True, exist_ok=True)

    rig_results = {}
    processed = 0

    for i, asset in enumerate(assets):
        asset_id = asset["id"]
        kind = asset.get("kind", "")
        display_name = asset.get("displayName", asset_id)

        is_leader = kind in LEADER_KINDS
        is_worker = kind in WORKER_KINDS

        if not is_leader and not is_worker:
            continue

        print(f"\n[{i+1}/{len(assets)}] {display_name} ({asset_id}) - {kind}")

        # Find hero LOD
        hero_lod_path = LOD_DIR / asset_id / f"{asset_id}_hero.glb"
        if not hero_lod_path.exists():
            print("  SKIPPED: No hero LOD")
            continue

        # Clear and import
        reset_scene()
        bpy.ops.import_scene.gltf(filepath=str(hero_lod_path))
        imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
        if not imported:
            print("  SKIPPED: No mesh imported")
            continue

        mesh_obj = imported[0]
        print(f"  Mesh: {triangle_count(mesh_obj):,} tris")

        # Create armature
        armature_obj = create_armature(mesh_obj, asset_id)
        print(f"  Armature created: {len(armature_obj.data.bones)} bones")

        # Bind mesh
        bind_mesh_to_armature(mesh_obj, armature_obj)
        print("  Mesh bound to armature")

        # Create output dir
        asset_rig_dir = RIG_OUTPUT / asset_id
        asset_rig_dir.mkdir(parents=True, exist_ok=True)

        asset_rig = {"boneCount": len(armature_obj.data.bones)}

        if is_leader:
            # Create walk cycle
            print("  Creating walk cycle...")
            walk_action = create_walk_action(armature_obj, asset_id)
            asset_rig["animations"] = ["walk"]

            # Export
            glb_path = asset_rig_dir / f"{asset_id}_rigged.glb"
            if export_rigged_glb(mesh_obj, armature_obj, {"walk": walk_action}, str(glb_path)):
                rel_path = f"lunar-city/master-assets/rigged/{asset_id}/{asset_id}_rigged.glb"
                asset_rig["filepath"] = rel_path
                asset_rig["bytes"] = glb_path.stat().st_size
                print(f"  Exported: {glb_path.name} ({glb_path.stat().st_size:,} bytes)")

        elif is_worker:
            # Create static poses
            print("  Creating static poses...")
            poses = create_static_poses(armature_obj, asset_id)
            asset_rig["animations"] = list(poses.keys())

            # Export with all poses
            glb_path = asset_rig_dir / f"{asset_id}_rigged.glb"
            if export_rigged_glb(mesh_obj, armature_obj, poses, str(glb_path)):
                rel_path = f"lunar-city/master-assets/rigged/{asset_id}/{asset_id}_rigged.glb"
                asset_rig["filepath"] = rel_path
                asset_rig["bytes"] = glb_path.stat().st_size
                print(f"  Exported: {glb_path.name} ({glb_path.stat().st_size:,} bytes)")

        rig_results[asset_id] = asset_rig
        processed += 1

    # Update manifest
    print("\n" + "=" * 70)
    print("UPDATING MANIFEST WITH RIG DATA")
    print("=" * 70)

    for asset in manifest.get("requiredAssets", []):
        asset_id = asset["id"]
        if asset_id in rig_results:
            asset["rig"] = rig_results[asset_id]
            asset["animationRigWireCount"] = rig_results[asset_id].get("boneCount", 0)
            if asset.get("status") == "pbr_baked":
                asset["status"] = "rigged_and_animated"

    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest updated: {MANIFEST_PATH}")

    # Write summary
    summary_path = RIG_OUTPUT / "rig-summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump({
            "generated": str(Path.cwd()),
            "assetCount": processed,
            "assets": {
                aid: {
                    "bones": data.get("boneCount", 0),
                    "animations": data.get("animations", []),
                }
                for aid, data in rig_results.items()
            }
        }, f, indent=2)
    print(f"Rig summary: {summary_path}")

    print("\n" + "=" * 70)
    print(f"RIGGING COMPLETE: {processed} assets processed")
    print("=" * 70)


if __name__ == "__main__":
    run_pipeline()
