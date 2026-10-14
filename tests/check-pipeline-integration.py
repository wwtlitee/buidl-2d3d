"""One-click prompt -> image -> model integration. v0.6.0 / Unclecow.

All launches use temporary CPU file-producer engines and a private mutex. The
actual Factory worker, PowerShell wrappers, HTTP routes and persisted receipts
are exercised without installed models, GPU inference or the user's browser.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_queue import Factory
from factory_web import FactoryServer, Handler

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
PWSH = shutil.which('pwsh')


def fixture_glb():
    binary = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {'asset': {'version': '2.0'}, 'scene': 0,
                'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
                'buffers': [{'byteLength': len(binary)}],
                'bufferViews': [{'buffer': 0, 'byteLength': len(binary)}],
                'accessors': [{'bufferView': 0, 'componentType': 5126,
                               'count': 3, 'type': 'VEC3',
                               'min': [0, 0, 0], 'max': [1, 1, 0]}],
                'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}]}
    encoded = json.dumps(document, separators=(',', ':')).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    return (struct.pack('<III', 0x46546C67, 2, 28 + len(encoded) + len(binary))
            + struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
            + struct.pack('<II', len(binary), 0x004E4942) + binary)


class PipelineIntegrationChecks(unittest.TestCase):
    def test_new_image_engine_defaults_drive_oneclick_parameters(self):
        path=self.root/'engines/image/engine.json'
        manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest.update(mediaAdapter=True,defaults={'width':768,'height':512,'steps':4})
        manifest['capabilities'].update(modes=['txt2img'],allowedSteps=[4])
        path.write_text(json.dumps(manifest),encoding='utf-8')
        try:
            result=self.factory.create_pipeline(self.request())
        except ValueError as error:self.fail('one-click must use the selected engine defaults: '+str(error))
        job=self.factory._find_job(result['imageJobId'])
        self.assertEqual((job['request']['width'],job['request']['height'],job['request']['steps']),(768,512,4))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='build-something-pipeline-')
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'scripts').mkdir()
        (self.root / 'VERSION.json').write_text('{"version":"0.6.0"}', encoding='utf-8')
        for name in ('factory.ps1', 'image-job.ps1'):
            source = (ROOT / 'scripts' / name).read_text(encoding='utf-8')
            source = source.replace(r'Local\Image2D3DFactoryGPU',
                                    'Local\\PipelineCPU-' + self.root.name)
            self.assertNotIn(r'Local\Image2D3DFactoryGPU', source)
            (self.root / 'scripts' / name).write_text(source, encoding='utf-8')
        Image.new('RGBA', (512, 512), (30, 80, 170, 255)).save(self.root / 'fixture.png')
        (self.root / 'fixture.glb').write_bytes(fixture_glb())
        for name, kind, status in [('image', 'image', 'installed'),
                                   ('mesh', 'model', 'installed'),
                                   ('mesh2', 'model', 'installed'),
                                   ('blocked-mesh', 'model', 'blocked')]:
            folder = self.root / 'engines' / name
            folder.mkdir(parents=True)
            manifest = {'id': name, 'title': 'CPU fixture ' + name, 'kind': kind,
                        'status': status, 'entrypoint': 'run.ps1', 'version': 'fixture-1',
                        'capabilities': {'multiView': False, 'views': []}}
            (folder / 'engine.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.install_engines()
        self.factory = Factory(self.root, start_worker=False)

    def tearDown(self):
        self.factory.close(pause_pending=True)
        self.temporary.cleanup()

    def install_engines(self, image_mode='valid', image_hold_ms=0, model_fail=False):
        image_action = {
            'valid': "[IO.File]::Copy((Join-Path $root 'fixture.png'), (Join-Path $OutputDirectory 'generated.png'))",
            'missing': "Write-Output 'CPU fixture produced no image'",
            'fail-after-image': "[IO.File]::Copy((Join-Path $root 'fixture.png'), (Join-Path $OutputDirectory 'generated.png')); throw 'CPU image failure'",
        }[image_mode]
        image_script = f"""param([string]$RequestJson,[string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'engine-started.marker'),'started')
