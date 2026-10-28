"""Rebuild the private local SVGR artifact from the authentic published archive.

Usage: python rebuild-svgr-plugin-svgo.py /path/to/plugin-svgo-8.1.0.tgz
No network, package execution, installation hooks, or version substitution.
"""
import base64
import gzip
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
buffer = io.BytesIO()
with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0, filename='') as compressed:
    with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted(files.items()):
            item = tarfile.TarInfo('package/' + name)
            item.size = len(data)
            item.mtime = 0
            item.uid = item.gid = 0
            item.uname = item.gname = ''
            item.mode = 0o644
            tar.addfile(item, io.BytesIO(data))
output = buffer.getvalue()
assert hashlib.sha256(output).hexdigest() == provenance['local_sha256']
assert 'sha512-' + base64.b64encode(hashlib.sha512(output).digest()).decode() == provenance['local_integrity']
target = root / provenance['local_artifact']
target.write_bytes(output)
print(target.name + ': reproduced exact artifact and integrity')
