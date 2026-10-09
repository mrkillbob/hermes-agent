import hashlib,json
from pathlib import Path
m=json.loads(Path(".ci/portable-reconcile/manifest.json").read_text(encoding="utf-8"))
for p,h in m.items():
 assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h,p
print("Verified",len(m),"frozen source/lock files")
