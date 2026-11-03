#!/usr/bin/env python3
"""PBR texture bake pipeline for Lunar City assets.

Bakes high-poly master sculpt detail into PBR texture maps (albedo, normal,
roughness, metallic, AO, emissive) for each LOD level's UV-mapped mesh.

Run with Blender's Python:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_pbr_bake.py

The pipeline:
  1. Load the master sculpt scene
  2. For each asset, load the hero LOD (highest quality)
  3. Unwrap UVs if not present
  4. Bake PBR maps from the high-poly source to the LOD mesh
  5. Export textures as PNG (2k default, 4k for hero assets)
  6. Update master-asset-manifest.json with texture paths
"""

import json
import math
import os
import sys
from pathlib import Path

import bpy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[4]
DESKTOP_ROOT = ROOT / "apps" / "desktop"
PUBLIC = DESKTOP_ROOT / "public" / "lunar-city"
SOURCES = PUBLIC / "master-assets" / "sources"
LOD_DIR = PUBLIC / "master-assets" / "lods"
TEXTURE_OUTPUT = PUBLIC / "master-assets" / "textures"
MANIFEST_PATH = PUBLIC / "master-assets" / "master-asset-manifest.json"
METADATA_PATH = SOURCES / "lunar-city-sculpted-master-assets-metadata.json"

# Texture resolution per asset type
HERO_RESOLUTION = 4096
DEFAULT_RESOLUTION = 2048
TERRAIN_RESOLUTION = 4096
SKYBOX_RESOLUTION = 2048

# Bake settings
BAKE_MARGIN = 16  # pixels
BAKE_SAMPLES = 64
RAY_DISTANCE = 0.05  # cage extrusion distance for normal baking

# PBR map types to bake
BAKE_MAPS = {
    "albedo": {"type": "DIFFUSE", "color_space": "sRGB", "inputs": {"Color": True}},
    "normal": {"type": "NORMAL", "color_space": "Non-Color", "inputs": {}},
    "roughness": {"type": "ROUGHNESS", "color_space": "Non-Color", "inputs": {}},
    "metallic": {"type": "EMIT", "color_space": "Non-Color", "inputs": {}},  # baked from principled metallic
    "ao": {"type": "AO", "color_space": "Non-Color", "inputs": {}},
    "emissive": {"type": "EMIT", "color_space": "sRGB", "inputs": {}},
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def resolution_for_asset(asset):
    """Determine the appropriate texture resolution for an asset."""
    if asset.get("heroAsset", False):
        return HERO_RESOLUTION
    kind = asset.get("kind", "")
    if kind == "terrain":
        return TERRAIN_RESOLUTION
    if kind == "skybox":
        return SKYBOX_RESOLUTION
    return DEFAULT_RESOLUTION


def ensure_uv_map(obj):
    """Ensure the object has a valid UV map. Create one if missing."""
    if not obj.data.uv_layers:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(
            angle_limit=math.radians(66.0),
            island_margin=0.02,
            area_weight=0.0,
            correct_aspect=True,
            scale_to_bounds=True,
        )
        bpy.ops.object.mode_set(mode='OBJECT')
        print("    Created UV map via Smart UV Project")
    else:
        # Check if UV map has any actual data
        uv_layer = obj.data.uv_layers.active
        if uv_layer and len(uv_layer.data) == 0:
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
            bpy.ops.object.mode_set(mode='OBJECT')
            print("    Recreated empty UV map")


def create_image(name, resolution, color_space="Non-Color", float_buffer=True):
    """Create a new image for baking."""
    img = bpy.data.images.new(
        name=name,
        width=resolution,
        height=resolution,
        float_buffer=float_buffer,
        is_data=(color_space == "Non-Color"),
    )
    img.colorspace_settings.name = color_space
    return img


def setup_bake_material(obj, map_type, resolution):
    """Set up a bake-ready material with an ImageTexture node for the target map."""
    # Get or create material
    if obj.data.materials:
        mat = obj.data.materials[0]
    else:
        mat = bpy.data.materials.new(f"{obj.name}_bake")
        obj.data.materials.append(mat)

    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Clear existing nodes
    nodes.clear()

    # Create principled BSDF
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)

    # Create output
    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (300, 0)

    # Link BSDF to output
    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

    # Create image texture node for baking target
    bake_node = nodes.new('ShaderNodeTexImage')
    bake_node.location = (-300, 0)
    bake_node.name = "bake_target"
    bake_node.label = f"bake_{map_type}"

    # Create the image
    img_name = f"{obj.name}_{map_type}"
    color_space = BAKE_MAPS.get(map_type, {}).get("color_space", "Non-Color")
    img = create_image(img_name, resolution, color_space)
    bake_node.image = img

    return mat, bake_node, img


