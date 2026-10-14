"""v0.9.1b: compile only in isolated paths; never start the product GUI."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = Path(os.environ.get("SystemRoot", "C:/Windows"))
POWERSHELL = WINDOWS / "System32/WindowsPowerShell/v1.0/powershell.exe"
CSCRIPT = WINDOWS / "System32/cscript.exe"
ENTRY = "启动造点啥.vbs"
APP = "造点啥.exe"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Requires Windows PowerShell 5.1 and .NET Framework")
class SourceLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="factory source 中文 ' ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "clone space 中文 ' directory"
        (self.root / "scripts").mkdir(parents=True)
        for name in ("start-source-factory.ps1", "build-desktop-launcher.ps1", "desktop_launcher.cs"):
            # Exercise GitHub-style LF files even if this checkout uses CRLF.
            (self.root / "scripts" / name).write_text((ROOT / "scripts" / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        (self.root / ENTRY).write_text((ROOT / ENTRY).read_text(encoding="ascii"), encoding="ascii", newline="\n")
        self.version("0.9.1b")

    def version(self, value):
        (self.root / "VERSION.json").write_text(json.dumps({"version": value}) + "\n", encoding="utf-8")

    def invoke(self, vbs=False, success=True):
        if vbs:
            args = [str(CSCRIPT), "//B", "//Nologo", str(self.root / ENTRY), "--prepare-only"]
        else:
            args = [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(self.root / "scripts/start-source-factory.ps1"), "-PrepareOnly"]
        run = subprocess.run(args, cwd=self.root, capture_output=True, encoding="utf-8", errors="replace",
                             timeout=150, creationflags=subprocess.CREATE_NO_WINDOW)
        if success:
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        else:
            self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        if vbs:
            receipts = sorted((self.root / "logs/desktop-launcher/source").glob("*.json"))
            self.assertTrue(receipts, run.stdout + run.stderr)
            result = json.loads(receipts[-1].read_text(encoding="utf-8"))
        else:
            result = json.loads(run.stdout)
        self.assertEqual(result["ok"], success)
        self.assertTrue(result["prepareOnly"])
        self.assertNotIn("launchedPid", result)
        self.assertTrue(Path(result["logPath"]).is_file())
        return result

    def assert_valid_build(self, result, version="0.9.1b"):
        binary = (self.root / APP).read_bytes()
        self.assertEqual(binary[:2], b"MZ")
        offset = struct.unpack_from("<I", binary, 0x3C)[0]
        self.assertEqual(binary[offset:offset + 4], b"PE\0\0")
        self.assertEqual(struct.unpack_from("<H", binary, offset + 24 + 68)[0], 2)
        receipt = json.loads((self.root / (APP + ".build.json")).read_text(encoding="utf-8"))
        self.assertEqual(result["version"], version)
        self.assertEqual(result["exeProductVersion"], version)
        self.assertEqual(receipt["version"], version)
        for key, path in (("sha256", self.root / APP), ("sourceSha256", self.root / "scripts/desktop_launcher.cs"),
                          ("versionSha256", self.root / "VERSION.json")):
            self.assertEqual(receipt[key], sha256(path))
            self.assertEqual(result[key], sha256(path))

    def test_vbs_first_build_then_reuse_in_unicode_space_apostrophe_path(self):
        (ROOT / ENTRY).read_bytes().decode("ascii")
        (ROOT / "scripts/start-source-factory.ps1").read_bytes().decode("ascii")
        first = self.invoke(vbs=True)
        self.assertEqual(first["action"], "built")
        self.assert_valid_build(first)
        executable = self.root / APP
        original = (sha256(executable), executable.stat().st_mtime_ns)
        second = self.invoke(vbs=True)
        self.assertEqual(second["action"], "reused")
        self.assertEqual((sha256(executable), executable.stat().st_mtime_ns), original)
        self.assertFalse((self.root / "logs/desktop-launcher/builds").exists())

    def test_tampered_binary_version_and_source_rebuild(self):
        self.invoke()
        with (self.root / APP).open("ab") as stream:
            stream.write(b"tampered")
        binary_result = self.invoke()
        self.assertEqual(binary_result["action"], "built")
        self.assert_valid_build(binary_result)
        self.version("0.9.1c")
        version_result = self.invoke()
        self.assertEqual(version_result["action"], "built")
        self.assert_valid_build(version_result, "0.9.1c")
        with (self.root / "scripts/desktop_launcher.cs").open("a", encoding="utf-8") as stream:
            stream.write("\n// source cache invalidation test\n")
        source_result = self.invoke()
        self.assertEqual(source_result["action"], "built")
        self.assert_valid_build(source_result, "0.9.1c")

    def test_missing_source_fails_with_specific_persistent_log(self):
        self.invoke()
        (self.root / "scripts/desktop_launcher.cs").unlink()
        result = self.invoke(success=False)
        self.assertIn("desktop_launcher.cs", result["error"])
        self.assertIn("Required source file is missing", Path(result["logPath"]).read_text(encoding="utf-8"))

    def test_concurrent_first_launch_builds_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.invoke(), range(2)))
        self.assertEqual(sorted(result["action"] for result in results), ["built", "reused"])
        self.assertFalse((self.root / "logs/desktop-launcher/builds").exists())
        for result in results:
            self.assert_valid_build(result)

    def test_corrupt_receipt_rebuilds_and_invalid_version_fails(self):
        self.invoke()
        (self.root / (APP + ".build.json")).write_text("not json", encoding="utf-8")
        rebuilt = self.invoke()
        self.assertEqual(rebuilt["action"], "built")
        self.assert_valid_build(rebuilt)
        self.version("not-a-version")
        result = self.invoke(success=False)
        self.assertIn("invalid product version", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
