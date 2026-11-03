#!/usr/bin/env python3
"""Collision mesh generation for Lunar City assets.

Generates simplified convex hull and mesh collision proxies for each asset's
hero LOD. These are exported as separate GLB files for use in the game physics.

Run with Blender's Python:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_collision.py

The pipeline:
  1. For each asset with a hero LOD, generate collision proxies
  2. Create both convex hull (for dynamic bodies) and simplified mesh (for static)
  3. Export collision meshes as separate GLBs
  4. Update master-asset-manifest.json with collision paths
"""

import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[4]
DESKTOP_ROOT = ROOT / "apps" / "desktop"
PUBLIC = DESKTOP_ROOT / "public" / "lunar-city"
LOD_DIR = PUBLIC / "master-assets" / "lods"
COLLISION_OUTPUT = PUBLIC / "master-assets" / "collision"
MANIFEST_PATH = PUBLIC / "master-assets" / "master-asset-manifest.json"

# Collision mesh settings
CONVEX_HULL_DECAY = 0.02  # shrinkwrap offset for convex hull
MESH_SIMPLIFY_RATIO = 0.1  # collision mesh is 10% of hero LOD
MAX_COLLISION_TRIS = 1000  # hard cap on collision triangle count
MIN_COLLISION_TRIS = 32    # minimum collision mesh complexity

# Asset kinds that need collision
COLLISION_KINDS = {"terrain", "road", "building", "prop", "vehicle", "character", "worker", "leader"}

# Asset kinds that use convex hull only (dynamic objects)
CONVEX_ONLY_KINDS = {"character", "worker", "leader", "vehicle"}


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


