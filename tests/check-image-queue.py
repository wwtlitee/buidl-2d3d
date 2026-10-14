"""Isolated image/model queue integration checks. v0.1.0 / Unclecow.

All engines are fake CPU file producers under TemporaryDirectory. Both copied
launchers use one fixture-specific mutex, never the real GPU mutex or models.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import http.client
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
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
SPEC = importlib.util.spec_from_file_location('factory_web_image_checks', ROOT / 'scripts/factory_web.py')
WEB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WEB)
PWSH = shutil.which('pwsh')
REAL_MUTEX = r'Local\Image2D3DFactoryGPU'
TERMINAL = {'generated-unreviewed', 'failed', 'cancelled', 'interrupted'}


class ImageQueueChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='factory-image-check-')
        self.root = Path(self.temporary.name)
        self.mutex = 'Local\\FactoryImageTest-' + self.root.name
        (self.root / 'scripts').mkdir()
        (self.root / 'VERSION.json').write_text(json.dumps({'version': '0.1.0'}), encoding='utf-8')
        for name in ('factory.ps1', 'image-job.ps1'):
            source = (ROOT / 'scripts' / name).read_text(encoding='utf-8')
            self.assertIn(REAL_MUTEX, source)
            source = source.replace(REAL_MUTEX, self.mutex)
            self.assertNotIn(REAL_MUTEX, source)
            (self.root / 'scripts' / name).write_text(source, encoding='utf-8')
        Image.new('RGBA', (512, 768), (33, 100, 155, 255)).save(self.root / 'fixture.png')
        for engine, kind, status in (('mesh', 'model', 'installed'), ('image', 'image', 'installed'),
                                     ('blocked-image', 'image', 'blocked')):
            folder = self.root / 'engines' / engine
            folder.mkdir(parents=True)
            manifest = {'id': engine, 'title': 'CPU fixture ' + engine, 'kind': kind,
                        'version': 'fixture-1', 'factoryVersion': '0.1.0', 'status': status,
                        'entrypoint': 'run.ps1', 'capabilities': {'multiView': False, 'views': []}}
            (folder / 'engine.json').write_text(json.dumps(manifest), encoding='utf-8')
            (folder / 'run.ps1').write_text("throw 'Fake engine not configured'\n", encoding='utf-8')
        self.factory = WEB.Factory(self.root, start_worker=False)

    def tearDown(self):
        self.factory.close()
        self.temporary.cleanup()

    def image_request(self, **changes):
        return {'engineId': 'image', 'prompt': '  一个金属小牛\n纯色背景  ',
                'width': 512, 'height': 768, 'steps': 20, 'seed': 42,
                'negativePrompt': '文字，模糊', **changes}

    def model_request(self, image=None):
        if image is None:
            image = self.factory.upload_image((self.root / 'fixture.png').read_bytes(), 'main.png')
        return {'objects': [{'name': '小牛', 'mainImageId': image['id'], 'auxiliaryImages': []}],
                'engineIds': ['mesh']}

    def assert_rejected(self, request):
        before = len(self.factory.state()['jobs'])
        with self.assertRaises((ValueError, TypeError)):
            self.factory.create_image_job(request)
        self.assertEqual(len(self.factory.state()['jobs']), before)

    def install_fake_engines(self, image_mode='valid', hold_ms=0):
        image_body = {
            'valid': "[IO.File]::Copy((Join-Path $root 'fixture.png'), (Join-Path $OutputDirectory 'generated.png'))",
            'missing': "Write-Output 'No image produced'",
            'wrong-kind': "'v 0 0 0'|Set-Content -LiteralPath (Join-Path $OutputDirectory 'wrong.obj')",
            'fail-after-image': "[IO.File]::Copy((Join-Path $root 'fixture.png'), (Join-Path $OutputDirectory 'generated.png')); throw 'Intentional fixture failure'",
        }[image_mode]
        image_script = f"""param([string]$RequestJson,[string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$request=Get-Content -Raw -LiteralPath $RequestJson|ConvertFrom-Json
$request|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $OutputDirectory 'captured-request.json') -Encoding utf8NoBOM
'started'|Set-Content -LiteralPath (Join-Path $OutputDirectory 'engine-started.marker')
Start-Sleep -Milliseconds {hold_ms}
{image_body}
"""
        model_script = """param([string]$InputImage,[string]$OutputDirectory)
$ErrorActionPreference='Stop'
"v 0 0 0`nv 1 0 0`nv 0 1 0`nf 1 2 3"|Set-Content -LiteralPath (Join-Path $OutputDirectory 'fixture.obj') -Encoding utf8NoBOM
"""
        (self.root / 'engines/image/run.ps1').write_text(image_script, encoding='utf-8')
        (self.root / 'engines/mesh/run.ps1').write_text(model_script, encoding='utf-8')

    def start_worker(self):
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=True)

    def wait_for(self, condition, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = condition()
            if result:
                return result
            time.sleep(0.05)
        self.fail('Fixture did not reach the expected state within the bounded timeout')

    def execute(self, request=None):
        job = self.factory.create_image_job(request or self.image_request())
        self.factory._execute_job(self.factory._find_job(job['id']))
        return self.factory._find_job(job['id'])

    def receipt(self, job):
        return json.loads((self.factory.run_root(job) / 'receipt.json').read_text(encoding='utf-8'))

    @contextmanager
    def local_http(self):
        with patch.object(WEB.Handler, 'log_message', lambda *_: None):
            server = WEB.FactoryServer(('127.0.0.1', 0), self.factory)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                yield connection
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def post(self, connection, endpoint, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        connection.request('POST', endpoint, body=data,
                           headers={'Content-Type': 'application/json', 'X-Factory-Request': '1'})
        response = connection.getresponse()
        status, value = response.status, json.loads(response.read())
        if status >= 400:
            connection.close()
        return status, value

    def test_registry_separates_image_and_model_without_claiming_ready(self):
        state = self.factory.state()
        self.assertEqual([engine['id'] for engine in state['engines']], ['mesh'])
        self.assertEqual({engine['id'] for engine in state['imageEngines']}, {'image', 'blocked-image'})
        self.assertEqual(next(engine['status'] for engine in state['imageEngines'] if engine['id'] == 'image'), 'installed')
        (self.root / 'engines/image/run.ps1').unlink()
        self.assertEqual(next(engine['status'] for engine in self.factory.engines() if engine['id'] == 'image'), 'blocked')
        self.assert_rejected(self.image_request())

    def test_routes_reject_wrong_kind_and_blocked_engine(self):
        for engine in ('mesh', 'blocked-image', 'unknown'):
            with self.subTest(engine=engine):
                self.assert_rejected(self.image_request(engineId=engine))
        payload = self.model_request()
        payload['engineIds'] = ['image']
        with self.assertRaises(ValueError):
            self.factory.create_batch(payload)
        self.assertEqual(self.factory.state()['jobs'], [])

    def test_default_request_and_prompt_are_saved_as_data(self):
        job = self.factory.create_image_job({'engineId': 'image', 'prompt': '  两行\n描述 "quoted" $(literal)  '})
        self.assertEqual(job['request']['prompt'], '两行\n描述 "quoted" $(literal)')
        self.assertEqual(job['request']['width'], 512)
        self.assertEqual(job['request']['height'], 512)
        self.assertEqual(job['request']['steps'], 20)
        self.assertEqual(job['request']['negativePrompt'], '')
        self.assertIs(type(job['request']['seed']), int)
        self.assertTrue(0 <= job['request']['seed'] < 2**31)
        self.assertEqual(job['viewImages'], {})
        self.assertIsNone(job['mainImageId'])

    def test_invalid_prompts_are_rejected_before_queueing(self):
        for prompt in (None, '', ' \n\t ', 0, [], {}, 'x' * 3001, '牛\0模型'):
            with self.subTest(prompt=repr(prompt)[:50]):
                self.assert_rejected(self.image_request(prompt=prompt))

    def test_invalid_negative_prompts_are_not_coerced_into_empty_text(self):
        for negative in (False, 0, [], {}, None, 'x' * 2001, 'bad\0text'):
            with self.subTest(negative=repr(negative)[:50]):
                self.assert_rejected(self.image_request(negativePrompt=negative))

    def test_dimensions_require_integer_presets(self):
        for key in ('width', 'height'):
            for value in (256, 513, 512.0, True, '512', [], None, 2048):
                with self.subTest(key=key, value=repr(value)):
                    self.assert_rejected(self.image_request(**{key: value}))

    def test_steps_and_seed_reject_bool_float_and_out_of_range(self):
        for key, values in (('steps', (0, 101, True, 20.0, '20', None)),
                            ('seed', (-1, 2**31, True, 42.0, '42', [], {}))):
            for value in values:
                with self.subTest(key=key, value=repr(value)):
                    self.assert_rejected(self.image_request(**{key: value}))

    def test_parameter_endpoints_and_seed_zero_survive_persistence(self):
        for width, height, steps, seed in ((512, 768, 1, 0), (1024, 1024, 100, 2**31 - 1)):
            job = self.factory.create_image_job(self.image_request(width=width, height=height, steps=steps, seed=seed))
            for key, expected in {'width': width, 'height': height, 'steps': steps, 'seed': seed}.items():
                self.assertEqual(job['request'][key], expected)
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=False)
        self.assertEqual([job['request']['seed'] for job in self.factory.state()['imageJobs']], [0, 2**31 - 1])

    def test_http_image_create_cancel_retry_and_kind_rejection(self):
        with self.local_http() as connection:
            status, job = self.post(connection, '/api/image-jobs', self.image_request(seed=0))
            self.assertEqual(status, 201)
            status, cancelled = self.post(connection, f"/api/image-jobs/{job['id']}/cancel", {})
            self.assertEqual((status, cancelled['status']), (200, 'cancelled'))
            status, retry = self.post(connection, f"/api/image-jobs/{job['id']}/retry", {})
            self.assertEqual(status, 200)
            self.assertEqual(retry['request'], job['request'])
            self.assertNotEqual(retry['runId'], job['runId'])
            status, _ = self.post(connection, f"/api/image-jobs/{job['id']}/retry", {})
            self.assertEqual(status, 400)
            status, _ = self.post(connection, '/api/image-jobs', self.image_request(engineId='mesh'))
            self.assertEqual(status, 400)

    def test_http_invalid_image_bodies_are_client_errors_without_jobs(self):
        with self.local_http() as connection:
            for endpoint in ('/api/image-jobs', '/api/images/from-output'):
                for payload in ([], None, 'wrong', 7):
                    with self.subTest(endpoint=endpoint, payload=payload):
                        status, _ = self.post(connection, endpoint, payload)
                        self.assertEqual(status, 400)
        self.assertEqual(self.factory.state()['jobs'], [])

    def test_http_rejected_post_advertises_closed_connection(self):
        with self.local_http() as connection:
            connection.request('POST', '/api/image-jobs', body=b'{}',
                               headers={'Content-Type': 'application/json', 'X-Factory-Request': '1'})
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertEqual(response.getheader('Connection'), 'close')
            response.read()

    def test_import_rejects_escape_missing_and_non_image_outputs(self):
        folder = self.root / 'outputs/image/example'
        folder.mkdir(parents=True)
        (folder / 'wrong.png').write_bytes(b'not an image')
        (folder / 'model.obj').write_text('v 0 0 0', encoding='utf-8')
        for url in ('https://example.invalid/image.png', '/files/inputs/a.png',
                    '/files/outputs/../VERSION.json', '/files/outputs/%2e%2e/VERSION.json',
                    '/files/outputs/image/example/missing.png', '/files/outputs/image/example/model.obj',
                    '/files/outputs/image/example/wrong.png'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.factory.import_output_image(url)
        self.assertEqual(self.factory.state()['images'], {})

    @unittest.skipUnless(PWSH, 'PowerShell 7 is required for isolated fake launchers')
    def test_fake_image_receipt_is_real_and_output_imports_as_model_main(self):
        self.install_fake_engines()
        job = self.execute()
        self.assertEqual(job['status'], 'generated-unreviewed')
        folder = self.factory.run_root(job)
        receipt = self.receipt(job)
        self.assertEqual((receipt['status'], receipt['inputMode']), ('generated-unreviewed', 'text'))
        self.assertEqual(receipt['request'], job['request'])
        self.assertTrue(receipt['finishedAt'])
        output = folder / 'generated.png'
        self.assertEqual(receipt['outputs'][0]['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(receipt['outputs'][0]['bytes'], output.stat().st_size)
        captured = json.loads((folder / 'captured-request.json').read_text(encoding='utf-8'))
        self.assertEqual(captured, job['request'])
        asset = next(asset for asset in self.factory.assets(folder) if asset['kind'] == 'image')
        self.assertEqual(asset['previewUrl'], asset['url'])
        with self.local_http() as connection:
            status, imported = self.post(connection, '/api/images/from-output', {'url': asset['url']})
            self.assertEqual(status, 201)
            connection.request('GET', imported['url'])
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            with Image.open(BytesIO(body)) as image:
                self.assertEqual(image.size, (512, 768))
                self.assertEqual(image.getpixel((0, 0)), (33, 100, 155, 255))
        batch = self.factory.create_batch(self.model_request(imported))
        self.assertEqual(batch['jobs'][0]['mainImageId'], imported['id'])
        self.assertEqual(batch['jobs'][0]['viewImages'], {'front': imported['id']})
        self.assertEqual(next(engine['status'] for engine in self.factory.engines() if engine['id'] == 'image'), 'installed')

    @unittest.skipUnless(PWSH, 'PowerShell 7 is required for isolated fake launchers')
    def test_fake_failure_missing_or_wrong_kind_never_report_success(self):
        for mode in ('missing', 'wrong-kind', 'fail-after-image'):
            with self.subTest(mode=mode):
                self.install_fake_engines(image_mode=mode)
                job = self.execute()
                self.assertEqual(job['status'], 'failed')
                self.assertEqual(self.receipt(job)['status'], 'failed')
                public = next(item for item in self.factory.state()['imageJobs'] if item['id'] == job['id'])
                self.assertEqual(public['assets'], [])
                self.assertIsNone(public['progress'])

    @unittest.skipUnless(PWSH, 'PowerShell 7 is required for isolated fake launchers')
    def test_retry_preserves_request_and_does_not_overwrite_original_output(self):
        self.install_fake_engines(image_mode='fail-after-image')
        original = self.execute(self.image_request(seed=0))
        old_bytes = (self.factory.run_root(original) / 'generated.png').read_bytes()
        self.install_fake_engines()
        retry = self.factory.retry_job(original['id'])
        self.assertEqual(retry['request'], original['request'])
        self.assertNotEqual(retry['runId'], original['runId'])
        self.factory._execute_job(self.factory._find_job(retry['id']))
        self.assertEqual(self.factory._find_job(retry['id'])['status'], 'generated-unreviewed')
        self.assertEqual(original['status'], 'failed')
        self.assertEqual((self.factory.run_root(original) / 'generated.png').read_bytes(), old_bytes)
        with self.assertRaises(ValueError):
            self.factory.retry_job(retry['id'])

    @unittest.skipUnless(PWSH, 'PowerShell 7 is required for isolated fake launchers')
    def test_worker_serializes_image_then_model_on_one_queue(self):
        self.install_fake_engines(hold_ms=300)
        image = self.factory.create_image_job(self.image_request())
        model = self.factory.create_batch(self.model_request())['jobs'][0]
        self.start_worker()
        self.wait_for(lambda: all(job['status'] in TERMINAL for job in self.factory.state()['jobs']))
        jobs = self.factory.state()['jobs']
        self.assertEqual([job['status'] for job in jobs], ['generated-unreviewed'] * 2)
        image_receipt, model_receipt = self.receipt(image), self.receipt(model)
        self.assertLessEqual(datetime.fromisoformat(image_receipt['finishedAt']), datetime.fromisoformat(model_receipt['startedAt']))
        state = self.factory.state()
        self.assertEqual(len(state['imageJobs']), 1)
        self.assertEqual(len(state['batches']), 1)
        self.assertTrue(any(asset['kind'] == 'image' for asset in state['imageJobs'][0]['assets']))
        self.assertTrue(any(asset['kind'] == 'model' for asset in state['batches'][0]['jobs'][0]['assets']))

    @unittest.skipUnless(PWSH, 'PowerShell 7 is required for isolated fake launchers')
    def test_running_cancel_stops_fake_image_tree_and_corrects_receipt(self):
        self.install_fake_engines(hold_ms=15000)
        job = self.factory.create_image_job(self.image_request())
        self.start_worker()
        folder = self.factory.run_root(job)
        self.wait_for(lambda: (folder / 'engine-started.marker').exists(), timeout=10)
        self.factory.cancel_job(job['id'])
        self.wait_for(lambda: next(item for item in self.factory.state()['imageJobs']
                                  if item['id'] == job['id'])['status'] == 'cancelled', timeout=10)
        self.assertEqual(self.receipt(job)['status'], 'cancelled')
        self.assertFalse((folder / 'generated.png').exists())

    @unittest.skipUnless(PWSH and os.name == 'nt', 'Windows named mutex protocol check')
    def test_model_launcher_rejects_gpu_lock_held_by_image_launcher(self):
        self.install_fake_engines(hold_ms=3000)
        job = self.factory.create_image_job(self.image_request())
        request = self.root / 'request.json'
        request.write_text(json.dumps(job['request'], ensure_ascii=False), encoding='utf-8')
        command = [PWSH, '-NoLogo', '-NoProfile', '-File', str(self.root / 'scripts/image-job.ps1'),
                   '-Engine', 'image', '-RequestJson', str(request), '-RunId', job['runId']]
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW}
        with subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **flags) as process:
            try:
                self.wait_for(lambda: (self.factory.run_root(job) / 'engine-started.marker').exists(), timeout=10)
                model_run = '20261003-010203-abcdef123456'
                result = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-File', str(self.root / 'scripts/factory.ps1'),
                                         '-Action', 'run', '-Engine', 'mesh', '-InputImage', str(self.root / 'fixture.png'),
                                         '-RunId', model_run], cwd=self.root, capture_output=True, timeout=8, **flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'Another factory generation', result.stdout + result.stderr)
                self.assertFalse((self.root / 'outputs/mesh' / model_run).exists())
                process.communicate(timeout=12)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    self.factory._stop_process(process)

    def test_recovery_needs_matching_artifact_kind_and_does_not_change_ready(self):
        job = self.factory.create_image_job(self.image_request())
        folder = self.factory.run_root(job)
        folder.mkdir(parents=True)
        (folder / 'only-model.obj').write_text('v 0 0 0', encoding='utf-8')
        (folder / 'receipt.json').write_text(json.dumps({'status': 'generated-unreviewed', 'inputMode': 'text'}), encoding='utf-8')
        self.factory._find_job(job['id'])['status'] = 'running'
        self.factory._save()
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=False)
        self.assertEqual(self.factory._find_job(job['id'])['status'], 'interrupted')
        self.assertEqual(next(engine['status'] for engine in self.factory.engines() if engine['id'] == 'image'), 'installed')

    def test_recovery_accepts_committed_image_receipt_with_real_image_artifact(self):
        job = self.factory.create_image_job(self.image_request())
        folder = self.factory.run_root(job)
        folder.mkdir(parents=True)
        (folder / 'generated.png').write_bytes((self.root / 'fixture.png').read_bytes())
        (folder / 'receipt.json').write_text(json.dumps({'status': 'generated-unreviewed', 'inputMode': 'text'}), encoding='utf-8')
        self.factory._find_job(job['id'])['status'] = 'running'
        self.factory._save()
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=False)
        self.assertEqual(self.factory._find_job(job['id'])['status'], 'generated-unreviewed')
        self.assertTrue(any(asset['kind'] == 'image' for asset in self.factory.state()['imageJobs'][0]['assets']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
