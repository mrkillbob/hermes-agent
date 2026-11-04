#!/usr/bin/env python3
"""Retopology + LOD generation pipeline for Lunar City master assets.

Reads the authoritative master sculpt scene (lunar-city-sculpted-master-assets.blend),
derives quad-dominant low-poly meshes for each asset, and generates a full LOD chain
(hero / high / medium / low) exported as individual GLBs.

Run with Blender's Python:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_retopology_lod.py

The pipeline:
  1. Open the master sculpt scene
  2. For each asset collection, duplicate and decimate to quad-dominant target
  3. Generate 4 LOD levels with configurable triangle budgets
  4. Export each LOD as a separate GLB
  5. Update master-asset-manifest.json with LOD paths and triangle counts
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

BLENDER_EXE = "/Applications/Blender.app/Contents/MacOS/Blender"
ROOT = Path(__file__).resolve().parents[4]
DESKTOP_ROOT = ROOT / "apps" / "desktop"
PUBLIC = DESKTOP_ROOT / "public" / "lunar-city"
SOURCES = PUBLIC / "master-assets" / "sources"
LOD_OUTPUT = PUBLIC / "master-assets" / "lods"
MANIFEST_PATH = PUBLIC / "master-assets" / "master-asset-manifest.json"
METADATA_PATH = SOURCES / "lunar-city-sculpted-master-assets-metadata.json"

# LOD triangle budgets as a fraction of the source triangle count
# These are derived from the manifest's evaluatedTriangleCount
LOD_BUDGETS = {
    "hero": 0.50,    # 50% of source — highest quality, close-up shots
    "high": 0.20,    # 20% of source — standard gameplay distance
    "medium": 0.08,  # 8% of source — mid-distance
    "low": 0.025,    # 2.5% of source — far distance / background
}

# Absolute floor per LOD level (never go below this)
LOD_FLOOR = {
    "hero": 50000,
    "high": 15000,
    "medium": 5000,
    "low": 1500,
}

# Absolute ceiling per LOD level (never exceed this)
LOD_CEILING = {
    "hero": 300000,
    "high": 100000,
    "medium": 40000,
    "low": 10000,
}

# Assets that are hero assets get a higher hero budget
HERO_BUDGET_MULTIPLIER = 1.0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def reset_scene():
    """Clear all data from the current Blender scene."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    for block in bpy.data.meshes:
        bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        bpy.data.materials.remove(block)
    for block in bpy.data.collections:
        bpy.data.collections.remove(block)
    for block in bpy.data.images:
        bpy.data.images.remove(block)