def triangle_count(obj):
    """Count triangles in a mesh."""
    if obj.type != 'MESH':
        return 0
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def generate_convex_hull(source_obj):
    """Generate a convex hull collision proxy from a source mesh."""
    # Duplicate the source
    hull_obj = source_obj.copy()
    hull_obj.data = source_obj.data.copy()
    hull_obj.name = f"{source_obj.name}_collision_convex"
    bpy.context.scene.collection.objects.link(hull_obj)

    # Add remesh modifier for clean voxel-based convex hull
    remesh = hull_obj.modifiers.new(name="Collision_VoxelRemesh", type='REMESH')
    remesh.mode = 'SMOOTH'
    remesh.octree_depth = 6
    remesh.scale = 0.9
    remesh.use_remove_disconnected = True
    remesh.threshold = 0.5
    bpy.context.view_layer.objects.active = hull_obj
    bpy.ops.object.modifier_apply(modifier=remesh.name)

    # Decimate to reasonable collision complexity
    current_tris = triangle_count(hull_obj)
    if current_tris > MAX_COLLISION_TRIS:
        ratio = MAX_COLLISION_TRIS / current_tris
        ratio = max(0.01, ratio)
        decimate = hull_obj.modifiers.new(name="Collision_Decimate", type='DECIMATE')
        decimate.decimate_type = 'COLLAPSE'
        decimate.ratio = ratio
        bpy.ops.object.modifier_apply(modifier=decimate.name)

    # Clean up
    bpy.context.view_layer.objects.active = hull_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.convex_hull()
    bpy.ops.mesh.remove_doubles(threshold=0.001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Tag as collision
    hull_obj["collision_type"] = "convex_hull"
    hull_obj["is_collision_proxy"] = True

    return hull_obj


def generate_mesh_collision(source_obj):
    """Generate a simplified mesh collision proxy (preserves shape better than convex hull)."""
    mesh_obj = source_obj.copy()
    mesh_obj.data = source_obj.data.copy()
    mesh_obj.name = f"{source_obj.name}_collision_mesh"
    bpy.context.scene.collection.objects.link(mesh_obj)

    # Simplify
    current_tris = triangle_count(mesh_obj)
    target_tris = max(MIN_COLLISION_TRIS, min(MAX_COLLISION_TRIS, int(current_tris * MESH_SIMPLIFY_RATIO)))

    if current_tris > target_tris:
        ratio = target_tris / current_tris
        ratio = max(0.01, ratio)
        decimate = mesh_obj.modifiers.new(name="CollisionMesh_Decimate", type='DECIMATE')
        decimate.decimate_type = 'COLLAPSE'
        decimate.ratio = ratio
        bpy.ops.object.modifier_apply(modifier=decimate.name)

    # Clean up
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Tag
    mesh_obj["collision_type"] = "simplified_mesh"
    mesh_obj["is_collision_proxy"] = True

    return mesh_obj


def export_collision_glb(obj, filepath):
    """Export a collision mesh as GLB."""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.export_scene.gltf(
        filepath=str(filepath),
        export_format='GLB',
        use_selection=True,
        export_apply=True,
        export_texcoords=False,
        export_normals=False,
        export_colors=False,
        export_materials='NONE',
        export_animations=False,
    )
    return filepath.exists()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    """Execute the collision mesh generation pipeline."""
    print("=" * 70)
    print("LUNAR CITY COLLISION MESH GENERATION")
    print("=" * 70)

    # Load manifest
    if not MANIFEST_PATH.exists():
        print("ERROR: Manifest not found")
        sys.exit(1)

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    assets = manifest.get("requiredAssets", [])
    print(f"Found {len(assets)} assets")

    COLLISION_OUTPUT.mkdir(parents=True, exist_ok=True)

    collision_results = {}
    processed = 0

    for i, asset in enumerate(assets):
        asset_id = asset["id"]
        kind = asset.get("kind", "")
        display_name = asset.get("displayName", asset_id)

        # Skip assets that don't need collision
        if kind not in COLLISION_KINDS:
            print(f"[{i+1}/{len(assets)}] SKIP {display_name} (kind={kind}, no collision needed)")
            continue

        print(f"\n[{i+1}/{len(assets)}] {display_name} ({asset_id})")

        # Find hero LOD
        hero_lod_path = LOD_DIR / asset_id / f"{asset_id}_hero.glb"
        if not hero_lod_path.exists():
            print("  SKIPPED: No hero LOD found")
            continue

        # Clear scene and import
        reset_scene()
        bpy.ops.import_scene.gltf(filepath=str(hero_lod_path))
        imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
        if not imported:
            print("  SKIPPED: Import produced no meshes")
            continue

        source_obj = imported[0]
        print(f"  Source: {triangle_count(source_obj):,} tris")

        # Create output dir
        asset_coll_dir = COLLISION_OUTPUT / asset_id
        asset_coll_dir.mkdir(parents=True, exist_ok=True)

        asset_collisions = {}

        # Generate convex hull (always)
        print("  Generating convex hull...")
        hull_obj = generate_convex_hull(source_obj)
        hull_tris = triangle_count(hull_obj)
        hull_path = asset_coll_dir / f"{asset_id}_collision_convex.glb"
        if export_collision_glb(hull_obj, str(hull_path)):
            rel_path = f"lunar-city/master-assets/collision/{asset_id}/{asset_id}_collision_convex.glb"
            asset_collisions["convexHull"] = {
                "filepath": rel_path,
                "triangleCount": hull_tris,
                "bytes": hull_path.stat().st_size,
            }
            print(f"    Convex hull: {hull_tris} tris, {hull_path.stat().st_size:,} bytes")

        # Generate mesh collision (for static objects)
        if kind not in CONVEX_ONLY_KINDS:
            print("  Generating simplified mesh collision...")
            mesh_obj = generate_mesh_collision(source_obj)
            mesh_tris = triangle_count(mesh_obj)
            mesh_path = asset_coll_dir / f"{asset_id}_collision_mesh.glb"
            if export_collision_glb(mesh_obj, str(mesh_path)):
                rel_path = f"lunar-city/master-assets/collision/{asset_id}/{asset_id}_collision_mesh.glb"
                asset_collisions["simplifiedMesh"] = {
                    "filepath": rel_path,
                    "triangleCount": mesh_tris,
                    "bytes": mesh_path.stat().st_size,
                }
                print(f"    Mesh collision: {mesh_tris} tris, {mesh_path.stat().st_size:,} bytes")

        collision_results[asset_id] = asset_collisions
        processed += 1

    # Update manifest
    print("\n" + "=" * 70)
    print("UPDATING MANIFEST WITH COLLISION DATA")
    print("=" * 70)

    for asset in manifest.get("requiredAssets", []):
        asset_id = asset["id"]
        if asset_id in collision_results:
            asset["collision"] = collision_results[asset_id]

    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest updated: {MANIFEST_PATH}")

    # Write summary
    summary_path = COLLISION_OUTPUT / "collision-summary.json"
    with open(summary_path, 'w') as f:
        json.dump({
            "generated": str(Path.cwd()),
            "assetCount": processed,
            "assets": {
                aid: {k: v.get("triangleCount", 0) for k, v in cols.items()}
                for aid, cols in collision_results.items()
            }
        }, f, indent=2)
    print(f"Collision summary: {summary_path}")

    print("\n" + "=" * 70)
    print(f"COLLISION GENERATION COMPLETE: {processed} assets processed")
    print("=" * 70)


if __name__ == "__main__":
    run_pipeline()
