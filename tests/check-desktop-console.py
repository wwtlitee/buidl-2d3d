"""CPU-only desktop startup regression: a pythonw-named console shim and a real GUI.

All processes, windows and logs belong to a temporary fixture. No application,
service, model, GPU, network, or user browser is started.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = argparse.Namespace(baseline_zip=None, output=None)
USER = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
if USER:
    USER.IsWindowVisible.argtypes = [wintypes.HWND]
    USER.IsWindowVisible.restype = wintypes.BOOL

FIXTURE = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
class Fixture {
    [DllImport("kernel32.dll")] static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    static string statusPath;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static void WriteJson(string path, object value) {
        File.WriteAllText(path, Json.Serialize(value), new UTF8Encoding(false));
    }
    [STAThread] static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        for (int i = 0; i + 1 < args.Length; i++)
            if (args[i] == "--startup-status") statusPath = args[i + 1];
        IntPtr console = GetConsoleWindow();
        var observation = new Dictionary<string, object> {
            {"pid", System.Diagnostics.Process.GetCurrentProcess().Id},
            {"consoleHwnd", console.ToInt64()},
            {"consoleVisible", console != IntPtr.Zero && IsWindowVisible(console)}
        };
        string mode = File.ReadAllText("fixture-mode.txt").Trim();
        Console.Out.WriteLine("fixture-stdout-start");
        Console.Error.WriteLine("fixture-stderr-start");
        if (mode != "ready") {
            WriteJson("fixture-observed.json", observation);
            if (mode == "failed") WriteJson(statusPath, new {
                status = "failed", error = "fixture expected startup failure"
            });
            Console.Error.WriteLine("fixture-exit-seven");
            return 7;
        }
        // More than pipe capacity on each stream detects redirection/drain regressions.
        Console.Out.WriteLine(new string('O', 131072));
        Console.Error.WriteLine(new string('E', 131072));
        var form = new Form { Text = "Isolated desktop startup test", Width = 320, Height = 130 };
        var timer = new Timer { Interval = 1800 };
        form.Shown += delegate {
            observation["guiHwnd"] = form.Handle.ToInt64();
            observation["guiVisible"] = IsWindowVisible(form.Handle);
            WriteJson("fixture-observed.json", observation);
            WriteJson(statusPath, new {
                status = "ready", version = "fixture", pid = System.Diagnostics.Process.GetCurrentProcess().Id,
                visible = IsWindowVisible(form.Handle), minimized = false
            });
            timer.Start();
        };
        timer.Tick += delegate { timer.Stop(); form.Close(); };
        Application.Run(form);
        // These arrive after startup has consumed the ready receipt.
        Console.Out.Write("fixture-stdout-tail");
        Console.Error.Write("fixture-stderr-tail");
        return 0;
    }
}
'''


def ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt", "Windows-only window regression")
class DesktopConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # NativeLauncher.ResolveShell uses the prepared PowerShell 7 for opening.
        # Windows PowerShell 5.1 only runs the separate ASCII bootstrap script.
        shell = ROOT / "runtime/tools/pwsh/pwsh.exe"
        if not shell.exists():
            shell = Path(shutil.which("pwsh")) if shutil.which("pwsh") else None
        if not shell:
            raise unittest.SkipTest("Prepared PowerShell 7 is unavailable")
        cls.shells = [shell]
        cls.compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not cls.compiler.exists():
            raise unittest.SkipTest(".NET Framework C# compiler is unavailable")
        cls.build = tempfile.TemporaryDirectory(prefix="factory-console-build-")
        source = Path(cls.build.name) / "fixture.cs"
        cls.fixture = Path(cls.build.name) / "pythonw.exe"
        source.write_text(FIXTURE, encoding="utf-8")
        result = subprocess.run(
            [str(cls.compiler), "/nologo", "/target:exe", "/out:" + str(cls.fixture),
             "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
             "/reference:System.Web.Extensions.dll", str(source)],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
        binary = cls.fixture.read_bytes()
        header = struct.unpack_from("<I", binary, 0x3C)[0]
        assert struct.unpack_from("<H", binary, header + 24 + 68)[0] == 3, "Fixture must be a console executable"
        cls.receipts = []

    @classmethod
    def tearDownClass(cls):
        if OPTIONS.output:
            output = Path(OPTIONS.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({
                "mode": "isolated-real-console-shim-and-GUI", "productionStarted": False,
                "boundary": "Complete opening script tested under its prepared PowerShell 7, not Windows PowerShell 5.1",
                "sourceSha256": hashlib.sha256((ROOT / "scripts/start-desktop-factory.ps1").read_bytes()).hexdigest(),
                "receipts": cls.receipts,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        cls.build.cleanup()

    def run_fixture(self, script: bytes, shell: Path, mode="ready", baseline=False):
        with tempfile.TemporaryDirectory(prefix="factory console 中文 ") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            runtime = root / "runtime/desktop/.venv/Scripts"
            runtime.mkdir(parents=True)
            shutil.copyfile(self.fixture, runtime / "pythonw.exe")
            startup = root / "scripts/start-desktop-factory.ps1"
            startup.write_bytes(script)
            (root / "fixture-mode.txt").write_text(mode, encoding="ascii")
            # The GUI fixture ignores the app-script argument; no real Python/app is run.
            command = ("$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
                       "try{& " + ps_quote(startup) + ";exit 0}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}")
            process = subprocess.Popen(
                [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-EncodedCommand", base64.b64encode(command.encode("utf-16-le")).decode()],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                observation_path = root / "fixture-observed.json"
                deadline = time.monotonic() + 10
                observed = None
                while time.monotonic() < deadline:
                    try:
                        observed = json.loads(observation_path.read_text(encoding="utf-8"))
                        break
                    except (OSError, ValueError):
                        if process.poll() is not None:
                            break
                        time.sleep(0.02)
                if observed and mode == "ready":
                    self.assertTrue(observed["guiVisible"])
                    self.assertTrue(USER.IsWindowVisible(observed["guiHwnd"]), "Actual GUI must be visible at ready")
                stdout, stderr = process.communicate(timeout=12)
                self.assertIsNotNone(observed, stderr.decode("utf-8", errors="replace"))
                if mode == "ready":
                    self.assertEqual(process.returncode, 0, stderr.decode("utf-8", errors="replace"))
                else:
                    self.assertNotEqual(process.returncode, 0)
                    if mode == "failed":
                        self.assertIn(b"fixture expected startup failure", stderr)
                if baseline:
                    self.assertTrue(observed["consoleHwnd"])
                    self.assertTrue(observed["consoleVisible"], "Delivered Normal startup must reproduce the console")
                else:
                    self.assertEqual(observed["consoleHwnd"], 0)
                    self.assertFalse(observed["consoleVisible"])
                deadline = time.monotonic() + 8
                while True:
                    logs = {p.name: p.read_bytes() for p in (root / "logs/desktop").glob("*.log")}
                    output = next((v for k, v in logs.items() if not k.endswith("-error.log")), b"")
                    error = next((v for k, v in logs.items() if k.endswith("-error.log")), b"")
                    if mode != "ready" or (b"fixture-stdout-tail" in output and b"fixture-stderr-tail" in error):
                        break
                    if time.monotonic() > deadline:
                        self.fail("Logs after ready or their unterminated final lines were lost")
                    time.sleep(0.05)
                self.assertIn(b"fixture-stdout-start", output)
                self.assertIn(b"fixture-stderr-start", error)
                if mode == "ready":
                    self.assertIn(b"O" * 131072, output)
                    self.assertIn(b"E" * 131072, error)
                else:
                    self.assertIn(b"fixture-exit-seven", error)
                self.receipts.append({"shell": str(shell), "mode": mode, "baseline": baseline,
                                      "exitCode": process.returncode, "observed": observed,
                                      "stdoutBytes": len(output), "stderrBytes": len(error),
                                      "startupStderr": stderr.decode("utf-8", errors="replace")})
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if observed and observed.get("guiHwnd"):
                    deadline = time.monotonic() + 4
                    while USER.IsWindowVisible(observed["guiHwnd"]) and time.monotonic() < deadline:
                        time.sleep(0.05)
                # Every fixture has a bounded lifetime; allow its file handles to close.
                time.sleep(0.2)

    def test_console_shim_keeps_real_gui_visible_and_all_logs(self):
        for shell in self.shells:
            with self.subTest(shell=str(shell)):
                self.run_fixture((ROOT / "scripts/start-desktop-factory.ps1").read_bytes(), shell)

    def test_failed_receipt_keeps_error_and_does_not_claim_ready(self):
        for shell in self.shells:
            with self.subTest(shell=str(shell)):
                self.run_fixture((ROOT / "scripts/start-desktop-factory.ps1").read_bytes(), shell, "failed")

    def test_exit_without_receipt_is_reported_as_startup_failure(self):
        for shell in self.shells:
            with self.subTest(shell=str(shell)):
                self.run_fixture((ROOT / "scripts/start-desktop-factory.ps1").read_bytes(), shell, "exit")

    def test_delivered_baseline_reproduces_visible_console(self):
        if not OPTIONS.baseline_zip:
            self.skipTest("Pass --baseline-zip for a comparison against the delivered package")
        with zipfile.ZipFile(OPTIONS.baseline_zip) as archive:
            matches = [name for name in archive.namelist() if name.endswith("/scripts/start-desktop-factory.ps1")]
            self.assertEqual(len(matches), 1)
            script = archive.read(matches[0])
        for shell in self.shells:
            with self.subTest(shell=str(shell)):
                self.run_fixture(script, shell, baseline=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--baseline-zip")
    parser.add_argument("--output")
    OPTIONS, remaining = parser.parse_known_args()
    unittest.main(argv=[__file__, *remaining], verbosity=2)
