"""Product entry contract: one native EXE, with no CMD recovery instructions."""
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


def assert_gui_executable(test: unittest.TestCase, binary: bytes):
    test.assertGreater(len(binary), 0x40)
    test.assertEqual(binary[:2], b"MZ")
    offset = struct.unpack_from("<I", binary, 0x3C)[0]
    test.assertEqual(binary[offset:offset + 4], b"PE\0\0")
    test.assertEqual(struct.unpack_from("<H", binary, offset + 24 + 68)[0], 2)


class DesktopEntryTests(unittest.TestCase):
    def test_checkout_has_only_native_product_entry(self):
        shell_entries = [path.name for path in ROOT.iterdir() if path.is_file() and path.suffix.lower() in SHELL_SUFFIXES]
        self.assertEqual(shell_entries, [], "Root product entries must not launch a shell window")
        assert_gui_executable(self, (ROOT / "造点啥.exe").read_bytes())

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

    def test_release_archive_has_same_native_entry_contract(self):
        if PACKAGE is None:
            self.skipTest("Pass --package to inspect the exact release ZIP")
        with zipfile.ZipFile(PACKAGE) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            executables = [name for name in names if PurePosixPath(name).name == "造点啥.exe"]
            self.assertEqual(len(executables), 1)
            package_root = PurePosixPath(executables[0]).parent
            entries = [name for name in names if PurePosixPath(name).parent == package_root]
            shell_entries = [name for name in entries if PurePosixPath(name).suffix.lower() in SHELL_SUFFIXES]
            self.assertEqual(shell_entries, [], "Shipped product must not contain competing shell entries")
            assert_gui_executable(self, archive.read(executables[0]))
            violations = []
            for name in names:
                relative = PurePosixPath(name).relative_to(package_root)
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