def bake_map(high_poly_objs, low_poly_obj, map_type, resolution):
    """Bake a single PBR map from high-poly to low-poly."""
    if not high_poly_objs:
        print(f"    WARNING: No high-poly source for {map_type} bake")
        return None

    # Setup material
    _mat, _bake_node, img = setup_bake_material(low_poly_obj, map_type, resolution)

    # Select high poly objects as source
    bpy.ops.object.select_all(action='DESELECT')
    for hp in high_poly_objs:
        hp.select_set(True)
        hp.hide_set(False)
        hp.hide_render = False

    # Select low poly as active (target)
    low_poly_obj.select_set(True)
    bpy.context.view_layer.objects.active = low_poly_obj

    # Configure bake settings
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.bake_type = BAKE_MAPS[map_type]["type"]
    scene.cycles.samples = BAKE_SAMPLES

    # Set bake margin
    scene.render.bake.margin = BAKE_MARGIN

    # Configure for selected-to-active
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.cage_extrusion = RAY_DISTANCE
    scene.render.bake.max_ray_distance = RAY_DISTANCE * 2

    # Special handling for diffuse/albedo (no direct/indirect contribution)
    if map_type == "albedo":
        scene.render.bake.use_pass_direct = False
        scene.render.bake.use_pass_indirect = False
        scene.render.bake.use_pass_color = True

    # Perform bake
    try:
        bpy.ops.object.bake(
            type=BAKE_MAPS[map_type]["type"],
            pass_filter=set(),
            filepath="",
            width=resolution,
            height=resolution,
            margin=BAKE_MARGIN,
            use_selected_to_active=True,
            cage_extrusion=RAY_DISTANCE,
            max_ray_distance=RAY_DISTANCE * 2,
            use_clear=True,
        )
    except RuntimeError as e:
        print(f"    ERROR baking {map_type}: {e}")
        return None

    return img


