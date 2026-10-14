"""Portable startup contracts; CPU-only, no downloads. v0.5.0 / Unclecow."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.find_spec('factory_runtime')
runtime = importlib.util.module_from_spec(spec) if spec else None
if spec:
    spec.loader.exec_module(runtime)


class PortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, 'Portable runtime resolver must exist')
        self.temp = tempfile.TemporaryDirectory(prefix='build-something-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def touch(self, relative):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'fixture')
        return str(file)

    def test_core_python_preferred_over_model_environment(self):
        self.touch('engines/hunyuan3d/.venv/Scripts/python.exe')
        expected = self.touch('runtime/core/.venv/Scripts/python.exe')
        self.assertEqual(runtime.python_command(self.root), expected)

    def test_existing_installation_remains_usable(self):
        expected = self.touch('engines/hunyuan3d/.venv/Scripts/python.exe')
        self.assertEqual(runtime.python_command(self.root), expected)

    def test_portable_shell_preferred_over_global_path(self):
        expected = self.touch('runtime/tools/pwsh/pwsh.exe')
        with patch('shutil.which', return_value='global-pwsh'):
            self.assertEqual(runtime.powershell_command(self.root), expected)

    def test_missing_shell_returns_actionable_error(self):
        with patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '造点啥.exe'):
                runtime.powershell_command(self.root)

    def test_bootstrap_offline_check_reports_missing_runtime(self):
        shell = __import__('shutil').which('pwsh')
        result = subprocess.run([shell, '-NoProfile', '-File', str(ROOT/'scripts/bootstrap-runtime.ps1'),
                                 '-Root', str(self.root), '-CheckOnly'], capture_output=True, text=True,
                                encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['coreReady'])
        self.assertFalse(report['desktopReady'])
        self.assertFalse((self.root/'runtime').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
