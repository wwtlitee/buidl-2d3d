"""Desktop-owned runtime checks; temporary roots and CPU fixtures only. v0.5.0a."""
from __future__ import annotations

import http.client
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_queue import Factory
import factory_app_runtime as APP_RUNTIME
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from factory_app_runtime import LocalAppRuntime
except ImportError:
    LocalAppRuntime = None


class AppLifecycleChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='build-something-app-')
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'VERSION.json').write_text('{"version":"0.5.0a"}', encoding='utf-8')
        (self.root / 'config').mkdir()
        for name in ('setup-catalog.json', 'python-models.json'):
            (self.root / 'config' / name).write_text('{"models":[]}', encoding='utf-8')
        (self.root / 'visual').mkdir()
        (self.root / 'visual/index.html').write_text('<html>Fixture app</html>', encoding='utf-8')
        (self.root / 'scripts').mkdir()
        engine = self.root / 'engines/fixture'
        engine.mkdir(parents=True)
        (engine / 'engine.json').write_text(json.dumps({
            'id': 'fixture', 'title': 'CPU fixture', 'kind': 'model', 'status': 'ready',
            'entrypoint': 'run.ps1', 'capabilities': {'multiView': False}}), encoding='utf-8')
        (engine / 'run.ps1').write_text("throw 'Fixture only'", encoding='utf-8')
        self.runtime = None

    def tearDown(self):
        if self.runtime:
            self.runtime.stop()
        self.temporary.cleanup()

    def start_app(self, port=0, start_worker=False):
        self.assertIsNotNone(LocalAppRuntime, 'Desktop needs an owned in-process LocalAppRuntime')
        self.runtime = LocalAppRuntime(self.root, port=port, start_worker=start_worker).start()
        return self.runtime

    @staticmethod
    def wait_for(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.05)
        raise AssertionError('Fixture operation did not reach the expected state')

    def queue_job(self):
        factory = self.runtime.factory
        buffer = BytesIO()
        Image.new('RGB', (16, 16), (20, 60, 90)).save(buffer, format='PNG')
        image = factory.upload_image(buffer.getvalue(), 'fixture.png')
        batch = factory.create_batch({'engineIds': ['fixture'], 'objects': [
            {'name': 'CPU fixture', 'mainImageId': image['id'], 'auxiliaryImages': []}]})
        return batch['jobs'][0]

    def test_http_health_uses_the_same_process_and_closes_listener(self):
        app = self.start_app()
        client = http.client.HTTPConnection('127.0.0.1', app.port, timeout=3)
        client.request('GET', '/api/health')
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Connection'), 'close')
        self.assertEqual(json.loads(response.read())['version'], '0.5.0a')
        client.close()
        self.assertEqual(app.url, f'http://127.0.0.1:{app.port}/')
        self.assertTrue(app.thread.is_alive())
        app.stop()
        self.assertFalse(app.thread.is_alive())
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', app.port), timeout=.5)
        app.stop()  # Closing a window and finally cleanup may both call stop.

    def test_closing_also_releases_idle_half_open_request_threads(self):
        app = self.start_app()
        client = socket.create_connection(('127.0.0.1', app.port), timeout=3)
        try:
            client.sendall(b'GET /api/health HTTP/1.1\r\n')
            self.wait_for(lambda: bool(app.server.request_threads))
            started = time.monotonic()
            app.stop()
            self.assertLess(time.monotonic() - started, 4)
            self.assertFalse(app.thread.is_alive())
            self.assertFalse(any(thread.is_alive() for thread in app.server.request_threads))
        finally:
            client.close()

    def test_port_conflict_does_not_read_or_mutate_existing_queue(self):
        self.assertIsNotNone(LocalAppRuntime, 'Desktop needs an owned in-process LocalAppRuntime')
        storage = self.root / 'logs/visual-factory'
        storage.mkdir(parents=True)
        state_file = storage / 'state.json'
        original = b'{"images":{},"batches":[{"jobs":[{"status":"running"}]}]}'
        state_file.write_bytes(original)
        with socket.socket() as external_listener:
            external_listener.bind(('127.0.0.1', 0))
            external_listener.listen()
            app = LocalAppRuntime(self.root, port=external_listener.getsockname()[1])
            with self.assertRaisesRegex(RuntimeError, '旧|占用|关闭'):
                app.start()
            self.assertEqual(state_file.read_bytes(), original)
            self.assertIsNone(app.factory)

    def test_same_project_cannot_open_on_another_port(self):
        app = self.start_app()
        state_file = self.root / 'logs/visual-factory/state.json'
        original = state_file.read_bytes()
        second = LocalAppRuntime(self.root, port=0, start_worker=False)
        with self.assertRaisesRegex(RuntimeError, '旧|占用|关闭|运行'):
            second.start()
        self.assertEqual(state_file.read_bytes(), original)
        self.assertTrue(app.thread.is_alive())
        self.assertIsNone(second.factory)

    def test_shutdown_failure_retains_project_lease_until_cleanup_can_retry(self):
        app = self.start_app()
        with patch.object(app.factory, 'close', side_effect=RuntimeError('fixture still stopping')):
            with self.assertRaisesRegex(RuntimeError, 'still stopping'):
                app.stop()
        self.assertIsNotNone(app._lease, 'Failed shutdown released ownership of an active queue')
        second = LocalAppRuntime(self.root, port=0, start_worker=False)
        with self.assertRaisesRegex(RuntimeError, '运行'):
            second.start()
        app.stop()
        self.assertIsNone(app._lease)
        restarted = LocalAppRuntime(self.root, port=0, start_worker=False).start()
        restarted.stop()

    def test_pending_generation_is_preserved_but_requires_manual_retry(self):
        app = self.start_app()
        job = self.queue_job()
        input_path = app.factory.root / app.factory.data['images'][job['mainImageId']]['path']
        input_bytes = input_path.read_bytes()
        app.stop()
        state = json.loads(app.factory.state_file.read_text(encoding='utf-8'))
        self.assertEqual(state['batches'][0]['jobs'][0]['status'], 'interrupted')
        app = self.start_app()
        self.assertEqual(app.factory._find_job(job['id'])['status'], 'interrupted')
        self.assertEqual(input_path.read_bytes(), input_bytes)
        self.assertFalse((self.root / 'outputs').exists())
        retry = app.factory.retry_job(job['id'])
        self.assertEqual(retry['status'], 'queued')
        self.assertEqual(retry['retryOf'], job['id'])

    def test_generic_factory_close_keeps_pending_queue_semantics(self):
        self.start_app()
        job = self.queue_job()
        self.runtime.factory.close()
        self.assertEqual(self.runtime.factory._find_job(job['id'])['status'], 'queued')

    def test_pending_installation_is_marked_interrupted_at_window_close(self):
        app = self.start_app()
        app.factory.setup.jobs.append({'id': 'f' * 32, 'modelId': 'fixture',
                                       'status': 'queued', 'stage': '等待安装'})
        app.factory.setup._save()
        app.stop()
        state = json.loads(app.factory.setup.state_file.read_text(encoding='utf-8'))
        self.assertEqual(state['jobs'][0]['status'], 'interrupted')

    def test_failed_start_does_not_leave_an_installation_worker(self):
        self.assertIsNotNone(LocalAppRuntime, 'Desktop needs an owned in-process LocalAppRuntime')
        storage = self.root / 'logs/visual-factory'
        storage.mkdir(parents=True)
        (storage / 'state.json').write_text('broken queue', encoding='utf-8')
        previous = set(threading.enumerate())
        app = LocalAppRuntime(self.root, port=0)
        try:
            with self.assertRaisesRegex(RuntimeError, '队列'):
                app.start()
            leaked = [thread for thread in threading.enumerate() if thread not in previous]
            self.assertEqual(leaked, [], 'Failed native startup leaked the installation worker')
        finally:
            # Clean up the known pre-fix leak so the failing test remains isolated.
            for thread in threading.enumerate():
                if thread not in previous:
                    owners = [value for value in getattr(thread, '_args', ())
                              if hasattr(value, 'close')]
                    target = getattr(thread, '_target', None)
                    if target and hasattr(target, '__self__'):
                        owners.append(target.__self__)
                    for owner in owners:
                        owner.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows native app fixture')
    def test_window_close_cancels_an_owned_installation_probe(self):
        app = self.start_app()
        setup = app.factory.setup
        command = self.root / 'engines/fixture/cmd.exe'
        shutil.copy2(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/cmd.exe', command)
        script = self.root / 'scripts/probe.cmd'
        script.write_text(f'@echo off\n"{sys.executable}" -c "import time; time.sleep(90)"\n', encoding='utf-8')
        setup.catalog = {'models': [{'id': 'fixture', 'title': 'CPU probe', 'kind': 'model',
                                    'files': [], 'required': [], 'probes': [
            {'path': 'engines/fixture/cmd.exe', 'args': ['/c', '@root:scripts/probe.cmd'],
             'cwd': 'engines/fixture', 'timeout': 90}]}]}
        setup.jobs.append({'id': 'c' * 32, 'modelId': 'fixture', 'status': 'queued',
                           'totalBytes': 0, 'licenseAccepted': True, 'log': []})
        setup.cancel_events['c' * 32] = threading.Event()
        setup.worker = threading.Thread(target=setup._work, name='fixture-installer', daemon=True)
        setup.worker.start()
        self.wait_for(lambda: setup.jobs[0].get('stage') == '部署并检测模型入口')
        time.sleep(.25)
        started = time.monotonic()
        app.stop()
        self.assertFalse(setup.worker.is_alive(), 'Closing the app left an installation probe alive')
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(setup.jobs[0]['status'], 'interrupted')

    def test_generation_child_and_descendant_stop_when_app_closes(self):
        app = self.start_app(start_worker=True)
        launcher = self.root / 'scripts/factory.ps1'
        launcher.write_text('''param([string]$Action,[string]$Engine,[string]$InputImage,[string]$RunId)
$child = Start-Process -FilePath $env:FACTORY_CPU_PYTHON -ArgumentList @('-c','"import time; time.sleep(90)"') -PassThru -WindowStyle Hidden
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'child.pid'), [string]$child.Id)
Start-Sleep -Seconds 90
''', encoding='utf-8')
        from factory_runtime import powershell_command
        shell = powershell_command(ROOT)
        with patch('factory_queue.powershell_command', return_value=shell), patch.dict(
                os.environ, {'FACTORY_CPU_PYTHON': sys.executable}):
            job = self.queue_job()
            self.wait_for(lambda: (self.root / 'scripts/child.pid').is_file())
            process = app.factory.process
            self.assertIsNotNone(process)
            child_pid = int((self.root / 'scripts/child.pid').read_text())
            if os.name == 'nt':
                self.assertIsNotNone(getattr(process, '_factory_owned_job', None),
                                     'Engine descendants need a kill-on-close Windows Job')
            app.stop()
        self.assertIsNotNone(process.poll())
        self.assertFalse(app.factory.worker.is_alive())
        self.assertFalse(app.factory.setup.worker.is_alive())
        self.assertEqual(app.factory._find_job(job['id'])['status'], 'interrupted')
        if os.name == 'nt':
            result = subprocess.run([shell, '-NoLogo', '-NoProfile', '-Command',
                f"if (Get-Process -Id {child_pid} -ErrorAction SilentlyContinue) {{ exit 1 }}"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, 'App close left a CPU descendant alive')


@unittest.skipUnless(os.name == 'nt', 'Legacy migration is Windows-only')
class LegacyMigrationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='build-something-legacy-')
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/factory_web.py').write_text('# isolated old service fixture\n', encoding='utf-8')
        (self.root / 'logs/visual-factory').mkdir(parents=True)
        self.state_file = self.root / 'logs/visual-factory/state.json'
        self.original_state = b'{"fixture":"preserved"}'
        self.state_file.write_bytes(self.original_state)
        self.health = {'ok': True, 'service': '2D3DFactory', 'version': '0.5.0'}
        self.jobs = [{'status': 'generated-unreviewed'}]
        self.install_jobs = []
        self.state_reads = 0
        self.activate_on_second_read = False
        self.commands = []
        self.after_stop_owner = {'listenerCount': 0}
        fixture = self

        class FakeOldService(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/api/health':
                    payload = fixture.health
                elif self.path == '/api/state':
                    fixture.state_reads += 1
                    if fixture.activate_on_second_read and fixture.state_reads > 1:
                        fixture.jobs = [{'status': 'running'}]
                    payload = {'jobs': fixture.jobs}
                elif self.path == '/api/setup':
                    payload = {'jobs': fixture.install_jobs}
                else:
                    self.send_error(404)
                    return
                data = json.dumps(payload).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOldService)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()
        self.owner = {'pid': 24680, 'creationDate': '2026-10-08T00:00:00Z', 'commandLine':
            f'"C:\\Fixture\\python.exe" -u "{self.root / "scripts/factory_web.py"}" --port {self.port}'}
        self.owners = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertEqual(self.state_file.read_bytes(), self.original_state)
        self.temporary.cleanup()

    def run_process(self, command, **kwargs):
        self.commands.append(command)
        self.assertEqual(kwargs.get('creationflags'), subprocess.CREATE_NO_WINDOW)
        if Path(command[0]).name.lower() == 'taskkill.exe':
            self.assertEqual(command[1:], ['/PID', '24680', '/T', '/F'])
            self.server.shutdown()
            self.server.server_close()
            return subprocess.CompletedProcess(command, 0, '', '')
        if not self.thread.is_alive():
            return subprocess.CompletedProcess(command, 0, json.dumps(self.after_stop_owner), '')
        owner = self.owners.pop(0) if self.owners is not None else self.owner
        return subprocess.CompletedProcess(command, 0, json.dumps(owner), '')

    def migrate(self):
        migrate = getattr(APP_RUNTIME, 'migrate_legacy_service', None)
        self.assertIsNotNone(migrate, 'Native launch needs safe idle legacy service migration')
        with patch.object(APP_RUNTIME.subprocess, 'run', side_effect=self.run_process):
            return migrate(self.root, self.port)

    def assert_no_kill(self):
        self.assertFalse(any(Path(command[0]).name.lower() == 'taskkill.exe'
                             for command in self.commands))

    def test_validated_idle_old_service_is_migrated_without_proxy_or_queue_mutation(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1',
                                    'http_proxy': 'http://127.0.0.1:1'}):
            self.assertTrue(self.migrate())
        self.assertEqual(self.state_reads, 2)
        self.assertEqual(len(self.commands), 4)  # Two identity checks, owned PID stop, absent listener.

    def test_validated_idle_old_service_with_large_history_is_migrated(self):
        self.jobs = [{'status': 'generated-unreviewed', 'logTail': 'x' * (40 * 1024 * 1024)}]
        self.assertTrue(self.migrate())
        self.assertEqual(self.state_reads, 2)

    def test_large_active_history_is_skipped(self):
        self.jobs = [{'status': 'running', 'logTail': 'x' * (40 * 1024 * 1024)}]
        self.assertFalse(self.migrate())
        self.assert_no_kill()

    def test_active_generation_is_skipped(self):
        self.jobs = [{'status': 'running'}]
        self.assertFalse(self.migrate())
        self.assert_no_kill()

    def test_queued_installation_is_skipped(self):
        self.install_jobs = [{'status': 'queued'}]
        self.assertFalse(self.migrate())
        self.assert_no_kill()

    def test_unknown_service_or_current_advanced_server_is_skipped(self):
        for health in ({'ok': True, 'service': 'other', 'version': '0.5.0'},
                       {'ok': True, 'service': '2D3DFactory', 'version': '0.6.0'},
                       {'ok': True, 'service': '2D3DFactory', 'version': '0.6.0a'}):
            with self.subTest(health=health):
                self.health = health
                self.assertFalse(self.migrate())
                self.assert_no_kill()

    def test_process_script_of_another_root_is_skipped(self):
        self.owner['commandLine'] = f'"C:\\Fixture\\python.exe" "C:\\Other\\scripts\\factory_web.py" --port {self.port}'
        self.assertFalse(self.migrate())
        self.assert_no_kill()
        self.assertEqual(self.state_reads, 0, 'Unknown listener owner must be refused before large state reads')

    def test_explicit_different_queue_root_is_skipped(self):
        self.owner['commandLine'] += ' --root "C:\\Other"'
        self.assertFalse(self.migrate())
        self.assert_no_kill()

    def test_unknown_or_unreadable_task_state_is_skipped(self):
        for jobs in (None, {}, [{'status': 'unrecognized'}]):
            with self.subTest(jobs=jobs):
                self.jobs = jobs
                self.assertFalse(self.migrate())
                self.assert_no_kill()

    def test_new_work_after_first_idle_check_prevents_migration(self):
        self.activate_on_second_read = True
        self.assertFalse(self.migrate())
        self.assertEqual(self.state_reads, 2)
        self.assert_no_kill()

    def test_listener_process_replacement_prevents_migration(self):
        self.owners = [dict(self.owner), dict(self.owner, pid=24681)]
        self.assertFalse(self.migrate())
        self.assert_no_kill()

    def test_unverified_listener_state_is_not_confirmation_that_old_listener_is_gone(self):
        self.after_stop_owner = None
        with patch.object(APP_RUNTIME.time, 'monotonic', side_effect=[0, 0, 0, 6]):
            self.assertFalse(self.migrate())


if __name__ == '__main__':
    unittest.main(verbosity=2)
