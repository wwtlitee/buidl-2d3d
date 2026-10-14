"""CPU-only Windows console regression checks. v0.9.0a / Unclecow.

Real pythonw parents call the production adapters, with only native commands and
model discovery replaced by a tiny Python fixture. No model or GPU is loaded.
Missing hiding flags fail before spawning a potentially visible child. The
explicit FACTORY_CONSOLE_BASELINE_COMPARE=1 opt-in additionally compares the
saved pre-fix adapters; those CPU controls may briefly show a console window.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = {
    'cutout': ROOT / 'scripts/factory_image.py',
    'template': ROOT / 'scripts/templates/qwen-image-run.py',
    'deployed': ROOT / 'engines/qwen-image-21/run.py',
}
BASELINE = ROOT / 'output/console-v090a/baseline-deployed'
BASELINE_HASHES = {
    'cutout': '94a97f7c1f9236c93cac576e956c80556ebbeb674e61db4269ad74d6ac8be90c',
    'template': '908b77d3703bbe29d157727c0d1211660064517aadea5a96ee6f6749b1f761ea',
    'deployed': 'aae7245c9148f60b5708ef0715fe7486727ed29b0b359acd2e2f8ec0bc50516b',
}
COMPARE_BASELINE = os.environ.get('FACTORY_CONSOLE_BASELINE_COMPARE') == '1'
EVIDENCE = []


def console_state():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user.IsWindowVisible.restype = ctypes.c_int
    window = int(kernel.GetConsoleWindow() or 0)
    processes = (ctypes.c_ulong * 8)()
    return {'window': window, 'visible': bool(window and user.IsWindowVisible(window)),
            'processCount': int(kernel.GetConsoleProcessList(processes, len(processes)))}


def native_fixture(report, output, exit_code):
    Path(report).write_text(json.dumps(console_state()), encoding='utf-8')
    print('native-stdout', flush=True)
    print('native-stderr', file=sys.stderr, flush=True)
    if not exit_code:
        Image.new('RGBA', (256, 256), (50, 100, 150, 200)).save(output)
    return exit_code


def worker(folder, adapter_name, scenario, version='current'):
    folder = Path(folder)
    source = ADAPTERS[adapter_name]
    if version == 'baseline':
        if not COMPARE_BASELINE:
            raise RuntimeError('Baseline console comparison needs explicit opt-in')
        source = BASELINE / source.relative_to(ROOT)
        if hashlib.sha256(source.read_bytes()).hexdigest() != BASELINE_HASHES[adapter_name]:
            raise RuntimeError('Saved baseline differs from the reviewed v0.9.0 source')
    result = {'parent': console_state(), 'adapter': adapter_name, 'scenario': scenario,
              'sourceVersion': version, 'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    console_python = Path(sys.executable).with_name('python.exe')
    spec = importlib.util.spec_from_file_location('hidden_adapter_fixture', source)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    exit_code = 7 if scenario == 'nonzero' else 0

    def checked_command(command, options):
        flags = options.get('creationflags', 0)
        if version == 'baseline' and flags != 0:
            raise AssertionError('Baseline no longer uses default creation flags')
        if version != 'baseline' and not flags & subprocess.CREATE_NO_WINDOW:
            raise AssertionError('Production adapter omitted CREATE_NO_WINDOW')
        result['creationflags'] = flags
        if scenario == 'spawn-error':
            raise OSError('fixture-start-failure')
        return command

    def native_command(destination):
        return [str(console_python), str(Path(__file__).resolve()), '--native',
                str(folder / 'child.json'), str(destination), str(exit_code)]

    def cutout_run(command, **options):
        # Reject anything except the expected interception point before executing.
        if Path(command[0]).name != 'trellis-cli.exe' or '--bg-only' not in command:
            raise AssertionError('Unexpected native command')
        safe_command = checked_command(native_command(command[2]), options)
        try:
            completed = subprocess.run(safe_command, **options, capture_output=True,
                                       text=True, encoding='utf-8', timeout=10)
            result.update(stdout=completed.stdout, stderr=completed.stderr)
            return completed
        except subprocess.CalledProcessError as error:
            result.update(stdout=error.stdout, stderr=error.stderr, returncode=error.returncode)
            raise

    def qwen_popen(command, **options):
        checked_command(command, options)
        if command[:2] != [str(console_python), str(Path(__file__).resolve())]:
            raise AssertionError('Unexpected native command')
        if options.get('stdout') != subprocess.PIPE or options.get('stderr') != subprocess.STDOUT:
            raise AssertionError('Native log capture changed')
        return subprocess.Popen(command, **options)

    try:
        if adapter_name == 'cutout':
            source = folder / 'input.png'
            Image.new('RGB', (16, 16), 'orange').save(source)
            result['inputSha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            adapter.subprocess = SimpleNamespace(run=cutout_run, CREATE_NO_WINDOW=subprocess.CREATE_NO_WINDOW)
            image = adapter.prepare_image(source, folder / 'output')
            result['outputMode'] = image.mode
        else:
            (folder / 'VERSION.json').write_text('{"version":"test"}', encoding='utf-8')
            request = folder / 'request.json'
            request.write_text(json.dumps({'prompt': 'CPU fixture', 'width': 256, 'height': 256}), encoding='utf-8')
            result['inputSha256'] = hashlib.sha256(request.read_bytes()).hexdigest()
            files = {role: {'name': role, 'sha256': 'fixture'} for role in ('runtime', 'diffusion', 'textEncoder', 'vae')}
            adapter.ROOT = folder
            adapter.subprocess = SimpleNamespace(Popen=qwen_popen, PIPE=subprocess.PIPE,
                STDOUT=subprocess.STDOUT, CREATE_NO_WINDOW=subprocess.CREATE_NO_WINDOW)
            with patch.object(adapter, 'installed_sources', return_value=({'runtimeCommit': 'fixture'}, files, {}, console_python)), \
                 patch.object(adapter, 'build_command', side_effect=lambda request, paths, exe, output: native_command(output)), \
                 patch.object(adapter, 'cuda_support_directory', return_value=folder, create=True):
                adapter.run(request, folder / 'output')
        result['status'] = 'success'
    except Exception as error:
        result.update(status='error', errorType=type(error).__name__, error=str(error))
    finally:
        receipt = folder / 'output/result.json'
        if receipt.is_file():
            result['receipt'] = json.loads(receipt.read_text(encoding='utf-8'))
        native_log = folder / 'output/native.log'
        if native_log.is_file():
            result['nativeLog'] = native_log.read_text(encoding='utf-8')
        (folder / 'worker.json').write_text(json.dumps(result), encoding='utf-8')


@unittest.skipUnless(os.name == 'nt', 'Windows console ownership contract')
class HiddenGenerationChecks(unittest.TestCase):
    def run_adapter(self, name, scenario, version='current'):
        if not ADAPTERS[name].is_file():
            self.skipTest('Deployed adapter is not installed on this machine')
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        self.assertTrue(pythonw.is_file(), 'Use the core Python runtime with pythonw.exe')
        with tempfile.TemporaryDirectory(prefix='factory-hidden-generation-') as directory:
            folder = Path(directory)
            completed = subprocess.run([str(pythonw), str(Path(__file__).resolve()), '--worker',
                str(folder), name, scenario, version], capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads((folder / 'worker.json').read_text(encoding='utf-8'))
            EVIDENCE.append(result)
            self.assertEqual(result['parent'], {'window': 0, 'visible': False, 'processCount': 0})
            if version == 'baseline':
                self.assertEqual(result.get('creationflags'), 0, result)
            else:
                self.assertTrue(result.get('creationflags', 0) & subprocess.CREATE_NO_WINDOW, result)
            if scenario != 'spawn-error':
                child = json.loads((folder / 'child.json').read_text(encoding='utf-8'))
                result['child'] = child
                # A venv console launcher can share a windowless console with its
                # interpreter. The user-facing contract is no console HWND.
                if version != 'baseline':
                    self.assertEqual(child['window'], 0, child)
                    self.assertFalse(child['visible'], child)
                text = result.get('nativeLog', '') + result.get('stdout', '') + result.get('stderr', '')
                self.assertIn('native-stdout', text)
                self.assertIn('native-stderr', text)
            if scenario == 'success':
                self.assertEqual(result['status'], 'success', result)
                if name != 'cutout':
                    self.assertEqual(result['receipt']['status'], 'generated-unreviewed')
                    self.assertEqual(result['receipt']['output']['width'], 256)
            else:
                self.assertEqual(result['status'], 'error', result)
                self.assertIn('7' if scenario == 'nonzero' else 'fixture-start-failure', result['error'])
                if name != 'cutout':
                    self.assertEqual(result['receipt']['status'], 'failed')
                    self.assertIn('finishedAt', result['receipt'])
                elif scenario == 'nonzero':
                    self.assertEqual(result['returncode'], 7)
            return result

    def compare_baseline(self, name):
        before = self.run_adapter(name, 'success', 'baseline')
        after = self.run_adapter(name, 'success')
        self.assertEqual(before['inputSha256'], after['inputSha256'])
        self.assertEqual(before['status'], after['status'])

    @unittest.skipUnless(COMPARE_BASELINE, 'Explicit opt-in for visible pre-fix CPU control')
    def test_cutout_baseline_comparison(self): self.compare_baseline('cutout')
    @unittest.skipUnless(COMPARE_BASELINE, 'Explicit opt-in for visible pre-fix CPU control')
    def test_template_baseline_comparison(self): self.compare_baseline('template')
    @unittest.skipUnless(COMPARE_BASELINE, 'Explicit opt-in for visible pre-fix CPU control')
    def test_deployed_baseline_comparison(self): self.compare_baseline('deployed')

    def test_cutout_success(self): self.run_adapter('cutout', 'success')
    def test_cutout_nonzero(self): self.run_adapter('cutout', 'nonzero')
    def test_cutout_spawn_error(self): self.run_adapter('cutout', 'spawn-error')
    def test_template_success(self): self.run_adapter('template', 'success')
    def test_template_nonzero(self): self.run_adapter('template', 'nonzero')
    def test_template_spawn_error(self): self.run_adapter('template', 'spawn-error')
    def test_deployed_success(self): self.run_adapter('deployed', 'success')
    def test_deployed_nonzero(self): self.run_adapter('deployed', 'nonzero')
    def test_deployed_spawn_error(self): self.run_adapter('deployed', 'spawn-error')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--native':
        sys.exit(native_fixture(sys.argv[2], sys.argv[3], int(sys.argv[4])))
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(*sys.argv[2:])
    else:
        program = unittest.main(verbosity=2, exit=False)
        if os.environ.get('FACTORY_HIDDEN_EVIDENCE'):
            Path(os.environ['FACTORY_HIDDEN_EVIDENCE']).write_text(json.dumps(EVIDENCE, indent=2), encoding='utf-8')
        sys.exit(not program.result.wasSuccessful())
