"""SPAR3D fixed-source installer and single-image contracts. v0.7.0 / Unclecow."""
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    path = ROOT/'scripts/providers'/name
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SparTests(unittest.TestCase):
    def test_msvc_build_flags_use_real_cuda_and_cpp_extensions(self):
        helper = module('spar-setup.py')
        cuda = helper.extension_setup('texture_baker')
        uv = helper.extension_setup('uv_unwrapper')
        self.assertIn('CUDAExtension', cuda)
        self.assertIn('CppExtension', uv)
        self.assertIn('/openmp', uv)
        for invalid in ('-fopenmp', '-march=native', '-fdiagnostics-color'):
            self.assertNotIn(invalid, cuda+uv)
        self.assertIn('use_ninja=False', cuda)

    def test_source_patch_rejects_unknown_content(self):
        helper = module('spar-setup.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'target.py'
            path.write_text('changed', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '源'):
                helper.patch_checked(path, '0'*64, 'patched')
            self.assertEqual(path.read_text(), 'changed')

    def test_single_reference_contract_does_not_accept_joint_views(self):
        adapter = module('spar-run.py')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            from PIL import Image
            Image.new('RGBA', (64,64), (255,0,0,255)).save(root/'image.png')
            result = adapter.validate_input(root, str(root/'image.png'), None)
            self.assertEqual(result, root/'image.png')
            views = root/'views.json'
            views.write_text(json.dumps({'front': str(root/'image.png'), 'back': str(root/'image.png')}))
            with self.assertRaisesRegex(ValueError, '单图'):
                adapter.validate_input(root, str(root/'image.png'), str(views))

    def test_config_requires_supported_dino_and_exact_alpha_clip_weights(self):
        helper = module('spar-setup.py')
        self.assertEqual(helper.check_auxiliary_config({'image_estimator': {'model':'ViT-L/14@336px'}})['clipFile'], 'ViT-L-14-336px.pt')
        with self.assertRaisesRegex(ValueError, 'DINO'):
            helper.check_auxiliary_config({'image_tokenizer': {'pretrained_model_name_or_path':'another/model'}})
        with self.assertRaisesRegex(ValueError, 'CLIP'):
            helper.check_auxiliary_config({'image_estimator': {'model':'another/model'}})

    def test_portable_sdk_path_cannot_be_shadowed_by_mixed_case_environment(self):
        helper = module('spar-setup.py')
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            for name in ('cuda-12.4/bin/nvcc.exe','windows-sdk/microsoft.windows.sdk.cpp/c/Include/10.0.26100.0/ucrt/stddef.h'):
                path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
            result=SimpleNamespace(stdout='Path=inherited-path\nINCLUDE=compiler-include\nLIB=compiler-lib\n')
            with patch.object(helper,'detect_msvc',return_value=Path('fixture-vcvars.bat')),patch.object(helper.subprocess,'run',return_value=result):
                env=helper.compiler_environment(base)
            self.assertNotIn('Path',env)
            self.assertIn('c/bin/10.0.26100.0/x64',env['PATH'].replace('\\','/'))
            self.assertEqual(env['TORCH_CUDA_ARCH_LIST'],'8.6')

    def test_cpu_generation_is_rejected_before_loading_source_or_weights(self):
        adapter=module('spar-run.py')
        fake=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with patch.dict('sys.modules',{'torch':fake}),self.assertRaisesRegex(RuntimeError,'CUDA'):
            adapter.run(ROOT,ROOT/'inputs/unused.png',ROOT/'outputs/unused')

    def test_catalog_is_pinned_authorized_and_uses_independent_source(self):
        catalog = json.loads((ROOT/'config/spar-models.json').read_text(encoding='utf-8'))
        model = next(x for x in catalog['models'] if x['id']=='spar3d')
        self.assertTrue(model['authRequired'])
        self.assertFalse(model['gpuVerified'])
        self.assertEqual(model['appearance'], 'texture')
        self.assertEqual(model['capabilities']['maxReferenceImages'], 1)
        self.assertFalse(model['runtime']['jointMultiview'])
        self.assertIn('spar-build-tools', model['dependencies'])
        for item in catalog['models']:
            for f in item['files']:
                self.assertTrue(f.get('sha256') or f.get('gitBlobSha1'))
                self.assertGreater(f['size'], 0)
                self.assertNotIn('/resolve/main/', f['url'])
                if f.get('authRequired'):
                    self.assertFalse(f.get('mirrors'))


if __name__=='__main__':
    unittest.main()