def lod_dir(asset_id):
    """Create and return the LOD output directory for an asset."""
    d = LOD_OUTPUT / asset_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def triangle_count(obj):
    """Count triangles in a mesh object (evaluated with modifiers)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    tris = sum(len(p.vertices) - 2 for p in mesh.polygons)
    eval_obj.to_mesh_clear()
    return tris


def collection_triangle_count(collection):
    """Count total triangles across all mesh objects in a collection."""
    total = 0
    for obj in collection.objects:
        if obj.type == 'MESH':
            total += triangle_count(obj)
    return total


def duplicate_collection(source_collection, new_name):
    """Duplicate a collection and all its objects."""
    new_coll = bpy.data.collections.new(new_name)
    bpy.context.scene.collection.children.link(new_coll)

    obj_map = {}
    for obj in source_collection.objects:
        new_obj = obj.copy()
        new_obj.data = obj.data.copy()
        new_obj.name = f"{obj.name}_{new_name}"
        if obj.animation_data:
            new_obj.animation_data_copy = obj.animation_data.copy()
        new_coll.objects.link(new_obj)
        obj_map[obj.name] = new_obj

    # Copy materials
    for obj in new_coll.objects:
        if obj.data and hasattr(obj.data, 'materials'):
            for i, mat in enumerate(obj.data.materials):
                if mat and mat.name not in [m.name for m in bpy.data.materials]:
                    # Material already exists globally, no need to copy
                    pass

    return new_coll, obj_map


def retopologize_decimate(obj, target_ratio, method='QUADRIFORM'):
    """Apply a quad-dominant decimation to reach the target triangle ratio.
    
    Uses the Decimate modifier with COLLAPSE mode for ratio control,
    then optionally applies a planar decimation for cleaner quads.
    """
    if obj.type != 'MESH':
        return

    current_tris = triangle_count(obj)
    if current_tris == 0:
        return

    target_tris = max(4, int(current_tris * target_ratio))
    ratio = target_tris / current_tris if current_tris > 0 else 1.0

    # Clamp ratio to valid range
    ratio = max(0.01, min(0.99, ratio))

    # Primary decimation
    decimate = obj.modifiers.new(name="LOD_Decimate", type='DECIMATE')
    decimate.decimate_type = 'COLLAPSE'
    decimate.ratio = ratio
    decimate.use_collapse_triangulate = False

    # Apply modifier
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=decimate.name)

    # Planar pass for cleaner quads (removes flat-area triangles)
    actual_tris = triangle_count(obj)
    if actual_tris > target_tris * 1.2:
        planar = obj.modifiers.new(name="LOD_Planar", type='DECIMATE')
        planar.decimate_type = 'DISSOLVE'
        planar.angle_limit = math.radians(5.0)
        planar.use_dissolve_boundaries = True
        bpy.ops.object.modifier_apply(modifier=planar.name)

    # Clean up
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Add weighted normals for better shading on low-poly
    wn = obj.modifiers.new(name="LOD_WeightedNormal", type='WEIGHTED_NORMAL')
    if hasattr(wn, 'keep_sharp'):
        wn.keep_sharp = True
    if hasattr(wn, 'weight'):
        wn.weight = 50
    bpy.ops.object.modifier_apply(modifier=wn.name)


def export_lod_glb(collection, filepath):
    """Export a collection as a GLB file."""
    # Deselect all
    bpy.ops.object.select_all(action='DESELECT')

    # Select all mesh objects in the collection
    for obj in collection.objects:
        if obj.type == 'MESH':
            obj.select_set(True)

    if not any(obj.select_get() for obj in collection.objects):
        print(f"  WARNING: No mesh objects in {collection.name}, skipping export")
        return False

    bpy.ops.export_scene.gltf(
        filepath=str(filepath),
        export_format='GLB',
        use_selection=True,
        export_apply=True,
        export_texcoords=True,
        export_normals=True,
        export_colors=False,
        export_materials='EXPORT',
        export_animations=False,
        export_image_format='AUTO',
    )
    return True


def generate_lod_chain(asset_id, source_collection, source_tris, is_hero=False):
    """Generate the full LOD chain for a single asset.
    
    Returns a dict of {lod_level: {tris, filepath}} for the manifest.
    """
    results = {}
    multiplier = HERO_BUDGET_MULTIPLIER if is_hero else 1.0
    out_dir = lod_dir(asset_id)

    for level, ratio in LOD_BUDGETS.items():
        adjusted_ratio = ratio * multiplier
        adjusted_ratio = max(0.005, min(0.95, adjusted_ratio))

        # Calculate target triangles
        target_tris = int(source_tris * adjusted_ratio)
        target_tris = max(LOD_FLOOR[level], min(LOD_CEILING[level], target_tris))

        actual_ratio = target_tris / source_tris if source_tris > 0 else 1.0

        print(f"  LOD {level}: {source_tris:,} → {target_tris:,} tris (ratio: {actual_ratio:.3f})")

        # Duplicate the collection for this LOD
        lod_name = f"{asset_id}_{level}"
        lod_coll, obj_map = duplicate_collection(source_collection, lod_name)

        # Apply retopology to each mesh object
        for obj_name, obj in obj_map.items():
            if obj.type == 'MESH':
                retopologize_decimate(obj, actual_ratio)

        # Count actual triangles after retopology
        actual_tris = collection_triangle_count(lod_coll)

        # Export
        glb_path = out_dir / f"{asset_id}_{level}.glb"
        exported = export_lod_glb(lod_coll, str(glb_path))

        if exported:
            rel_path = f"lunar-city/master-assets/lods/{asset_id}/{asset_id}_{level}.glb"
            results[level] = {
                "triangleCount": actual_tris,
                "filepath": rel_path,
                "ratio": round(actual_ratio, 4),
                "sourceBytes": glb_path.stat().st_size if glb_path.exists() else 0,
            }
            print(f"    Exported: {glb_path.name} ({actual_tris:,} tris, {results[level]['sourceBytes']:,} bytes)")
        else:
            results[level] = {"triangleCount": actual_tris, "filepath": None, "ratio": round(actual_ratio, 4)}

        # Clean up this LOD collection
        bpy.data.collections.remove(lod_coll)
        for obj_name, obj in obj_map.items():
            if obj.data:
                bpy.data.meshes.remove(obj.data)

    return results


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    """Execute the full retopology + LOD generation pipeline."""
    print("=" * 70)
    print("LUNAR CITY RETOPOLOGY + LOD PIPELINE")
    print("=" * 70)

    # Load manifest
    if not MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found at {MANIFEST_PATH}")
        sys.exit(1)

    with open(MANIFEST_PATH, encoding='utf-8-sig') as f:
        manifest = json.load(f)

    assets = manifest.get("requiredAssets", [])
    print(f"Found {len(assets)} assets in manifest")

    # Open master scene
    master_blend = SOURCES / "lunar-city-sculpted-master-assets.blend"
    if not master_blend.exists():
        print(f"ERROR: Master scene not found at {master_blend}")
        sys.exit(1)

    print(f"\nOpening master scene: {master_blend}")
    bpy.ops.wm.open_mainfile(filepath=str(master_blend))

    # Process each asset
    lod_manifest = {}
    for i, asset in enumerate(assets):
        asset_id = asset["id"]
        source_tris = asset.get("evaluatedTriangleCount", 0)
        is_hero = asset.get("heroAsset", False)
        display_name = asset.get("displayName", asset_id)

        print(f"\n[{i+1}/{len(assets)}] {display_name} ({asset_id})")
        print(f"  Source triangles: {source_tris:,}")

        if source_tris == 0:
            print("  SKIPPED: No triangles in source")
            continue

        # Find the asset's collection in the scene
        source_coll = bpy.data.collections.get(asset_id)
        if source_coll is None:
            # Try alternate naming
            alt_names = [
                f"Master Asset - {asset_id}",
                asset_id.replace("-", "_"),
                asset_id.replace("_", "-"),
            ]
            for alt in alt_names:
                source_coll = bpy.data.collections.get(alt)
                if source_coll:
                    break

        if source_coll is None:
            print(f"  SKIPPED: Collection '{asset_id}' not found in scene")
            print(f"  Available collections: {[c.name for c in bpy.data.collections]}")
            continue

        # Verify source triangle count
        actual_source_tris = collection_triangle_count(source_coll)
        if actual_source_tris > 0:
            source_tris = actual_source_tris

        # Generate LOD chain
        lod_results = generate_lod_chain(asset_id, source_coll, source_tris, is_hero)
        lod_manifest[asset_id] = lod_results

    # Update manifest with LOD info
    print("\n" + "=" * 70)
    print("UPDATING MANIFEST")
    print("=" * 70)

    for asset in manifest.get("requiredAssets", []):
        asset_id = asset["id"]
        if asset_id in lod_manifest:
            asset["lods"] = lod_manifest[asset_id]
            # Update status to reflect retopology completion
            if all(lod.get("filepath") for lod in lod_manifest[asset_id].values()):
                asset["status"] = "retopology_lods_complete"
            else:
                asset["status"] = "retopology_lods_partial"

    # Update production readiness
    all_complete = all(
        asset.get("status") == "retopology_lods_complete"
        for asset in manifest.get("requiredAssets", [])
    )
    if all_complete:
        manifest["productionReady"] = True

    # Write updated manifest
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest updated: {MANIFEST_PATH}")

    # Write LOD summary
    summary_path = LOD_OUTPUT / "lod-summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump({
            "generated": str(Path.cwd()),
            "assetCount": len(lod_manifest),
            "lodLevels": list(LOD_BUDGETS.keys()),
            "assets": {
                aid: {lvl: data.get("triangleCount", 0) for lvl, data in lods.items()}
                for aid, lods in lod_manifest.items()
            }
        }, f, indent=2)
    print(f"LOD summary: {summary_path}")

    print("\n" + "=" * 70)
    print(f"PIPELLETE COMPLETE: {len(lod_manifest)} assets processed")
    print("=" * 70)


if __name__ == "__main__":
    run_pipeline()