Start-Sleep -Milliseconds {image_hold_ms}
{image_action}
"""
        model_action = "throw 'CPU model failure'" if model_fail else "[IO.File]::Copy((Join-Path $root 'fixture.glb'), (Join-Path $OutputDirectory 'model.glb'))"
        model_script = f"""param([string]$InputImage,[string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'model-started.marker'),'started')
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'consumed.sha256'),(Get-FileHash -LiteralPath $InputImage).Hash.ToLower())
{model_action}
"""
        (self.root / 'engines/image/run.ps1').write_text(image_script, encoding='utf-8')
        for engine in ('mesh', 'mesh2', 'blocked-mesh'):
            (self.root / f'engines/{engine}/run.ps1').write_text(model_script, encoding='utf-8')

    def request(self, **changes):
        return {'prompt': '一只蓝色的小牛', 'imageEngineId': 'image',
                'modelEngineIds': ['mesh', 'mesh2'],
                'clientRequestId': str(uuid.uuid4()), **changes}

    def create(self, payload=None):
        self.assertTrue(callable(getattr(self.factory, 'create_pipeline', None)),
                        'Factory needs a one-click pipeline entry')
        return self.factory.create_pipeline(payload or self.request())

    def pipeline(self, identifier):
        return next(item for item in self.factory.state()['pipelines'] if item['id'] == identifier)

    def start_worker(self):
        self.factory.close()
        self.factory = Factory(self.root, start_worker=True)

    def wait_for(self, condition, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(.05)
        self.fail('CPU pipeline fixture did not reach expected state')

    def wait_terminal(self, identifier):
        self.wait_for(lambda: self.pipeline(identifier)['status'] in TERMINAL)
        return self.pipeline(identifier)

    @contextmanager
    def local_http(self):
        with patch.object(Handler, 'log_message', lambda *_: None):
            server = FactoryServer(('127.0.0.1', 0), self.factory)
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={'poll_interval': .01}, daemon=True)
            thread.start()
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                yield client
            finally:
                client.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    @staticmethod
    def post(client, endpoint, payload):
        body = json.dumps(payload).encode('utf-8')
        client.request('POST', endpoint, body=body,
                       headers={'Content-Type': 'application/json', 'X-Factory-Request': '1'})
        response = client.getresponse()
        return response.status, json.loads(response.read())

    def test_create_queues_only_image_until_it_finishes(self):
        pipeline = self.create()
        self.assertEqual(pipeline['modelBatchId'], None)
        self.assertEqual(len(self.factory.state()['imageJobs']), 1)
        self.assertEqual(self.factory.state()['batches'], [])
        self.assertEqual(self.factory.state()['imageJobs'][0]['id'], pipeline['imageJobId'])

    def test_rejected_model_selection_never_queues_an_image_or_falls_back(self):
        for selected in ([], ['unknown'], ['blocked-mesh'], ['image'], ['mesh', 'mesh']):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.create(self.request(modelEngineIds=selected))
        self.assertEqual(self.factory.state()['jobs'], [])

    def test_image_selection_is_required_and_never_implicitly_changed(self):
        for selected in ('', 'unknown', 'mesh'):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.create(self.request(imageEngineId=selected))
        self.assertEqual(self.factory.state()['jobs'], [])

    def test_invalid_prompt_never_starts_an_image(self):
        for prompt in ('', '  ', None, '\0', '牛' * 3001):
            with self.subTest(prompt=repr(prompt)[:30]), self.assertRaises(ValueError):
                self.create(self.request(prompt=prompt))
        self.assertEqual(self.factory.state()['jobs'], [])

    def test_duplicate_client_request_does_not_submit_twice(self):
        request = self.request()
        first = self.create(request)
        second = self.create(request)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.factory.state()['pipelines']), 1)
        self.assertEqual(len(self.factory.state()['jobs']), 1)

    def fail_second_save(self):
        real_save = self.factory._save
        calls = 0

        def save():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('CPU fixture disk write failure')
            return real_save()

        return patch.object(self.factory, '_save', side_effect=save)

    def test_create_write_failure_cannot_persist_an_unlinked_image_job(self):
        with self.fail_second_save():
            try:
                self.create()
            except OSError:
                pass
        persisted = json.loads(self.factory.state_file.read_text(encoding='utf-8'))
        linked_images = {pipeline['imageJobId'] for pipeline in persisted.get('pipelines', [])}
        pending_images = {job['id'] for batch in persisted['batches'] for job in batch['jobs']
                          if job.get('kind') == 'image' and job['status'] in {'queued', 'running'}}
        self.assertLessEqual(pending_images, linked_images,
                             'Save failure persisted an image with no durable chain or request token')

    def test_rejected_creation_rolls_back_memory_and_allows_same_request_retry(self):
        request = self.request()
        original = self.factory.state_file.read_bytes()
        with patch.object(self.factory, '_save', side_effect=OSError('CPU fixture full disk')):
            with self.assertRaises(OSError):
                self.create(request)
        self.assertEqual(self.factory.data['batches'], [])
        self.assertEqual(self.factory.data['pipelines'], [])
        self.assertEqual(self.factory.state_file.read_bytes(), original)
        result = self.create(request)
        self.assertEqual(result['clientRequestId'], request['clientRequestId'])
        self.assertEqual(len(self.factory.state()['jobs']), 1)

    def test_duplicate_request_token_cannot_change_the_prompt(self):
        request = self.request()
        self.create(request)
        with self.assertRaises(ValueError):
            self.create({**request, 'prompt': '另一只小牛'})
        self.assertEqual(len(self.factory.state()['jobs']), 1)

    def test_concurrent_duplicate_submissions_are_one_persisted_pipeline(self):
        request = self.request()
        self.assertTrue(callable(getattr(self.factory, 'create_pipeline', None)),
                        'Factory needs a one-click pipeline entry')
        barrier = threading.Barrier(5)
        results, errors = [], []

        def submit():
            try:
                barrier.wait(timeout=5)
                results.append(self.factory.create_pipeline(request))
            except Exception as error:
                errors.append(error)

        workers = [threading.Thread(target=submit) for _ in range(4)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len({result['id'] for result in results}), 1)
        persisted = json.loads(self.factory.state_file.read_text(encoding='utf-8'))
        self.assertEqual(len(persisted['pipelines']), 1)
        self.assertEqual(sum(len(batch['jobs']) for batch in persisted['batches']), 1)

    def test_http_pipeline_create_cancel_and_retry_are_real_routes(self):
        with self.local_http() as client:
            status, pipeline = self.post(client, '/api/pipelines', self.request())
            self.assertEqual(status, 201)
            identifier = pipeline['id']
            status, cancelled = self.post(client, f'/api/pipelines/{identifier}/cancel', {})
            self.assertEqual(status, 200)
            self.assertEqual(cancelled['status'], 'cancelled')
            status, retry = self.post(client, f'/api/pipelines/{identifier}/retry', {})
            self.assertEqual(status, 201)
            self.assertNotEqual(retry['id'], identifier)
            self.assertNotEqual(retry['imageJobId'], pipeline['imageJobId'])

    def test_retry_is_durable_with_its_origin_even_if_an_extra_save_would_fail(self):
        original = self.create()
        self.factory.cancel_pipeline(original['id'])
        with self.fail_second_save():
            try:
                self.factory.retry_pipeline(original['id'])
            except OSError:
                pass
        persisted = json.loads(self.factory.state_file.read_text(encoding='utf-8'))
        retries = [pipeline for pipeline in persisted['pipelines'] if pipeline['id'] != original['id']]
        self.assertTrue(retries)
        self.assertTrue(all(pipeline.get('retryOf') == original['id'] for pipeline in retries),
                        'Accepted retry lost its durable origin when a later redundant save failed')

    def test_repeating_retry_while_first_retry_is_pending_does_not_duplicate(self):
        original = self.create()
        self.factory.cancel_pipeline(original['id'])
        first = self.factory.retry_pipeline(original['id'])
        second = self.factory.retry_pipeline(original['id'])
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.factory.state()['pipelines']), 2)

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_real_worker_generates_one_image_then_two_chosen_models(self):
        pipeline = self.create()
        self.start_worker()
        result = self.wait_terminal(pipeline['id'])
        self.assertEqual(result['status'], 'completed')
        jobs = self.factory.state()['jobs']
        self.assertEqual([job['engine'] for job in jobs], ['image', 'mesh', 'mesh2'])
        self.assertTrue(all(job['status'] == 'generated-unreviewed' for job in jobs))
        image_receipt = self.factory._receipt(self.factory.run_root(jobs[0]))
        image_finish = datetime.fromisoformat(image_receipt['finishedAt'])
        generated_hash = hashlib.sha256((self.root / 'fixture.png').read_bytes()).hexdigest()
        model_input_ids = {job['mainImageId'] for job in jobs[1:]}
        self.assertEqual(len(model_input_ids), 1)
        self.assertTrue(result['imageUrl'].startswith('/files/outputs/image/'))
        self.assertEqual(len(result['modelJobs']), 2)
        for job in jobs[1:]:
            folder = self.factory.run_root(job)
            receipt = self.factory._receipt(folder)
            self.assertLessEqual(image_finish, datetime.fromisoformat(receipt['startedAt']))
            self.assertEqual(receipt['inputSha256'], generated_hash)
            self.assertEqual((folder / 'consumed.sha256').read_text(), generated_hash)
            self.assertEqual((folder / 'model.glb').read_bytes(), fixture_glb())

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_failed_image_with_pixels_still_never_starts_model_stage(self):
        self.install_engines(image_mode='fail-after-image')
        pipeline = self.create()
        self.start_worker()
        result = self.wait_terminal(pipeline['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['modelBatchId'])
        self.assertEqual(self.factory.state()['batches'], [])
        self.assertEqual(self.factory.state()['imageJobs'][0]['status'], 'failed')
        self.assertEqual(list((self.root / 'outputs').glob('mesh*/*/model-started.marker')), [])

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_empty_image_success_exit_never_starts_models(self):
        self.install_engines(image_mode='missing')
        pipeline = self.create()
        self.start_worker()
        result = self.wait_terminal(pipeline['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['modelBatchId'])
        self.assertEqual(self.factory.state()['batches'], [])

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_selected_model_becoming_unavailable_fails_without_substitution(self):
        pipeline = self.create(self.request(modelEngineIds=['mesh']))
        manifest_file = self.root / 'engines/mesh/engine.json'
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        manifest['status'] = 'blocked'
        manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
        self.start_worker()
        result = self.wait_terminal(pipeline['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['modelBatchId'])
        self.assertEqual(self.factory.state()['batches'], [])
        self.assertEqual([job['engine'] for job in self.factory.state()['jobs']], ['image'])

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_model_failure_is_reported_and_retry_preserves_original_pixels(self):
        self.install_engines(model_fail=True)
        original = self.create(self.request(modelEngineIds=['mesh']))
        self.start_worker()
        failed = self.wait_terminal(original['id'])
        self.assertEqual(failed['status'], 'failed')
        original_image = self.factory._find_job(original['imageJobId'])
        image_file = self.factory.run_root(original_image) / 'generated.png'
        original_bytes = image_file.read_bytes()
        original_run_id = original_image['runId']
        self.install_engines()
        retry = self.factory.retry_pipeline(original['id'])
        completed = self.wait_terminal(retry['id'])
        self.assertEqual(completed['status'], 'completed')
        self.assertNotEqual(retry['id'], original['id'])
        self.assertNotEqual(self.factory._find_job(retry['imageJobId'])['runId'], original_run_id)
        self.assertEqual(image_file.read_bytes(), original_bytes)
        self.assertEqual(self.pipeline(original['id'])['status'], 'failed')

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_handoff_write_failure_never_leaves_unlinked_queued_models(self):
        original = self.create(self.request(modelEngineIds=['mesh']))
        image_job = self.factory._find_job(original['imageJobId'])
        self.factory._execute_job(image_job)
        self.assertEqual(image_job['status'], 'generated-unreviewed')
        with self.fail_second_save():
            self.factory.pipeline.advance()
        persisted = json.loads(self.factory.state_file.read_text(encoding='utf-8'))
        linked_batches = {pipeline.get('modelBatchId') for pipeline in persisted['pipelines']}
        pending_batches = {batch['id'] for batch in persisted['batches'] if batch.get('kind') != 'image'
                           and any(job['status'] in {'queued', 'running'} for job in batch['jobs'])}
        self.assertLessEqual(pending_batches, linked_batches,
                             'Failed handoff still enqueued a model batch outside its durable chain')

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_rejected_handoff_rolls_back_models_but_preserves_completed_image(self):
        original = self.create(self.request(modelEngineIds=['mesh']))
        image_job = self.factory._find_job(original['imageJobId'])
        self.factory._execute_job(image_job)
        image_file = self.factory.run_root(image_job) / 'generated.png'
        original_pixels = image_file.read_bytes()
        real_save = self.factory._save
        attempted = False

        def save_once_failed():
            nonlocal attempted
            if not attempted:
                attempted = True
                raise OSError('CPU fixture transient disk failure')
            return real_save()

        with patch.object(self.factory, '_save', side_effect=save_once_failed):
            self.factory.pipeline.advance()
        self.assertEqual(self.pipeline(original['id'])['status'], 'failed')
        self.assertIsNone(self.pipeline(original['id'])['modelBatchId'])
        self.assertEqual(self.factory.state()['batches'], [])
        self.assertEqual(image_file.read_bytes(), original_pixels)
        self.assertEqual(image_job['status'], 'generated-unreviewed')

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_public_pipeline_reports_running_image_child_and_real_phase(self):
        self.install_engines(image_hold_ms=2000)
        pipeline = self.create(self.request(modelEngineIds=['mesh']))
        self.start_worker()
        image_job = self.factory._find_job(pipeline['imageJobId'])
        self.wait_for(lambda: (self.factory.run_root(image_job) / 'engine-started.marker').exists())
        state = self.factory.state()
        public = next(item for item in state['pipelines'] if item['id'] == pipeline['id'])
        active_image = next(job for job in state['imageJobs'] if job['id'] == image_job['id'])
        self.assertEqual(active_image['status'], 'running')
        self.assertEqual(public['status'], 'running')
        self.assertEqual(public['phase'], 'image')
        self.assertEqual(public['phaseLabel'], active_image['phase'])
        self.assertIsNone(public['progress'], 'Do not invent progress for a CPU engine without progress events')

    @unittest.skipUnless(PWSH, 'PowerShell 7 CPU fixture')
    def test_cancelling_running_image_stops_chain_and_owned_child(self):
        self.install_engines(image_hold_ms=15000)
        pipeline = self.create()
        self.start_worker()
        image_job = self.factory._find_job(pipeline['imageJobId'])
        self.wait_for(lambda: (self.factory.run_root(image_job) / 'engine-started.marker').exists())
        process = self.factory.process
        self.factory.cancel_pipeline(pipeline['id'])
        self.wait_for(lambda: image_job['status'] == 'cancelled')
        self.assertEqual(self.pipeline(pipeline['id'])['status'], 'cancelled')
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.factory.state()['batches'], [])

    def test_app_close_pauses_pipeline_image_and_does_not_autostart_on_reopen(self):
        pipeline = self.create()
        self.factory.close(pause_pending=True)
        persisted = json.loads(self.factory.state_file.read_text(encoding='utf-8'))
        self.assertEqual(persisted['pipelines'][0]['status'], 'interrupted')
        self.factory = Factory(self.root, start_worker=False)
        self.assertEqual(self.pipeline(pipeline['id'])['status'], 'interrupted')
        self.assertEqual(self.factory.state()['imageJobs'][0]['status'], 'interrupted')
        self.assertFalse((self.root / 'outputs').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
