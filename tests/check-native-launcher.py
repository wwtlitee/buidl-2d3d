"""Native EXE startup regressions, using temporary scripts only. v0.6.0 / Unclecow.

No production service, model download, browser, or existing application is used.
The tiny startup form and PowerShell children belong to each temporary fixture.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_VERSION = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))["version"]
FILE_VERSION = re.fullmatch(r"(\d+\.\d+\.\d+)[a-z]*", PRODUCT_VERSION).group(1) + ".0"
KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
USER = ctypes.WinDLL("user32", use_last_error=True)
KERNEL.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL.OpenProcess.restype = wintypes.HANDLE
KERNEL.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
KERNEL.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
USER.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER.IsWindowVisible.argtypes = [wintypes.HWND]
USER.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
USER.EnumWindows.argtypes = [ENUM, wintypes.LPARAM]


def windows(pid: int):
    found = []

    @ENUM
    def each(hwnd, _):
        owner = wintypes.DWORD()
        USER.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            class_name = ctypes.create_unicode_buffer(256)
            USER.GetClassNameW(hwnd, class_name, len(class_name))
            found.append((hwnd, class_name.value, bool(USER.IsWindowVisible(hwnd))))
        return True

    USER.EnumWindows(each, 0)
    return found


class NativeLauncherChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "scripts/build-desktop-launcher.ps1").exists():
            raise AssertionError("Native windowed EXE build entry is missing")
        cls.build_temp = tempfile.TemporaryDirectory(prefix="factory-native-build-")
        cls.exe = Path(cls.build_temp.name) / "fixture-launcher.exe"
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-File", str(ROOT / "scripts/build-desktop-launcher.ps1"),
             "-OutputPath", str(cls.exe)], capture_output=True, text=True, encoding="utf-8", timeout=45)
        if result.returncode:
            cls.build_temp.cleanup()
            raise AssertionError(f"Native EXE build failed: {result.stdout}\n{result.stderr}")
        if not cls.exe.is_file():
            raise AssertionError("Native compiler did not create an executable")

    @classmethod
    def tearDownClass(cls):
        cls.build_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build something '造点啥-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(self.exe, self.root / "造点啥.exe")
        (self.root / "VERSION.json").write_text(json.dumps({"version": PRODUCT_VERSION}), encoding="utf-8")
        self.processes = []
        self.child_handles = []
        self.addCleanup(self.cleanup_owned)

    def cleanup_owned(self):
        for process in self.processes:
            if process.poll() is None:
                self.close_form(process)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=8)
        for handle in self.child_handles:
            if KERNEL.WaitForSingleObject(handle, 0) == 258:
                KERNEL.TerminateProcess(handle, 0)
                KERNEL.WaitForSingleObject(handle, 5000)
            KERNEL.CloseHandle(handle)

    def write_scripts(self, prepare_body="", desktop_body=""):
        (self.root / "scripts/bootstrap-runtime.ps1").write_text(
            "param([string]$Mode,[string]$Root,[int]$Port,[switch]$PrepareOnly)\n"
            "if($Mode -ne 'desktop' -or -not $PrepareOnly){throw 'Expected desktop preparation only'}\n"
            "[IO.File]::WriteAllText((Join-Path $Root 'prepared.txt'),'prepared')\n"
            + prepare_body, encoding="utf-8")
        (self.root / "scripts/start-desktop-factory.ps1").write_text(
            "param([int]$Port,[string]$StartupStatusPath)\n"
            "$root=Split-Path $PSScriptRoot -Parent\n"
            "$version=(Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $root 'VERSION.json')|ConvertFrom-Json).version\n"
            + desktop_body, encoding="utf-8")

    def start(self):
        process = subprocess.Popen([str(self.root / "造点啥.exe")], cwd=self.root)
        self.processes.append(process)
        return process

    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail("Timed out waiting for the temporary native startup fixture")

    def receipt(self, process, status):
        for path in (self.root / "logs/desktop-launcher").glob("*-startup.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if value.get("launcherPid") == process.pid and value.get("status") == status:
                return value
        return None

    def close_form(self, process):
        for hwnd, class_name, _ in windows(process.pid):
            if class_name.startswith("WindowsForms10."):
                USER.PostMessageW(hwnd, 0x0010, 0, 0)

    def child_handle(self, name):
        path = self.root / name
        self.wait_for(path.exists)
        pid = int(path.read_text())
        handle = KERNEL.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | TERMINATE
        self.assertTrue(handle, "Fixture child must be alive before ownership checks")
        self.child_handles.append(handle)
        return handle

    def test_executable_is_gui_subsystem_and_product_version_is_complete(self):
        binary = self.exe.read_bytes()
        header = struct.unpack_from("<I", binary, 0x3C)[0]
        self.assertEqual(binary[header:header + 4], b"PE\0\0")
        self.assertEqual(struct.unpack_from("<H", binary, header + 24 + 68)[0], 2)
        quoted = str(self.exe).replace("'", "''")
        command = f"(Get-Item -LiteralPath '{quoted}').VersionInfo | Select-Object ProductVersion,FileVersion | ConvertTo-Json"
        result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-EncodedCommand",
                                 base64.b64encode(command.encode("utf-16-le")).decode()],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        value = json.loads(result.stdout)
        self.assertEqual(value["ProductVersion"], PRODUCT_VERSION)
        self.assertEqual(value["FileVersion"], FILE_VERSION)

    def test_ready_closes_startup_form_and_releases_only_ready_native_child(self):
        self.write_scripts(desktop_body="""
