#!/usr/bin/env bash
set -euo pipefail

blender_bin="${LUNAR_CITY_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
if [[ ! -x "$blender_bin" ]]; then
  echo "Lunar City one-shot rebuild: executable not found: $blender_bin" >&2
  exit 127
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
args=(--background --factory-startup --python "$script_dir/blender_rebuild.py" --)
"$blender_bin" "${args[@]}" "$@"
