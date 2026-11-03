#!/usr/bin/env python3
"""Master pipeline orchestrator for Lunar City asset production.

Runs the full pipeline in order:
  1. Retopology + LOD generation
  2. PBR texture baking
  3. Collision mesh generation
  4. Rigging + animation

Usage:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_pipeline.py
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_pipeline.py -- --stage=retopology
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_pipeline.py -- --stage=bake
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_pipeline.py -- --stage=collision
  /Applications/Blender.app/Contents/MacOS/Blender --background --python lunar_city_pipeline.py -- --stage=rigging
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = ROOT / "apps" / "desktop" / "scripts" / "lunar-city"

STAGES = ["retopology", "bake", "collision", "rigging"]

STAGE_SCRIPTS = {
    "retopology": "lunar_city_retopology_lod.py",
    "bake": "lunar_city_pbr_bake.py",
    "collision": "lunar_city_collision.py",
    "rigging": "lunar_city_rigging.py",
}


def run_stage(stage):
    """Run a single pipeline stage."""
    script_name = STAGE_SCRIPTS.get(stage)
    if not script_name:
        print(f"ERROR: Unknown stage '{stage}'")
        return False

    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        print(f"ERROR: Script not found: {script_path}")
        return False

    print(f"\n{'='*70}")
    print(f"RUNNING STAGE: {stage}")
    print(f"Script: {script_path}")
    print(f"{'='*70}\n")

    # Execute the script
    exec_globals = {"__name__": "__main__", "__file__": str(script_path)}
    with open(script_path) as f:
        code = f.read()

    try:
        exec(compile(code, str(script_path), 'exec'), exec_globals)
        return True
    except SystemExit as e:
        if e.code != 0 and e.code is not None:
            print(f"Stage {stage} exited with code {e.code}")
            return False
        return True
    except Exception as e:  # health: allow BLE001 -- stage boundary: prints the traceback and reports failure so later stages can be skipped
        print(f"ERROR in stage {stage}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Parse arguments and run requested stages."""
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []

    # Determine which stages to run
    stages_to_run = STAGES  # default: all

    for arg in args:
        if arg.startswith("--stage="):
            stage = arg.split("=")[1]
            if stage in STAGES:
                stages_to_run = [stage]
            else:
                print(f"Unknown stage '{stage}'. Available: {STAGES}")
                sys.exit(1)

    print("=" * 70)
    print("LUNAR CITY MASTER PIPELINE")
    print(f"Stages: {stages_to_run}")
    print("=" * 70)

    results = {}
    for stage in stages_to_run:
        success = run_stage(stage)
        results[stage] = success
        if not success:
            print(f"\nPipeline stopped at stage '{stage}' (failure)")
            break

    print("\n" + "=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    for stage, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"  {stage:15s} {status}")

    all_passed = all(results.values())
    print(f"\nOverall: {'ALL STAGES PASSED' if all_passed else 'SOME STAGES FAILED'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
