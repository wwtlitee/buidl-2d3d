"""Product entry contract: a hidden source launcher and optional native GUI EXE."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import struct
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = None
SHELL_SUFFIXES = {".cmd", ".bat", ".ps1", ".vbs"}
LEGACY_ENTRY = re.compile(r"启动[^\s\"'<>`]*\.(?:cmd|bat)", re.IGNORECASE)
SOURCE_ENTRY = "启动造点啥.vbs"
SOURCE_REQUIRED = (
    SOURCE_ENTRY, "VERSION.json", "scripts/start-source-factory.ps1",
    "scripts/build-desktop-launcher.ps1", "scripts/desktop_launcher.cs",
    "scripts/start-desktop-factory.ps1", "scripts/bootstrap-runtime.ps1",
    "scripts/factory_desktop.py", "scripts/factory_web.py",
    "scripts/factory_runtime.py", "config/desktop-requirements.lock",
)


def assert_gui_executable(test: unittest.TestCase, binary: bytes):
    test.assertGreater(len(binary), 0x40)
    test.assertEqual(binary[:2], b"MZ")
    offset = struct.unpack_from("<I", binary, 0x3C)[0]
    test.assertEqual(binary[offset:offset + 4], b"PE\0\0")
    test.assertEqual(struct.unpack_from("<H", binary, offset + 24 + 68)[0], 2)


class DesktopEntryTests(unittest.TestCase):
    def test_checkout_has_only_supported_product_entries(self):
        shell_entries = [path.name for path in ROOT.iterdir() if path.is_file() and path.suffix.lower() in SHELL_SUFFIXES]
        self.assertEqual(shell_entries, [SOURCE_ENTRY], "Only the hidden source entry is allowed at the root")
        for relative in SOURCE_REQUIRED:
            self.assertTrue((ROOT / relative).is_file(), relative)
        entry = (ROOT / SOURCE_ENTRY).read_bytes()
        entry.decode("ascii")
        self.assertIn(b"shell.Run(command, 0, True)", entry)
        executable = ROOT / "造点啥.exe"
        if executable.exists():
            assert_gui_executable(self, executable.read_bytes())

    def test_runtime_recovery_messages_do_not_return_users_to_cmd(self):
        paths = [p for p in (ROOT / "scripts").rglob("*") if p.suffix in {".py", ".ps1", ".cs"}]
        paths.append(ROOT / "README.md")
        violations = []
        for path in paths:
            for line, text in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if LEGACY_ENTRY.search(text):
                    violations.append(f"{path.relative_to(ROOT)}:{line}")
        self.assertEqual(violations, [])
        for relative in ("scripts/factory_runtime.py", "scripts/start-desktop-factory.ps1", "scripts/start-visual-factory.ps1"):
            self.assertIn("造点啥.exe", (ROOT / relative).read_text(encoding="utf-8-sig"), relative)

    def test_release_archive_has_supported_product_entries(self):
        if PACKAGE is None:
            self.skipTest("Pass --package to inspect the exact release ZIP")
        with zipfile.ZipFile(PACKAGE) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            executables = [name for name in names if PurePosixPath(name).name == "造点啥.exe"]
            self.assertLessEqual(len(executables), 1)
            roots = [PurePosixPath(name).parent for name in names
                     if PurePosixPath(name).name == "VERSION.json"
                     and str(PurePosixPath(name).parent / "scripts/desktop_launcher.cs") in names]
            self.assertEqual(len(roots), 1, "Archive must contain one complete product source root")
            package_root = roots[0]
            entries = [name for name in names if PurePosixPath(name).parent == package_root]
            shell_entries = [PurePosixPath(name).name for name in entries if PurePosixPath(name).suffix.lower() in SHELL_SUFFIXES]
            self.assertEqual(shell_entries, [SOURCE_ENTRY], "Shipped source must contain only the supported hidden entry")
            for relative in SOURCE_REQUIRED:
                self.assertIn(str(package_root / relative), names, relative)
            archive.read(str(package_root / SOURCE_ENTRY)).decode("ascii")
            if executables:
                self.assertEqual(PurePosixPath(executables[0]).parent, package_root)
                assert_gui_executable(self, archive.read(executables[0]))
            violations = []
            for name in names:
                try:
                    relative = PurePosixPath(name).relative_to(package_root)
                except ValueError:
                    continue
                if str(relative) == "README.md" or (relative.parts[0] == "scripts" and relative.suffix in {".py", ".ps1", ".cs"}):
                    if LEGACY_ENTRY.search(archive.read(name).decode("utf-8-sig")):
                        violations.append(str(relative))
            self.assertEqual(violations, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--package", type=Path)
    options, remaining = parser.parse_known_args()
    explicit_environment_package = os.environ.get("FACTORY_RELEASE_PACKAGE")
    PACKAGE = options.package or (Path(explicit_environment_package) if explicit_environment_package else None)
    unittest.main(argv=[__file__, *remaining], verbosity=2)
