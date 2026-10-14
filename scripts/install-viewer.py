"""Install a pinned offline GLB viewer, verify npm integrity. v0.1.0 / Unclecow."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "4.3.1"
cache = ROOT / "cache/frontend"
cache.mkdir(parents=True, exist_ok=True)
metadata = cache / f"model-viewer-{VERSION}.json"
subprocess.run(["curl.exe", "-fLSs", "--retry", "3", "--max-time", "120",
                f"https://registry.npmjs.org/@google%2Fmodel-viewer/{VERSION}", "-o", str(metadata)], check=True)
package = json.loads(metadata.read_text(encoding="utf-8"))
archive = cache / f"model-viewer-{VERSION}.tgz"
subprocess.run(["curl.exe", "-fLSs", "--retry", "3", "--max-time", "240",
                package["dist"]["tarball"], "-o", str(archive)], check=True)
algorithm, expected = package["dist"]["integrity"].split("-", 1)
actual = base64.b64encode(hashlib.new(algorithm, archive.read_bytes()).digest()).decode()
if actual != expected:
    raise RuntimeError("npm archive integrity mismatch")
destination = ROOT / "visual/vendor"
destination.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive) as tar:
    for original, filename in [("package/dist/model-viewer.min.js", "model-viewer.min.js"),
                                ("package/LICENSE", "model-viewer.LICENSE")]:
        member = tar.getmember(original)
        if not member.isfile():
            raise RuntimeError("Unexpected package member")
        data = tar.extractfile(member).read()
        (destination / filename).write_bytes(data)
record = {"factoryVersion": "0.1.0", "package": "@google/model-viewer", "version": VERSION,
          "source": package["dist"]["tarball"], "integrity": package["dist"]["integrity"],
          "sha256": hashlib.sha256((destination / "model-viewer.min.js").read_bytes()).hexdigest()}
(ROOT / "config/frontend-dependencies.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record), flush=True)
