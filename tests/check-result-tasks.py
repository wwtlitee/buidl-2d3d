"""Isolated result-task, weight-variant and deletion checks. v0.2.0 / Unclecow.

Only small fake files under TemporaryDirectory are created or deleted.
No engine process, real output, browser, model, or GPU is touched.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import http.client
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_queue import Factory
from factory_web import FactoryServer, Handler


class ResultTaskChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='factory-task-contract-')
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'VERSION.json').write_text(json.dumps({'version': '0.2.0'}), encoding='utf-8')
        (self.root / 'config').mkdir()
        # Fake qwen IDs must not inherit the shipped real-weight deployment catalog.
        (self.root / 'config/setup-catalog.json').write_text('{"models":[]}', encoding='utf-8')
        (self.root / 'config/python-models.json').write_text('{"models":[]}', encoding='utf-8')
        for engine, kind, multiview in (('mesh-a', 'model', False), ('mesh-b', 'model', True),
                                         ('generic-image', 'image', False), ('qwen-image-21', 'image', False)):
            folder = self.root / 'engines' / engine
            folder.mkdir(parents=True)
            (folder / 'run.ps1').write_text("throw 'Fixture must not run'\n", encoding='utf-8')
            manifest = {'id': engine, 'title': engine, 'version': 'fixture', 'status': 'ready',
                        'kind': kind, 'entrypoint': 'run.ps1',
                        'capabilities': {'multiView': multiview, 'views': ['left', 'back', 'right'] if multiview else []}}
            if engine == 'qwen-image-21':
                variants = []
                for variant, status, verified, gpu_verified, legacy in (
                    ('Q4_K', 'ready', True, True, True), ('Q4_K_M', 'installed', True, False, False),
                    ('Q6_K', 'installing', False, False, False), ('missing', 'ready', True, False, False)):
                    relative = f'engines/{engine}/runtime/models/{variant}.gguf'
                    if status in {'ready', 'installed'} and variant != 'missing':
                        weight = self.root / relative
                        weight.parent.mkdir(parents=True, exist_ok=True)
                        weight.write_bytes(b'fixture!')
                    variants.append({'id': variant, 'label': variant, 'bytes': 8, 'path': relative,
                                     'status': status, 'verified': verified, 'gpuVerified': gpu_verified, 'legacy': legacy})
                manifest.update(variants=variants, defaultWeightVariant='Q4_K_M')
            (folder / 'engine.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.factory = Factory(self.root, start_worker=False)

    def tearDown(self):
        self.factory.close()
        self.temporary.cleanup()

    def upload(self, name='main.png'):
        stream = BytesIO()
        Image.new('RGBA', (12, 10), (17, 103, 219, 255)).save(stream, format='PNG')
        return self.factory.upload_image(stream.getvalue(), name)

    def batch(self, *, objects=None, engines=None):
        if objects is None:
            main, left, back = (self.upload(name) for name in ('front.png', 'left.png', 'back.png'))
            objects = [{'name': '机械小牛', 'mainImageId': main['id'], 'auxiliaryImages': [
                {'view': 'left', 'imageId': left['id']}, {'view': 'back', 'imageId': back['id']}]}]
        return self.factory.create_batch({'name': 'Fixture batch', 'objects': objects, 'engineIds': engines or ['mesh-a', 'mesh-b']})

    def image_job(self, **changes):
        return self.factory.create_image_job({'engineId': 'qwen-image-21', 'prompt': '机械小牛', 'seed': 42, **changes})

    def finish(self, public_job, status='generated-unreviewed'):
        job = self.factory._find_job(public_job['id'])
        job.update(status=status, phase=status, finishedAt='2026-10-03T12:00:00+00:00', error='fixture failure' if status == 'failed' else None)
        folder = self.factory.run_root(job)
        folder.mkdir(parents=True)
        receipt = {'engine': job['engine'], 'runId': job['runId'], 'status': status,
                   'startedAt': job['createdAt'], 'inputMode': job['inputMode'], 'inputs': []}
        for view, image_id in job['viewImages'].items():
            source = self.root / self.factory.data['images'][image_id]['path']
            copy = folder / ('input.png' if view == 'front' else f'input_{view}.png')
            copy.write_bytes(source.read_bytes())
            receipt['inputs'].append({'view': view, 'source': str(source), 'path': str(copy)})
        if job.get('kind') == 'image':
            receipt['request'] = deepcopy(job['request'])
            Image.new('RGB', (12, 10), 'orange').save(folder / 'image.png')
        else:
            (folder / 'model.glb').write_bytes(b'fake GLB artifact')
            (folder / 'model.ply').write_bytes(b'fake PLY artifact')
            Image.new('RGB', (12, 10), 'orange').save(folder / 'texture.png')
        (folder / 'nested').mkdir()
        (folder / 'nested/detail.txt').write_text('attached output', encoding='utf-8')
        (folder / 'run.log').write_text('fixture native evidence', encoding='utf-8')
        (folder / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        self.factory._save()
        self.factory.history_read_at = 0
        return job

    def history_run(self, engine='mesh-a', run_id='legacy-run', *, receipt=None, with_input=True):
        folder = self.root / 'outputs' / engine / run_id
        folder.mkdir(parents=True)
        (folder / 'model.glb').write_bytes(b'fake historical model')
        if with_input:
            Image.new('RGB', (12, 10), 'blue').save(folder / 'input.jpg')
        value = {'engine': engine, 'runId': run_id, 'status': 'generated-unreviewed',
                 'startedAt': '2026-09-30T12:00:00+00:00', **(receipt or {})}
        (folder / 'receipt.json').write_text(json.dumps(value), encoding='utf-8')
        self.factory.history_read_at = 0
        return folder

    def task_for(self, job):
        return next(task for task in self.factory.state()['resultTasks'] if any(item['id'] == job['id'] for item in task['jobs']))

    @contextmanager
    def local_http(self):
        with patch.object(Handler, 'log_message', lambda *_: None):
            server = FactoryServer(('127.0.0.1', 0), self.factory)
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

    def test_http_delete_without_action_header_is_rejected(self):
        job = self.finish(self.image_job())
        folder = self.factory.run_root(job)
        before = deepcopy(self.factory.data)
        with self.local_http() as connection:
            connection.request('POST', f"/api/image-jobs/{job['id']}/delete", body=b'{}',
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertIn('error', json.loads(response.read()))
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.factory.data, before)

    def test_http_authorized_delete_removes_entire_fixture_runs(self):
        model = self.finish(self.batch(engines=['mesh-a'])['jobs'][0])
        image = self.finish(self.image_job())
        historical = self.history_run(run_id='http-delete-history')
        folders = [self.factory.run_root(model), self.factory.run_root(image), historical]
        uploads = {path: path.read_bytes() for path in (self.root / 'inputs/uploads').glob('*')}
        endpoints = [f"/api/jobs/{model['id']}/delete", f"/api/image-jobs/{image['id']}/delete",
                     '/api/history/mesh-a~http-delete-history/delete']
        with self.local_http() as connection:
            for endpoint, folder in zip(endpoints, folders):
                connection.request('POST', endpoint, body=b'{}',
                                   headers={'Content-Type': 'application/json', 'X-Factory-Request': '1'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(json.loads(response.read())['deleted'])
                self.assertFalse(folder.exists())
        self.assertEqual({path: path.read_bytes() for path in uploads}, uploads)
        self.assertEqual(self.factory.state()['resultTasks'], [])

    def test_http_task_zip_contains_one_object_only(self):
        first, other = self.upload('first.png'), self.upload('other.png')
        batch = self.batch(objects=[{'name': name, 'mainImageId': image['id']} for name, image in (('A', first), ('B', other))])
        for job in batch['jobs']:
            self.finish(job)
        task = next(task for task in self.factory.state()['resultTasks'] if task['name'] == 'A')
        with self.local_http() as connection:
            connection.request('GET', task['downloadUrl'])
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn('attachment', response.getheader('Content-Disposition'))
            body = response.read()
        with zipfile.ZipFile(BytesIO(body)) as compressed:
            names = compressed.namelist()
        self.assertTrue(names)
        for job in batch['jobs']:
            self.assertEqual(any(job['runId'] in name for name in names), job['objectName'] == 'A')

    def test_variants_expose_exact_fields_without_claiming_gpu_acceptance(self):
        engine = next(item for item in self.factory.engines() if item['id'] == 'qwen-image-21')
        self.assertEqual(engine['defaultWeightVariant'], 'Q4_K_M')
        variants = {item['id']: item for item in engine['weightVariants']}
        self.assertEqual(set(variants['Q4_K_M']), {'id', 'label', 'bytes', 'status', 'verified', 'gpuVerified', 'legacy'})
        self.assertEqual((variants['Q4_K_M']['status'], variants['Q4_K_M']['verified'], variants['Q4_K_M']['gpuVerified']), ('installed', True, False))
        self.assertTrue(variants['Q4_K']['legacy'])
        self.assertEqual(variants['missing']['status'], 'blocked')
        self.assertFalse(variants['missing']['verified'])

    def test_missing_variant_uses_original_q4_and_generic_has_no_added_field(self):
        job = self.image_job()
        self.assertEqual(job['request']['weightVariant'], 'Q4_K')
        generic = self.image_job(engineId='generic-image')
        self.assertNotIn('weightVariant', generic['request'])
        engine = next(item for item in self.factory.engines() if item['id'] == 'generic-image')
        self.assertNotIn('weightVariants', engine)

    def test_explicit_unknown_uninstalled_and_invalid_variants_never_downgrade(self):
        before = deepcopy(self.factory.data)
        for variant in ('Q6_K', 'missing', 'unknown', 'q4_k', '', None, False, [], {}):
            with self.subTest(variant=repr(variant)):
                with self.assertRaises(ValueError):
                    self.image_job(weightVariant=variant)
                self.assertEqual(self.factory.data, before)
        with self.assertRaises(ValueError):
            self.image_job(engineId='generic-image', weightVariant='Q4_K')

    def test_variant_path_and_size_are_verified_within_engine_models(self):
        file = self.root / 'engines/qwen-image-21/runtime/models/Q4_K_M.gguf'
        file.write_bytes(b'wrong size')
        with self.assertRaises(ValueError):
            self.image_job(weightVariant='Q4_K_M')
        manifest_path = self.root / 'engines/qwen-image-21/engine.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        selected = next(item for item in manifest['variants'] if item['id'] == 'Q4_K_M')
        (self.root / 'outside.gguf').write_bytes(b'fixture!')
        selected['path'] = 'outside.gguf'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.image_job(weightVariant='Q4_K_M')

    def test_variant_and_object_identity_survive_retry_and_restart(self):
        job = self.image_job(weightVariant='Q4_K_M')
        self.factory.cancel_job(job['id'])
        retry = self.factory.retry_job(job['id'])
        self.assertEqual(retry['request'], job['request'])
        self.assertEqual(retry['objectId'], job['objectId'])
        self.factory.close()
        self.factory = Factory(self.root, start_worker=False)
        restored = self.factory._find_job(retry['id'])
        self.assertEqual(restored['request']['weightVariant'], 'Q4_K_M')
        self.assertEqual(len(self.factory.state()['resultTasks']), 1)
        self.assertEqual(len(self.factory.state()['resultTasks'][0]['jobs']), 2)

    def test_old_image_request_migrates_to_legacy_q4_without_changing_selected_variant(self):
        old = self.image_job()
        explicit = self.image_job(weightVariant='Q4_K_M')
        self.factory._find_job(old['id'])['request'].pop('weightVariant')
        self.factory._save()
        self.factory.close()
        self.factory = Factory(self.root, start_worker=False)
        self.assertEqual(self.factory._find_job(old['id'])['request']['weightVariant'], 'Q4_K')
        self.assertEqual(self.factory._find_job(explicit['id'])['request']['weightVariant'], 'Q4_K_M')

    def test_retry_rejects_missing_selected_weight_instead_of_using_legacy(self):
        job = self.image_job(weightVariant='Q4_K_M')
        self.factory.cancel_job(job['id'])
        (self.root / 'engines/qwen-image-21/runtime/models/Q4_K_M.gguf').unlink()
        before = deepcopy(self.factory.data)
        with self.assertRaises(ValueError):
            self.factory.retry_job(job['id'])
        self.assertEqual(self.factory.data, before)
        self.assertEqual(self.factory._find_job(job['id'])['request']['weightVariant'], 'Q4_K_M')

    def test_history_transmits_actual_receipt_variant_and_prompt(self):
        job = self.finish(self.image_job(weightVariant='Q4_K_M'))
        item = next(item for item in self.factory.history() if item['runId'] == job['runId'])
        self.assertEqual(item['weightVariant'], 'Q4_K_M')
        self.assertEqual(item['request']['prompt'], '机械小牛')
        task = self.task_for(job)
        self.assertEqual((task['kind'], task['inputMode'], task['prompt'], task['weightVariant']), ('image', 'text', '机械小牛', 'Q4_K_M'))
        self.assertEqual(task['inputs'], [])

    def test_one_object_groups_all_tools_states_inputs_and_multiple_outputs(self):
        batch = self.batch()
        self.finish(batch['jobs'][0])
        self.finish(batch['jobs'][1], 'failed')
        state = self.factory.state()
        self.assertEqual(len(state['resultTasks']), 1)
        task = state['resultTasks'][0]
        self.assertEqual((task['name'], task['batchId'], task['source'], task['inputMode']), ('机械小牛', batch['id'], 'queue', 'multi-view'))
        self.assertEqual([item['view'] for item in task['inputs']], ['front', 'left', 'back'])
        self.assertEqual([item['name'] for item in task['inputs']], ['front.png', 'left.png', 'back.png'])
        self.assertTrue(all(item['url'].startswith('/files/inputs/uploads/') for item in task['inputs']))
        self.assertEqual({job['status'] for job in task['jobs']}, {'generated-unreviewed', 'failed'})
        self.assertEqual(sum(asset['kind'] == 'model' for job in task['jobs'] for asset in job['assets']), 2)
        self.assertEqual(sum(asset['kind'] == 'texture' for job in task['jobs'] for asset in job['assets']), 1)
        self.assertTrue(task['downloadUrl'].startswith('/api/result-tasks/'))

    def test_queued_and_running_tools_remain_in_result_task(self):
        batch = self.batch()
        self.factory._find_job(batch['jobs'][1]['id'])['status'] = 'running'
        task = self.task_for(batch['jobs'][0])
        self.assertEqual({job['status'] for job in task['jobs']}, {'queued', 'running'})
        self.assertIsNone(task['downloadUrl'])

    def test_new_objects_with_same_main_image_remain_distinct(self):
        image = self.upload()
        self.batch(objects=[{'name': name, 'mainImageId': image['id']} for name in ('一个物体', '另一个物体')])
        tasks = self.factory.state()['resultTasks']
        self.assertEqual(len(tasks), 2)
        self.assertEqual({task['name'] for task in tasks}, {'一个物体', '另一个物体'})
        self.assertTrue(all(len(task['jobs']) == 2 for task in tasks))

    def test_old_jobs_without_object_id_group_by_main_only_inside_batch(self):
        image = self.upload()
        for name in ('第一批', '第二批'):
            batch = self.batch(objects=[{'name': name, 'mainImageId': image['id']}])
            for job in batch['jobs']:
                self.factory._find_job(job['id']).pop('objectId')
        self.assertEqual(len(self.factory.state()['resultTasks']), 2)
        self.assertTrue(all(len(task['jobs']) == 2 for task in self.factory.state()['resultTasks']))

    def test_history_runs_never_merge_by_identical_input_or_duplicate_queue_run(self):
        batch = self.batch(engines=['mesh-a'])
        job = self.finish(batch['jobs'][0])
        self.history_run(run_id='independent-a')
        self.history_run(run_id='independent-b')
        tasks = self.factory.state()['resultTasks']
        self.assertEqual(len(tasks), 3)
        self.assertEqual(sum(task['source'] == 'queue' for task in tasks), 1)
        historical = [task for task in tasks if task['source'] == 'history']
        self.assertEqual(len(historical), 2)
        self.assertTrue(all(task['jobs'][0]['historyId'] == task['jobs'][0]['id'] for task in historical))
        self.assertEqual(sum(item['runId'] == job['runId'] for task in tasks for item in task['jobs']), 1)

    def test_history_uses_receipt_views_and_copy_fallback_without_inventing_unknown_input(self):
        folder = self.history_run(run_id='multi-history', with_input=False)
        source = self.root / 'inputs/source-left.png'
        source.parent.mkdir()
        Image.new('RGB', (12, 10), 'blue').save(source)
        Image.new('RGB', (12, 10), 'green').save(folder / 'input.png')
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        receipt.update(inputMode='multi-view', inputs=[{'view': 'left', 'source': str(source)}])
        (folder / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        self.history_run(run_id='unknown-input', with_input=False,
                         receipt={'source': str(self.root.parent / 'unavailable.png')})
        tasks = self.factory.state()['resultTasks']
        multi = next(task for task in tasks if task['jobs'][0]['runId'] == 'multi-history')
        unknown = next(task for task in tasks if task['jobs'][0]['runId'] == 'unknown-input')
        self.assertEqual([item['view'] for item in multi['inputs']], ['front', 'left'])
        self.assertEqual(unknown['inputs'], [])

    def test_task_download_contains_only_selected_object_runs(self):
        image_a, image_b = self.upload('a.png'), self.upload('b.png')
        batch = self.batch(objects=[{'name': name, 'mainImageId': image['id']} for name, image in (('A', image_a), ('B', image_b))])
        for job in batch['jobs']:
            self.finish(job)
        task = next(task for task in self.factory.state()['resultTasks'] if task['name'] == 'A')
        archive = self.factory.archive(task_id=task['id'])
        with zipfile.ZipFile(archive) as compressed:
            names = compressed.namelist()
        self.assertTrue(names)
        for job in batch['jobs']:
            self.assertEqual(any(job['runId'] in name for name in names), job['objectName'] == 'A')

    def test_delete_job_removes_whole_run_and_preserves_other_run_and_uploads(self):
        batch = self.batch()
        first, other = (self.finish(job) for job in batch['jobs'])
        inputs = {path: path.read_bytes() for path in (self.root / 'inputs/uploads').glob('*')}
        other_files = {path: path.read_bytes() for path in self.factory.run_root(other).rglob('*') if path.is_file()}
        first_folder = self.factory.run_root(first)
        reply = self.factory.delete_job(first['id'])
        self.assertEqual((reply['id'], reply['status'], reply['deleted']), (first['id'], 'deleted', True))
        self.assertFalse(first_folder.exists())
        self.assertEqual({path: path.read_bytes() for path in inputs}, inputs)
        self.assertEqual({path: path.read_bytes() for path in other_files}, other_files)
        task = self.factory.state()['resultTasks'][0]
        self.assertEqual([job['id'] for job in task['jobs']], [other['id']])
        self.assertEqual(self.factory._find_job(first['id'])['status'], 'deleted')
        self.assertNotIn('logTail', self.factory._find_job(first['id']))

    def test_delete_image_and_history_synchronize_tombstones_and_restart(self):
        image = self.finish(self.image_job())
        image_folder = self.factory.run_root(image)
        self.factory.delete_history(f"{image['engine']}~{image['runId']}")
        self.assertFalse(image_folder.exists())
        self.assertEqual(self.factory._find_job(image['id'])['status'], 'deleted')
        public = self.factory.state()
        for key in ('resultTasks', 'jobs', 'imageJobs', 'batches'):
            self.assertEqual(public[key], [])
        self.factory.close()
        self.factory = Factory(self.root, start_worker=False)
        self.assertEqual(self.factory.state()['resultTasks'], [])
        history = self.history_run(run_id='delete-legacy')
        self.factory.delete_history('mesh-a~delete-legacy')
        self.assertFalse(history.exists())
        self.assertEqual(self.factory.state()['resultTasks'], [])

    def test_delete_all_terminal_states_is_allowed_but_active_is_rejected(self):
        for status in ('failed', 'cancelled', 'interrupted', 'generated-unreviewed'):
            job = self.finish(self.image_job(), status)
            self.assertTrue(self.factory.delete_job(job['id'])['deleted'])
        for status in ('queued', 'running'):
            job = self.image_job()
            original = self.factory._find_job(job['id'])
            original['status'] = status
            saved = deepcopy(original)
            with self.assertRaises(ValueError):
                self.factory.delete_job(job['id'])
            self.assertEqual(original, saved)

    def test_delete_rejects_invalid_run_and_foreign_receipt_without_touching_files(self):
        job = self.finish(self.image_job())
        record = self.factory._find_job(job['id'])
        original_run = record['runId']
        folder = self.factory.run_root(record)
        for run_id in ('', '..', '../other', 'a/b', 'a\\b', str(self.root / 'inputs')):
            record['runId'] = run_id
            saved = deepcopy(record)
            with self.assertRaises(ValueError):
                self.factory.delete_job(record['id'])
            self.assertEqual(record, saved)
            self.assertTrue(folder.is_dir())
        record['runId'] = original_run
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        receipt['runId'] = 'another-run'
        (folder / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.factory.delete_job(record['id'])
        for history_id in ('mesh-a~../escape', '../mesh-a~run', 'mesh-a~', 'unknown~run'):
            with self.assertRaises(ValueError):
                self.factory.delete_history(history_id)
        self.assertTrue(folder.is_dir())

    def test_delete_failure_does_not_report_success_or_change_record(self):
        job = self.finish(self.image_job())
        record = self.factory._find_job(job['id'])
        saved = deepcopy(record)
        persisted = self.factory.state_file.read_bytes()
        with patch('factory_queue.shutil.rmtree', side_effect=PermissionError('fixture denial')):
            with self.assertRaises(PermissionError):
                self.factory.delete_job(job['id'])
        self.assertEqual(record, saved)
        self.assertEqual(self.factory.state_file.read_bytes(), persisted)
        self.assertTrue(self.factory.run_root(job).is_dir())

    def test_delete_history_rejects_active_receipt_and_active_queue_reference(self):
        folder = self.history_run(run_id='running-history', receipt={'status': 'running'})
        with self.assertRaises(ValueError):
            self.factory.delete_history('mesh-a~running-history')
        self.assertTrue(folder.is_dir())
        job = self.finish(self.image_job())
        self.factory._find_job(job['id'])['status'] = 'running'
        with self.assertRaises(ValueError):
            self.factory.delete_history(f"{job['engine']}~{job['runId']}")
        self.assertTrue(self.factory.run_root(job).is_dir())

    def test_resolved_run_redirect_is_rejected_before_any_removal(self):
        job = self.finish(self.image_job())
        folder = self.factory.run_root(job)
        other = self.history_run(run_id='other-run')
        actual_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return actual_resolve(other) if path == folder else actual_resolve(path, *args, **kwargs)

        with patch.object(Path, 'resolve', resolve), patch('factory_queue.shutil.rmtree') as remove:
            with self.assertRaisesRegex(ValueError, '重定向'):
                self.factory.delete_job(job['id'])
            remove.assert_not_called()
        self.assertTrue(folder.is_dir())
        self.assertTrue(other.is_dir())

    def test_save_failure_after_deletion_restores_original_metadata_and_raises(self):
        job = self.finish(self.image_job())
        record = self.factory._find_job(job['id'])
        saved = deepcopy(record)
        persisted = self.factory.state_file.read_bytes()
        with patch.object(self.factory, '_save', side_effect=OSError('fixture save failure')):
            with self.assertRaises(OSError):
                self.factory.delete_job(job['id'])
        self.assertEqual(record, saved)
        self.assertEqual(self.factory.state_file.read_bytes(), persisted)


if __name__ == '__main__':
    unittest.main(verbosity=2)
