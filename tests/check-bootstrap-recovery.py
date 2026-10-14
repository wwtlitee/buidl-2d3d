"""Bootstrap partial-runtime recovery checks; no actual downloads. v0.5.0."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import threading
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class BootstrapRecoveryTests(unittest.TestCase):
    def test_corrupt_executable_is_not_reported_ready(self):
        shell = shutil.which("pwsh")
        self.assertTrue(shell)
        with tempfile.TemporaryDirectory(prefix="build-something-bootstrap-") as directory:
            root = Path(directory)
            for relative in ("runtime/core/.venv/Scripts/python.exe", "runtime/desktop/.venv/Scripts/python.exe", "runtime/desktop/.venv/Scripts/pythonw.exe"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"incomplete executable")
            result = subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts/bootstrap-runtime.ps1"),
                                     "-Root", str(root), "-CheckOnly"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            status = json.loads(result.stdout)
            self.assertFalse(status["coreReady"], "A partial private Python must be repaired before launching native adapters")
            self.assertFalse(status["desktopReady"])

    def test_legacy_model_python_cannot_skip_private_core_preparation(self):
        source = (ROOT / "scripts/bootstrap-runtime.ps1").read_text(encoding="utf-8")
        line = next(line for line in source.splitlines() if "$needsCore=" in line)
        self.assertNotIn("$legacyPython", line, "First startup must prepare the private runtime used by native model launchers")

    def test_invalid_private_environment_is_preserved_before_recreation(self):
        script = ROOT / "scripts/bootstrap-runtime.ps1"
        source = script.read_text(encoding="utf-8")
        self.assertIn("function Ensure-PrivateVenv", source, "Partial private environments need a repair path")
        with tempfile.TemporaryDirectory(prefix="build-something-venv-repair-") as directory:
            root = Path(directory)
            environment = root / "runtime/core/.venv"
            interpreter = environment / "Scripts/python.exe"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"incomplete interpreter")
            fixture = root / "repair.ps1"
            script_literal = str(script).replace("'", "''")
            root_literal = str(root).replace("'", "''")
            fixture.write_text("$ErrorActionPreference='Stop'\n$Root='" + root_literal + "'\n"
                "$ast=[System.Management.Automation.Language.Parser]::ParseFile('" + script_literal + "',[ref]$null,[ref]$null)\n"
                "$names=@('Test-RuntimePython','Ensure-PrivateVenv')\n"
                "foreach($function in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $names},$true)){Invoke-Expression $function.Extent.Text}\n"
                "function Fake-Uv {param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments) $destination=[string]$Arguments[-1]; New-Item -ItemType Directory -Path (Join-Path $destination 'Scripts') -Force|Out-Null; [IO.File]::WriteAllText((Join-Path $destination 'Scripts/python.exe'),'recreated'); $global:LASTEXITCODE=0}\n"
                "$venv=Join-Path $Root 'runtime/core/.venv'\n"
                "Ensure-PrivateVenv 'Fake-Uv' '3.11.14' $venv (Join-Path $venv 'Scripts/python.exe')\n", encoding="utf-8")
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(interpreter.read_bytes(), b"recreated")
            backups = list((root / "runtime/core").glob(".venv.incomplete-*/Scripts/python.exe"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"incomplete interpreter")

    def test_corrupt_cached_archive_is_preserved_and_redownloaded(self):
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w") as bundle:
            bundle.writestr("uv.exe", "fixture runtime component")
        content = payload.getvalue()
        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            def log_message(self, *_):pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="build-something-archive-repair-") as directory:
                root = Path(directory)
                archive = root / "cache/bootstrap/uv-fixture.zip"
                archive.parent.mkdir(parents=True)
                archive.write_bytes(b"corrupt cached runtime")
                fixture = root / "archive-repair.ps1"
                source_literal = str(ROOT / "scripts/bootstrap-runtime.ps1").replace("'", "''")
                root_literal = str(root).replace("'", "''")
                fixture.write_text("$ErrorActionPreference='Stop'\n$Root='" + root_literal + "'\nAdd-Type -AssemblyName System.IO.Compression.FileSystem\n"
                    "$ast=[System.Management.Automation.Language.Parser]::ParseFile('" + source_literal + "',[ref]$null,[ref]$null)\n"
                    "$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Install-VerifiedArchive'},$true)\nInvoke-Expression $function.Extent.Text\n"
                    "$item=[pscustomobject]@{version='fixture';size=" + str(len(content)) + ";sha256='" + hashlib.sha256(content).hexdigest() + "';url='http://127.0.0.1:" + str(server.server_port) + "/fixture'}\n"
                    "Install-VerifiedArchive $item 'uv' (Join-Path $Root 'runtime/tools/uv')\n", encoding="utf-8")
                result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / "runtime/tools/uv/uv.exe").read_text(), "fixture runtime component")
                backups = list(archive.parent.glob("uv-fixture.zip.invalid-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), b"corrupt cached runtime")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_missing_windowed_interpreter_recreates_desktop_environment(self):
        script = ROOT / "scripts/bootstrap-runtime.ps1"
        self.assertTrue("RequireWindowed" in script.read_text(encoding="utf-8"), "Desktop recovery must restore a missing pythonw launcher")
        with tempfile.TemporaryDirectory(prefix="build-something-desktop-repair-") as directory:
            root = Path(directory)
            environment = root / "runtime/desktop/.venv"
            interpreter = environment / "Scripts/python.exe"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b"healthy console fixture")
            fixture = root / "repair.ps1"
            fixture.write_text("$ErrorActionPreference='Stop'\n$Root='" + str(root).replace("'", "''") + "'\n"
                "$ast=[System.Management.Automation.Language.Parser]::ParseFile('" + str(script).replace("'", "''") + "',[ref]$null,[ref]$null)\n"
                "$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Ensure-PrivateVenv'},$true)\nInvoke-Expression $function.Extent.Text\n"
                "function Test-RuntimePython {return $true}\n"
                "function Fake-Uv {param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Arguments) $destination=[string]$Arguments[-1]; New-Item -ItemType Directory -Path (Join-Path $destination 'Scripts') -Force|Out-Null; foreach($name in @('python.exe','pythonw.exe')){[IO.File]::WriteAllText((Join-Path $destination ('Scripts/'+$name)),'recreated')}; $global:LASTEXITCODE=0}\n"
                "$venv=Join-Path $Root 'runtime/desktop/.venv'\n"
                "Ensure-PrivateVenv 'Fake-Uv' '3.11.14' $venv (Join-Path $venv 'Scripts/python.exe') -RequireWindowed\n", encoding="utf-8")
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((environment / "Scripts/pythonw.exe").read_bytes(), b"recreated")
            backups = list((root / "runtime/desktop").glob(".venv.incomplete-*/Scripts/python.exe"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"healthy console fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
