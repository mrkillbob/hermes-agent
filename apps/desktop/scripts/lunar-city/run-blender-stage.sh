#!/usr/bin/env bash
set -euo pipefail

blender_bin="${LUNAR_CITY_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
if [[ ! -x "$blender_bin" ]]; then
  echo "Lunar City Blender capture: executable not found: $blender_bin" >&2
  echo "Set LUNAR_CITY_BLENDER to a Blender 5.x executable." >&2
  exit 127
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../../../.." && pwd)"
stage_script="$script_dir/blender_stage.py"
gpu_backend="${LUNAR_CITY_BLENDER_GPU_BACKEND:-}"
blender_command=("$blender_bin" --background --factory-startup)
if [[ -n "$gpu_backend" ]]; then
  blender_command+=(--gpu-backend "$gpu_backend")
fi

set +e
"${blender_command[@]}" --python "$stage_script" -- "$@"
status=$?
set -e

if [[ "$status" -eq 139 ]]; then
  output_blend=""
  for ((index = 1; index <= $#; index++)); do
    if [[ "${!index}" == "--output" && $((index + 1)) -le $# ]]; then
      next_index=$((index + 1))
      output_blend="${!next_index}"
      break
    fi
  done
  if [[ -n "$output_blend" ]]; then
    failure_receipt="${output_blend%.*}.failure.json"
    printf '{"status":"blocked","reason":"blender-metal-startup","exitCode":139,"gpuBackend":"%s","host":"%s","message":"Blender terminated before Python initialization; rerun with host permissions or a Blender build with a working Metal driver."}\n' \
      "${gpu_backend:-default}" "$(uname -s)" > "$failure_receipt"
    echo "Wrote diagnostic receipt: $failure_receipt" >&2
  fi
  echo >&2
  echo "Lunar City Blender capture could not initialize ${gpu_backend:-the default GPU backend} (SIGSEGV before Python)." >&2
  echo "Run this command with host permissions, or use a newer Blender build:" >&2
  echo "  $repo_root/apps/desktop/scripts/lunar-city/run-blender-stage.sh --render-engine auto --output /tmp/lunar-city-stage.blend --render-output /tmp/lunar-city-stage.png" >&2
  echo "The authored scene remains unchanged; this is a Blender/macOS GPU startup boundary." >&2
fi
exit "$status"
