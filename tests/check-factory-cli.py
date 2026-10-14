"""Factory CLI checks against a temporary loopback HTTP fixture. v0.2.0 / Unclecow.

The fixture has no worker. Fake terminal artifacts are CPU-created small files;
the real production service and GPU are never accessed.
"""
from contextlib import redirect_stderr
import ast
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


CLI = module('factory_cli_contract', ROOT / 'scripts/factory-cli.py')
FIXTURE = module('factory_cli_temp_fixture', ROOT / 'tests/check-result-tasks.py')


class FactoryCliChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = FIXTURE.ResultTaskChecks('runTest')
        self.fixture.setUp()
        self.factory = self.fixture.factory
        self.factory._version = json.loads((ROOT / 'VERSION.json').read_text(encoding='utf-8-sig'))['version']
        self.log_patch = patch.object(FIXTURE.Handler, 'log_message', lambda *_: None)
        self.log_patch.start()
        self.server = FIXTURE.FactoryServer(('127.0.0.1', 0), self.factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.log_patch.stop()
        self.fixture.tearDown()

    def run_cli(self, *args, url=None, extra_global=(), environment=None):
        command = [sys.executable, str(ROOT / 'scripts/factory-cli.py'), '--url', url or self.url, *extra_global, *map(str, args)]
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='strict', timeout=12, env=environment)

    def successful(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), result

    def png(self, name):
        path = self.fixture.root / name
        FIXTURE.Image.new('RGB', (13, 11), 'orange').save(path)
        return path

    def finish_on_state(self, status='generated-unreviewed'):
        original = self.factory.state

        def state():
            with self.factory.lock:
                for job in self.factory._jobs():
                    if job['status'] == 'queued':
                        self.fixture.finish(job, status)
            return original()

        return patch.object(self.factory, 'state', side_effect=state)

    def test_status_tasks_and_job_are_read_only_json_queries(self):
        job = self.fixture.image_job()
        before = len(self.factory._jobs())
        state, _ = self.successful('status')
        self.assertEqual(state['jobs'][0]['id'], job['id'])
        tasks, _ = self.successful('tasks')
        self.assertEqual(tasks[0]['kind'], 'image')
        queried, _ = self.successful('job', job['id'])
        self.assertEqual(queried['runId'], job['runId'])
        self.assertEqual(len(self.factory._jobs()), before)

    def test_image_exact_variant_prompt_dimensions_seed_are_enqueued(self):
        prompt = '小牛 "引号" $(literal)\n纯色背景'
        job, result = self.successful('image', '--variant', 'Q4_K_M', '--prompt', prompt,
                                      '--width', 768, '--height', 512, '--steps', 21, '--seed', 0)
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['request'], {'prompt': prompt, 'width': 768, 'height': 512,
                                        'steps': 21, 'seed': 0, 'negativePrompt': '', 'weightVariant': 'Q4_K_M'})
        self.assertEqual(result.stderr, '')
        self.assertFalse(self.factory.run_root(job).exists())

    def test_image_omitted_variant_retains_api_legacy_default(self):
        job, _ = self.successful('image', '--prompt', '旧调用')
        self.assertEqual(job['request']['weightVariant'], 'Q4_K')

    def test_model_uploads_raw_main_and_directional_views_to_one_object(self):
        main, left, back, right = (self.png(name) for name in ('正面 图.png', 'left.png', 'back.png', 'right.png'))
        batch, _ = self.successful('model', '--name', '四视角小牛', '--main', main, '--left', left,
                                   '--back', back, '--right', right, '--engines', 'mesh-a,mesh-b')
        self.assertEqual(batch['engineIds'], ['mesh-a', 'mesh-b'])
        self.assertEqual(len(batch['objects']), 1)
        obj = batch['objects'][0]
        self.assertEqual([view['view'] for view in obj['auxiliaryImages']], ['left', 'back', 'right'])
        self.assertEqual(self.factory.data['images'][obj['mainImageId']]['name'], main.name)
        self.assertEqual({job['inputMode'] for job in batch['jobs']}, {'single', 'multi-view'})
        self.assertEqual({job['objectId'] for job in batch['jobs']}, {obj['id']})
        self.assertTrue(all(job['status'] == 'queued' for job in batch['jobs']))
        self.assertFalse((self.fixture.root / 'outputs').exists())

    def test_missing_auxiliary_rejects_before_any_upload_or_submission(self):
        result = self.run_cli('model', '--main', self.png('valid.png'), '--left', self.fixture.root / 'missing.png', '--engines', 'mesh-a')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('error', json.loads(result.stderr))
        self.assertEqual(self.factory.data['images'], {})
        self.assertEqual(self.factory._jobs(), [])

    def test_api_error_has_nonzero_exit_json_stderr_and_no_fallback(self):
        result = self.run_cli('image', '--variant', 'DO_NOT_DOWNGRADE', '--prompt', '牛')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('DO_NOT_DOWNGRADE', json.loads(result.stderr)['error'])
        self.assertEqual(self.factory._jobs(), [])

    def test_stale_fingerprint_blocks_mutation_and_never_stops_or_starts_service(self):
        self.factory.service_build = 'older-service'
        result = self.run_cli('image', '--prompt', '牛', extra_global=('--ensure-service',))
        self.assertEqual(result.returncode, 1)
        self.assertIn('指纹', json.loads(result.stderr)['error'])
        self.assertEqual(self.factory._jobs(), [])
        with patch.object(CLI.Client, 'start_service') as start:
            with self.assertRaises(CLI.ClientError):
                CLI.Client(self.url).connect(ensure_service=True)
            start.assert_not_called()

    def test_ensure_service_starts_only_after_connection_refusal_and_uses_hidden_no_browser(self):
        health = {'service': '2D3DFactory', 'version': self.factory.version(), 'serviceBuild': CLI.service_build()}
        client = CLI.Client(self.url)
        with patch.object(client, 'request', side_effect=[CLI.TransportError('refused', True), health]), patch.object(client, 'start_service') as start:
            client.connect(ensure_service=True)
            start.assert_called_once_with()
        with patch.object(client, 'request', side_effect=CLI.TransportError('busy timeout', False)), patch.object(client, 'start_service') as start:
            with self.assertRaises(CLI.ClientError):
                client.connect(ensure_service=True)
            start.assert_not_called()
        with patch.object(CLI.shutil, 'which', return_value='fixture-pwsh'), patch.object(CLI.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as launch:
            client.start_service()
        command = launch.call_args.args[0]
        self.assertIn('-NoBrowser', command)
        self.assertEqual(command[-2:], ['-Port', str(self.server.server_port)])
        self.assertEqual(launch.call_args.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertNotIn('shell', launch.call_args.kwargs)

    def test_proxy_environment_is_ignored(self):
        environment = dict(os.environ, HTTP_PROXY='http://127.0.0.1:1', http_proxy='http://127.0.0.1:1', NO_PROXY='', no_proxy='')
        state, _ = self.successful('status', environment=environment)
        self.assertIn('resultTasks', state)

    def test_non_loopback_urls_and_zero_port_are_rejected_before_connecting(self):
        for value in ('http://example.invalid:7860', 'https://127.0.0.1:7860',
                      'http://127.0.0.1:0', 'http://127.0.0.1:7860/other',
                      'http://user@127.0.0.1:7860', 'http://127.0.0.1:7860?x=1'):
            with self.subTest(url=value):
                with self.assertRaises(CLI.ClientError):
                    CLI.local_url(value)
        self.assertEqual(CLI.local_url('http://localhost:7861')[0], 'http://127.0.0.1:7861')
        result = self.run_cli('status', url='http://example.invalid:7860')
        self.assertEqual(result.returncode, 1)
        self.assertIn('本机', json.loads(result.stderr)['error'])

    def test_redirect_is_rejected_without_visiting_another_host(self):
        original = FIXTURE.Handler.do_GET

        def redirected(handler):
            if handler.path == '/api/state':
                handler.send_response(302)
                handler.send_header('Location', 'http://example.invalid/never-visit')
                handler.send_header('Content-Length', '0')
                handler.end_headers()
            else:
                original(handler)

        with patch.object(FIXTURE.Handler, 'do_GET', redirected):
            result = self.run_cli('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('重定向', json.loads(result.stderr)['error'])

    def test_wait_returns_final_assets_and_progress_only_on_stderr(self):
        with self.finish_on_state():
            result = self.run_cli('image', '--prompt', '牛', '--variant', 'Q4_K_M', '--wait')
        self.assertEqual(result.returncode, 0, result.stderr)
        final = json.loads(result.stdout)
        self.assertEqual(final['status'], 'generated-unreviewed')
        self.assertTrue(any(asset['kind'] == 'image' for asset in final['assets']))
        events = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual([event['event'] for event in events], ['submitted', 'progress'])

    def test_wait_model_returns_all_selected_tool_assets(self):
        with self.finish_on_state():
            batch, _ = self.successful('model', '--main', self.png('main.png'), '--engines', 'mesh-a,mesh-b', '--wait')
        self.assertEqual(len(batch['jobs']), 2)
        self.assertEqual(batch['status'], 'generated-unreviewed')
        self.assertEqual(sum(asset['kind'] == 'model' for asset in batch['assets']), 4)
        self.assertEqual({asset['engine'] for asset in batch['assets']}, {'mesh-a', 'mesh-b'})

    def test_wait_failure_returns_nonzero_without_automatic_retry(self):
        with self.finish_on_state('failed'):
            result = self.run_cli('image', '--prompt', '牛', '--wait')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'failed')
        self.assertEqual(len(self.factory._jobs()), 1)
        self.assertIn('error', json.loads(result.stderr.splitlines()[-1]))

    def test_explicit_cancel_retry_delete_and_history_delete_use_real_api(self):
        job = self.fixture.image_job()
        cancelled, _ = self.successful('cancel', '--job', job['id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        retried, _ = self.successful('retry', '--job', job['id'])
        self.assertEqual(retried['retryOf'], job['id'])
        deleted, _ = self.successful('delete', '--job', job['id'])
        self.assertTrue(deleted['deleted'])
        history = self.fixture.history_run(run_id='cli-delete-history')
        deleted, _ = self.successful('history-delete', '--id', 'mesh-a~cli-delete-history')
        self.assertTrue(deleted['deleted'])
        self.assertFalse(history.exists())
        self.assertEqual(self.factory._find_job(retried['id'])['status'], 'queued')

    def test_wait_timeout_does_not_cancel_and_poll_interval_is_five_seconds(self):
        job = {'id': 'a' * 32, 'runId': 'fixture', 'status': 'running', 'phase': 'fixture', 'progress': None}
        final = {**job, 'status': 'generated-unreviewed', 'assets': []}
        client = SimpleNamespace(state=unittest.mock.Mock(side_effect=[{'jobs': [job]}, {'jobs': [final]}]))
        with redirect_stderr(StringIO()), patch.object(CLI.time, 'sleep') as sleep:
            self.assertEqual(CLI.wait_jobs(client, [job])[0]['status'], 'generated-unreviewed')
            sleep.assert_called_once_with(5)
        client = SimpleNamespace(state=lambda: {'jobs': [job]})
        with redirect_stderr(StringIO()), patch.object(CLI.time, 'monotonic', side_effect=[0, 1]), patch.object(CLI.time, 'sleep') as sleep:
            with self.assertRaisesRegex(CLI.ClientError, '等待超时'):
                CLI.wait_jobs(client, [job], timeout=0.5)
            sleep.assert_not_called()

    def test_invalid_arguments_have_json_errors_before_submission(self):
        for arguments in (('image', '--prompt', '牛', '--steps', 'not-an-int'),
                          ('image', '--prompt', '牛', '--wait', '--wait-timeout', 'nan')):
            result = self.run_cli(*arguments)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertIn('error', json.loads(result.stderr))
        self.assertEqual(self.factory._jobs(), [])

    def test_cli_imports_only_standard_library(self):
        pending = [ROOT / 'scripts/factory-cli.py']
        checked = set()
        allowed_local = {'factory_capabilities', 'factory_identity'}
        while pending:
            path = pending.pop()
            if path in checked:
                continue
            checked.add(path)
            imported = set()
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    self.assertIsNotNone(node.module, 'Relative imports need explicit dependency review')
                    imported.add(node.module.split('.')[0])
            local = imported - sys.stdlib_module_names
            self.assertTrue(local.issubset(allowed_local), (path.name, local - allowed_local))
            pending.extend(ROOT / 'scripts' / (name + '.py') for name in local)

    def test_cli_identity_matches_unmodified_service_identity(self):
        from factory_queue import SERVICE_BUILD, service_fingerprint
        self.assertEqual(self.factory.service_build, SERVICE_BUILD)
        self.assertEqual(CLI.service_build(), service_fingerprint())
        client = CLI.Client(url=self.url)
        client.connect()
        self.assertEqual(client.health['serviceBuild'], SERVICE_BUILD)

    def test_identity_covers_policy_shared_helper_and_provider_changes(self):
        from factory_identity import service_fingerprint
        with tempfile.TemporaryDirectory(prefix='factory-cli-identity-') as directory:
            scripts = Path(directory) / 'scripts'
            (scripts / 'providers').mkdir(parents=True)
            for name in ('factory_capabilities.py', 'factory_identity.py', 'providers/fixture.py'):
                before = service_fingerprint(scripts)
                (scripts / name).write_text('# changed source\n', encoding='utf-8')
                self.assertNotEqual(service_fingerprint(scripts), before, name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
