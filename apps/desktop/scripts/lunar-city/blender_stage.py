"""Stage Lunar City assets in Blender for authored iteration.

Run from Blender's Python environment (the ``--`` separates Blender flags):

    blender --background --python blender_stage.py -- \
      --output /tmp/lunar-city-stage.blend \
      --polyhaven-dir /path/to/quarantined/polyhaven

Poly Haven files are optional and are never downloaded or copied by this
script.  The directory should contain files that were downloaded and reviewed
using ``import-open-asset-pack.mjs`` first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import bpy  # type: ignore


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ASSET_ROOT = ROOT / "apps" / "desktop" / "public" / "lunar-city" / "v2" / "models"


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


def import_model(path: Path, target: bpy.types.Collection) -> int:
    before = set(bpy.data.objects)
    suffix = path.suffix.lower()
    if suffix == ".glb" or suffix == ".gltf":
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    else:
        return 0
    imported = [obj for obj in bpy.data.objects if obj not in before]
    for obj in imported:
        move_to(obj, target)
    return len(imported)


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
    parser.add_argument("--polyhaven-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("/tmp/lunar-city-stage.blend"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    root = collection("LUNAR_CITY")
    collection("LUNAR_CITY::BUILDINGS", root)
    collection("LUNAR_CITY::WORKERS", root)
    collection("LUNAR_CITY::TERRAIN", root)
    external = collection("POLYHAVEN_BENCHMARK", root)

    palette = {
        "LUNAR_CITY::PALETTE::MOON": make_material("Lunar Moon", (0.09, 0.12, 0.18, 1), metallic=0.15),
        "LUNAR_CITY::PALETTE::TRIM": make_material("Lunar Trim", (0.22, 0.30, 0.38, 1), metallic=0.65),
        "LUNAR_CITY::PALETTE::CYAN": make_material("Lunar Cyan", (0.03, 0.45, 0.62, 1), metallic=0.35),
        "LUNAR_CITY::PALETTE::VIOLET": make_material("Lunar Violet", (0.35, 0.08, 0.55, 1), metallic=0.2),
        "LUNAR_CITY::PALETTE::WARM": make_material("Lunar Warm", (0.72, 0.30, 0.08, 1), metallic=0.2),
    }
    for name, mat in palette.items():
        mat["lunarCityRole"] = name.rsplit("::", 1)[-1].lower()

    counts = {"models": 0, "polyhavenModels": 0, "polyhavenTextures": 0}
    for path in sorted(args.asset_root.rglob("*")):
        if path.suffix.lower() not in {".glb", ".gltf", ".obj", ".fbx"}:
            continue
        stem = path.stem.lower()
        target_name = "LUNAR_CITY::WORKERS" if stem in {"workers", "leaders"} else "LUNAR_CITY::TERRAIN" if stem in {"terrain", "navigation"} else "LUNAR_CITY::BUILDINGS"
        counts["models"] += import_model(path, bpy.data.collections[target_name])

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
    scene["lunarCityStaging"] = "asset-neutral"
    scene["polyhavenReviewRequired"] = bool(receipt)
    scene.world.color = (0.005, 0.008, 0.02)
    bpy.ops.object.camera_add(location=(68, -68, 58))
    camera = bpy.context.object
    camera.name = "LunarCity_StagingCamera"
    scene.camera = camera
    camera.data.lens = 48
    bpy.ops.object.light_add(type="AREA", location=(12, -18, 46))
    key = bpy.context.object
    key.name = "LunarCity_KeyLight"
    key.data.energy = 1800
    key.data.shape = "DISK"
    key.data.size = 40

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output))
    receipt_path = args.output.with_suffix(".staging-receipt.json")
    receipt_path.write_text(json.dumps({"assetRoot": str(args.asset_root), "counts": counts, "polyhaven": receipt, "reviewRequired": bool(receipt)}, indent=2) + "\n")
    print(json.dumps({"blend": str(args.output), "receipt": str(receipt_path), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