$child=Start-Process -FilePath (Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe') -ArgumentList '-NoProfile -Command "Start-Sleep -Seconds 90"' -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText((Join-Path $root 'native-child.pid'),[string]$child.Id)
Start-Sleep -Milliseconds 250
$ready=@{status='ready';version=$version;pid=$child.Id;hwnd=123;visible=$true;minimized=$false}
[IO.File]::WriteAllText($StartupStatusPath,($ready|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
"""
        )
        process = self.start()
        child = self.child_handle("native-child.pid")
        receipt = self.wait_for(lambda: self.receipt(process, "ready"))
        self.assertTrue(receipt["visible"])
        self.assertEqual(receipt["version"], PRODUCT_VERSION)
        self.assertEqual(process.wait(timeout=8), 0)
        self.assertEqual(KERNEL.WaitForSingleObject(child, 0), 258, "Ready desktop child must remain alive")
        self.assertTrue((self.root / "prepared.txt").exists())

    def test_cancelling_preparation_kills_owned_child_and_creates_no_console(self):
        self.write_scripts(prepare_body="""
$child=Start-Process -FilePath (Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe') -ArgumentList '-NoProfile -Command "Start-Sleep -Seconds 90"' -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText((Join-Path $Root 'prepare-child.pid'),[string]$child.Id)
Start-Sleep -Seconds 90
"""
        )
        process = self.start()
        child = self.child_handle("prepare-child.pid")
        receipt = self.wait_for(lambda: self.receipt(process, "preparing"))
        self.assertTrue(any(visible for _, _, visible in windows(process.pid)), "Startup must be a visible native window")
        self.assertFalse(any(class_name == "ConsoleWindowClass" for _, class_name, _ in windows(process.pid)))
        self.assertFalse(any(visible for _, _, visible in windows(receipt["bootstrapPid"])), "Bootstrap cannot show a command window")
        self.close_form(process)
        self.assertEqual(process.wait(timeout=8), 2)
        self.assertEqual(KERNEL.WaitForSingleObject(child, 5000), 0, "Cancelled preparation child must exit")
        self.assertTrue(self.receipt(process, "cancelled"))

    def test_failed_preparation_retains_log_and_readable_error_window(self):
        self.write_scripts(prepare_body="Write-Output 'fixture preparation failed'; exit 7\n")
        process = self.start()
        receipt = self.wait_for(lambda: self.receipt(process, "failed"))
        self.assertIn("7", receipt["error"])
        self.assertIn("准备", receipt["message"])
        self.assertTrue(Path(receipt["logPath"]).exists())
        self.assertIn("fixture preparation failed", Path(receipt["logPath"]).read_text(encoding="utf-8"))
        self.assertIsNone(process.poll(), "Failure should remain readable until closed")
        self.close_form(process)
        self.assertEqual(process.wait(timeout=8), 1)

    def test_invisible_ready_is_rejected_and_does_not_handover(self):
        self.write_scripts(desktop_body="""
$ready=@{status='ready';version=$version;pid=$PID;hwnd=123;visible=$false;minimized=$false}
[IO.File]::WriteAllText($StartupStatusPath,($ready|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
"""
        )
        process = self.start()
        receipt = self.wait_for(lambda: self.receipt(process, "failed"))
        self.assertIn("可见", receipt["error"])
        self.close_form(process)
        self.assertEqual(process.wait(timeout=8), 1)

    def test_duplicate_double_click_does_not_run_another_preparation(self):
        self.write_scripts(prepare_body="Start-Sleep -Seconds 90\n")
        first = self.start()
        self.wait_for(lambda: self.receipt(first, "preparing"))
        second = self.start()
        self.assertEqual(second.wait(timeout=8), 0)
        self.assertTrue(self.receipt(second, "already-preparing"))
        self.assertIsNone(first.poll())
        self.close_form(first)
        self.assertEqual(first.wait(timeout=8), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
