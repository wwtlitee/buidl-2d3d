"""Setup API integration tests on temporary roots; no model download. v0.5.0."""
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('base_setup_fixture', ROOT/'tests/check-web-factory.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class SetupHttpTests(fixture.FactoryContractTests):
    def test_fresh_qwen_default_uses_installed_default_variant(self):
        engine={'defaultWeightVariant':'Q4_K_M','weightVariants':[{'id':'Q4_K_M','status':'installed','verified':True}]}
        try:selected=fixture.WEB.Factory._request_weight_variant(engine,{})
        except ValueError as error:self.fail('Fresh default installation should not require legacy weights: '+str(error))
        self.assertEqual(selected,'Q4_K_M')

    def test_normal_client_disconnect_does_not_raise_server_error(self):
        handler=object.__new__(fixture.WEB.Handler)
        with patch.object(fixture.WEB.BaseHTTPRequestHandler,'handle',side_effect=ConnectionAbortedError('closed tab')):
            try:handler.handle()
            except ConnectionAbortedError:self.fail('Closing a tab should not produce a server error stack')

    def test_machine_label_is_detected_on_current_computer(self):
        if not hasattr(self.factory, 'setup'):
            self.fail('Hardware detection must be wired to the local factory')
        self.factory.setup.hardware_override = {'gpuAvailable': False, 'gpus': [], 'freeDiskBytes': 10**12,
                                                'platform':'Windows','architecture':'AMD64','ramGiB':32}
        self.assertEqual(self.factory.state()['machine']['gpu'], '未检测到 NVIDIA 显卡')

    def test_setup_available_with_zero_models(self):
        for path in (self.root/'engines').glob('*/engine.json'):
            path.unlink()
        with self.local_http() as client:
            client.request('GET', '/api/setup')
            response = client.getresponse()
            body = json.loads(response.read())
        self.assertEqual(response.status, 200, body)
        self.assertIn('hardware', body)
        self.assertIn('models', body)
        self.assertIn('jobs', body)
        self.assertFalse(any(model['status'] == 'ready' for model in body['models']))

    def test_setup_install_requires_local_request_header(self):
        with self.local_http() as client:
            client.request('POST', '/api/setup/install', '{}', {'Content-Type':'application/json'})
            response = client.getresponse()
            response.read()
        self.assertEqual(response.status, 403)

    def test_setup_install_rejects_unknown_id_without_downloading(self):
        with self.local_http() as client:
            client.request('POST', '/api/setup/install', json.dumps({'modelId':'../../outside','acceptLicense':True}),
                           {'Content-Type':'application/json','X-Factory-Request':'1'})
            response = client.getresponse()
            body = json.loads(response.read())
        self.assertEqual(response.status, 400, body)

    def test_health_names_new_product_and_preserves_protocol_identity(self):
        with self.local_http() as client:
            client.request('GET', '/api/health')
            response = client.getresponse()
            body = json.loads(response.read())
        self.assertEqual(body['service'], '2D3DFactory')
        self.assertEqual(body.get('product'), 'build something')
        self.assertEqual(body.get('displayName'), '造点啥')


if __name__ == '__main__':
    # Only added tests; original suite is run separately.
    names = [name for name in SetupHttpTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(SetupHttpTests(name) for name in names))
    sys.exit(not result.wasSuccessful())
