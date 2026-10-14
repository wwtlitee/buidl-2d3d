"""Video routing boundaries without model downloads. v0.7.0 / Unclecow."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_PATH = ROOT / 'scripts/providers/video_wangp.py'


class VideoProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = None
        if PROVIDER_PATH.is_file():
            spec = importlib.util.spec_from_file_location('video_wangp', PROVIDER_PATH)
            cls.provider = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.provider)

    def adapter(self):
        self.assertIsNotNone(self.provider, '视频提供器尚未实现')
        return self.provider

    def model(self, **updates):
        model = {'id': 'video-fixture', 'runtimeModelType': 'exact_fixture',
                 'capabilities': {'modes': ['txt2video', 'img2video', 'reference-video'],
                                  'maxReferenceImages': 2, 'endImage': True},
                 'defaults': {'width': 640, 'height': 384, 'frames': 33, 'fps': 16, 'steps': 8},
                 'limits': {'sizeMultiple': 32, 'maxPixels': 640 * 480,
                            'frameRule': {'multiple': 8, 'offset': 1, 'min': 17, 'max': 97},
                            'maxSteps': 50, 'fps': [16, 24]}, 'settings': {}}
        model.update(updates)
        return model

    def test_exact_selected_model_and_precision_are_not_substituted(self):
        settings = self.adapter().build_settings({'prompt': '一头牛', 'mode': 'txt2video'}, self.model(), {'model_type': 'wrong'})
        self.assertEqual(settings['model_type'], 'exact_fixture')
        self.assertEqual(settings['resolution'], '640x384')
        self.assertEqual(settings['video_length'], 33)

    def test_rejects_unbounded_or_incompatible_dimensions_and_frames(self):
        for update in ({'width': 641}, {'width': 4096, 'height': 4096}, {'frames': 32}, {'frames': 1001}, {'steps': 0}, {'fps': 60}, {'seed': True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.adapter().build_settings({'prompt': 'cow', **update}, self.model(), {})

    def test_missing_image_cannot_turn_image_generation_into_text_generation(self):
        with self.assertRaises(ValueError):
            self.adapter().build_settings({'prompt': 'cow', 'mode': 'img2video'}, self.model(), {})

    def test_first_and_last_image_have_timeline_anchor_roles(self):
        references = [{'path': 'first.png'}, {'path': 'last.png'}]
        settings = self.adapter().build_settings({'prompt': 'cow', 'mode': 'img2video', 'imageReferences': references}, self.model(), {})
        self.assertEqual(settings['image_start'], ['first.png'])
        self.assertEqual(settings['image_end'], ['last.png'])
        self.assertEqual(settings['image_prompt_type'], 'SE')

    def test_reference_images_are_not_first_frame_anchors(self):
        settings = self.adapter().build_settings({'prompt': 'cow', 'mode': 'reference-video', 'imageReferences': [{'path': 'ref.png'}]}, self.model(), {})
        self.assertEqual(settings['image_refs'], ['ref.png'])
        self.assertNotIn('image_start', settings)
        self.assertIn('I', settings['video_prompt_type'])

    def test_unsupported_mode_or_extra_reference_is_rejected(self):
        for request in ({'mode': 'other'}, {'mode': 'img2video', 'imageReferences': [{'path': '1'}, {'path': '2'}, {'path': '3'}]}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.adapter().build_settings({'prompt': 'cow', **request}, self.model(), {})

    def test_preflight_missing_file_reports_setup_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, '模型与环境'):
                self.adapter().verify_installed(Path(directory), {'required': [{'path': 'video-lab/missing.safetensors'}]})

    def test_preflight_rejects_weight_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'weight').write_bytes(b'broken')
            with self.assertRaisesRegex(RuntimeError, '校验'):
                self.adapter().verify_installed(root, {'required': [{'path': 'weight', 'size': 6, 'sha256': '0' * 64}]})

    def test_deployed_ps_entry_resolves_adapter_from_factory_root(self):
        source = (ROOT / 'scripts/providers/video_wangp.ps1').read_text(encoding='utf-8')
        self.assertIn("Join-Path $factoryRoot 'scripts/providers/video_wangp.py'", source)
        self.assertNotIn("Join-Path $PSScriptRoot 'video_wangp.py'", source)

    def test_preflight_accepts_declared_required_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.adapter().verify_installed(Path(directory), {'required': [{'path': '.', 'directory': True}]})

    def test_generation_stops_before_session_when_cuda_is_unavailable(self):
        fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'torch': fake}), patch.object(self.adapter(), 'open_session') as open_session:
            with self.assertRaisesRegex(RuntimeError, 'CUDA'):
                self.adapter().run(Path(directory), self.model(), {'prompt': 'cow'}, Path(directory)/'output')
            open_session.assert_not_called()

    def test_distilled_steps_and_reference_video_mode_are_exact(self):
        model = self.model()
        model['limits']['allowedSteps'] = [8]
        for steps in (1, 7, 9):
            with self.subTest(steps=steps), self.assertRaisesRegex(ValueError, '固定'):
                self.adapter().build_settings({'prompt': 'cow', 'steps': steps}, model, {})
        model['capabilities']['referenceVideo'] = True
        settings = self.adapter().build_settings({'prompt': 'cow', 'mode': 'reference-video', 'referenceVideoPath': 'ref.mp4'}, model, {})
        self.assertEqual(settings['video_prompt_type'], 'V-U')

    def test_catalog_downloads_have_fixed_revision_hash_and_bounds(self):
        source = ROOT / 'config/video-models.json'
        self.assertTrue(source.is_file(), '视频安装目录尚未实现')
        catalog = json.loads(source.read_text(encoding='utf-8'))
        installable = [model for model in catalog['models'] if model.get('installable', True)]
        self.assertGreaterEqual(len(installable), 8)
        for model in installable:
            if model['kind'] == 'utility':
                continue
            self.assertEqual(model['kind'], 'video')
            self.assertFalse(model.get('gpuVerified', False))
            self.assertIn('txt2video', model['capabilities']['modes'] + (['txt2video'] if model.get('inputOnly') else []))
            for item in model['files']:
                self.assertRegex(item['sha256'], '^[a-f0-9]{64}$')
                self.assertGreater(item['size'], 0)
                self.assertNotIn('/resolve/main/', item['url'])
            self.assertGreater(model['limits']['maxPixels'], 0)

    def test_released_independent_candidates_are_explicitly_not_installable(self):
        catalog = json.loads((ROOT/'config/video-models.json').read_text(encoding='utf-8'))
        for engine in ('video-framepack', 'video-cogvideox15', 'video-sana20-preview'):
            model = next(x for x in catalog['models'] if x['id'] == engine)
            self.assertFalse(model['installable'])
            self.assertEqual(model['releaseStatus'], 'released')
            self.assertEqual(model['validationStage'], 'adapter-not-integrated')
            self.assertFalse(model['files'])
            self.assertIn('独立', model['remedy'])

    def test_catalog_matches_real_named_variants_and_private_runtime(self):
        catalog = json.loads((ROOT/'config/video-models.json').read_text(encoding='utf-8'))
        runtime = next(x for x in catalog['models'] if x['id'] == 'wangp-video-runtime')
        self.assertEqual(runtime['kind'], 'utility')
        self.assertTrue(runtime['files'][0]['stripPrefix'].endswith('/'))
        self.assertEqual(runtime['files'][1]['extractTo'], 'video-lab/wangp/src/ffmpeg_bins')
        for model in catalog['models']:
            if model['kind'] != 'video' or not model.get('installable', True):
                continue
            self.assertIn('wangp-video-runtime', model['dependencies'])
            self.assertEqual(model['runtime']['environment'], 'video-lab/wangp/.venv')
            self.assertEqual(model['capabilities']['frameRule'], model['limits']['frameRule'])
            self.assertEqual(model['capabilities']['maxPixels'], model['limits']['maxPixels'])
            self.assertIn('Uses WanGP', model['title'])
            self.assertNotIn('@root:.', model['probes'][0]['args'])
            request = {'prompt': 'cow', 'mode': model['capabilities']['modes'][0]}
            if request['mode'] == 'img2video':
                request['imageReferences'] = [{'path': 'first.png'}]
            settings = self.adapter().build_settings(request, model, {})
            self.assertEqual(settings['model_type'], model['runtimeModelType'])
            if model['id'].startswith('video-h3-'):
                self.assertIn('_pruned', model['runtimeModelType'])
                self.assertEqual(settings['config'], 'gguf_q4_k_m,bf16')
                names = [x['path'] for x in model['files']]
                self.assertTrue(any('pruned_rank8_int8_convrot' in x for x in names))
                self.assertTrue(any('Q4_K_M.gguf' in x for x in names))
                self.assertFalse(any('nvfp4' in x.lower() for x in names))

    def test_lock_preserves_cuda_and_ort_nightly_sources(self):
        lock = (ROOT/'config/deployment-requirements/wangp-video.txt').read_text(encoding='utf-8')
        for value in ('torch==2.10.0+cu130', 'av==16.1.0', '--hash=sha256:', 'ort-cuda-13-nightly', 'triton-windows==3.6.'):
            self.assertIn(value, lock)


if __name__ == '__main__':
    unittest.main()