def save_baked_image(img, output_dir, map_type):
    """Save a baked image to disk."""
    if img is None:
        return None

    # Ensure image has pixel data
    img.update()

    # Determine file extension and color depth
    is_data = img.colorspace_settings.name == "Non-Color"
    ext = "png"
    color_depth = "16" if is_data else "8"

    filename = f"{img.name}.{ext}"
    filepath = output_dir / filename

    # Set save settings
    scene = bpy.context.scene
    original_format = scene.render.image_settings.file_format
    original_depth = scene.render.image_settings.color_depth
    original_color_mode = scene.render.image_settings.color_mode

    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_depth = color_depth
    scene.render.image_settings.color_mode = 'RGBA' if is_data else 'RGB'
    scene.render.image_settings.compression = 15  # moderate compression

    img.save_render(str(filepath))

    # Restore settings
    scene.render.image_settings.file_format = original_format
    scene.render.image_settings.color_depth = original_depth
    scene.render.image_settings.color_mode = original_color_mode

    size_bytes = filepath.stat().st_size if filepath.exists() else 0
    print(f"    Saved: {filename} ({size_bytes:,} bytes)")
    return filepath


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    """Execute the full PBR bake pipeline."""
    print("=" * 70)
    print("LUNAR CITY PBR TEXTURE BAKE PIPELINE")
    print("=" * 70)

    # Load manifest
    if not MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found at {MANIFEST_PATH}")
        sys.exit(1)

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    assets = manifest.get("requiredAssets", [])
    print(f"Found {len(assets)} assets in manifest")

    # Create texture output directory
    TEXTURE_OUTPUT.mkdir(parents=True, exist_ok=True)

    # Open master scene to get high-poly sources
    master_blend = SOURCES / "lunar-city-sculpted-master-assets.blend"
    if not master_blend.exists():
        print(f"ERROR: Master scene not found at {master_blend}")
        sys.exit(1)

    print(f"\nOpening master scene: {master_blend}")
    bpy.ops.wm.open_mainfile(filepath=str(master_blend))

    # Process each asset
    bake_results = {}
    for i, asset in enumerate(assets):
        asset_id = asset["id"]
        display_name = asset.get("displayName", asset_id)
        resolution = resolution_for_asset(asset)

        print(f"\n[{i+1}/{len(assets)}] {display_name} ({asset_id}) @ {resolution}px")

        # Find high-poly source collection
        source_coll = bpy.data.collections.get(asset_id)
        if source_coll is None:
            alt_names = [f"Master Asset - {asset_id}", asset_id.replace("-", "_")]
            for alt in alt_names:
                source_coll = bpy.data.collections.get(alt)
                if source_coll:
                    break

        if source_coll is None:
            print("  SKIPPED: Source collection not found")
            continue

        high_poly_objs = [obj for obj in source_coll.objects if obj.type == 'MESH']
        if not high_poly_objs:
            print("  SKIPPED: No mesh objects in source")
            continue

        # Load hero LOD as bake target
        hero_lod_path = LOD_DIR / asset_id / f"{asset_id}_hero.glb"
        if not hero_lod_path.exists():
            print(f"  SKIPPED: Hero LOD not found at {hero_lod_path}")
            print("  Run retopology pipeline first")
            continue

        # Import hero LOD
        bpy.ops.import_scene.gltf(filepath=str(hero_lod_path))
        imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
        if not imported:
            print("  SKIPPED: Failed to import hero LOD")
            continue

        low_poly_obj = imported[0]
        low_poly_obj.name = f"{asset_id}_bake_target"

        # Ensure UV map
        ensure_uv_map(low_poly_obj)

        # Create texture output directory for this asset
        asset_texture_dir = TEXTURE_OUTPUT / asset_id
        asset_texture_dir.mkdir(parents=True, exist_ok=True)

        # Bake each map type
        asset_bakes = {}
        for map_type in BAKE_MAPS:
            print(f"  Baking {map_type}...")
            img = bake_map(high_poly_objs, low_poly_obj, map_type, resolution)
            filepath = save_baked_image(img, asset_texture_dir, map_type)

            if filepath:
                rel_path = f"lunar-city/master-assets/textures/{asset_id}/{filepath.name}"
                asset_bakes[map_type] = {
                    "filepath": rel_path,
                    "resolution": resolution,
                    "bytes": filepath.stat().st_size,
                }

        bake_results[asset_id] = asset_bakes

        # Clean up imported LOD
        bpy.ops.object.select_all(action='DESELECT')
        low_poly_obj.select_set(True)
        bpy.ops.object.delete()

    # Update manifest
    print("\n" + "=" * 70)
    print("UPDATING MANIFEST WITH TEXTURE PATHS")
    print("=" * 70)

    for asset in manifest.get("requiredAssets", []):
        asset_id = asset["id"]
        if bake_results.get(asset_id):
            asset["pbrTextures"] = bake_results[asset_id]
            if asset.get("status") == "retopology_lods_complete":
                asset["status"] = "pbr_baked"

    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest updated: {MANIFEST_PATH}")

    # Write bake summary
    summary_path = TEXTURE_OUTPUT / "bake-summary.json"
    with open(summary_path, 'w') as f:
        json.dump({
            "generated": str(Path.cwd()),
            "assetCount": len(bake_results),
            "maps": list(BAKE_MAPS.keys()),
            "assets": {
                aid: {mt: data.get("resolution", 0) for mt, data in maps.items()}
                for aid, maps in bake_results.items()
            }
        }, f, indent=2)
    print(f"Bake summary: {summary_path}")

    print("\n" + "=" * 70)
    print(f"BAKE COMPLETE: {len(bake_results)} assets baked")
    print("=" * 70)


if __name__ == "__main__":
    run_pipeline()
