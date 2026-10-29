"""Reproduce the private local SVGR directory from the authentic published archive.

Usage: python rebuild-svgr-plugin-svgo.py /external/original.tgz /external/new-directory
No network, package execution, installation hooks, or version substitution.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

root = Path(__file__).resolve().parent
provenance = json.loads((root / 'svgr-plugin-svgo-provenance.json').read_text())
archive = Path(sys.argv[1]).read_bytes()
assert 'sha512-' + base64.b64encode(hashlib.sha512(archive).digest()).decode() == provenance['original_integrity']
files = {}
with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
    for item in tar.getmembers():
        path = Path(item.name)
        assert path.parts[0] == 'package' and not path.is_absolute() and '..' not in path.parts
        if item.isdir():
            continue
        assert item.isfile(), 'non-regular upstream package member'
        name = str(path.relative_to('package'))
        assert name not in files, 'duplicate archive member'
        files[name] = tar.extractfile(item).read()
manifest = json.loads(files['package.json'])
assert manifest['name'] == '@svgr/plugin-svgo' and manifest['version'] == '8.1.0'
assert manifest['dependencies'].pop('deepmerge') == '^4.3.1'
assert manifest.pop('gitHead') == provenance['original_git_head']
manifest['private'] = True
files['package.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
assert {name: hashlib.sha256(data).hexdigest() for name, data in files.items()} == provenance['files']
target = Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=False)
for name, data in sorted(files.items()):
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
print(str(target) + ': reproduced exact directory and per-file hashes')
