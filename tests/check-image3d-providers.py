"""Extension request contracts and pinned recipes; no installs or GPU. Unclecow."""
from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProviderTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'scripts/providers/image-run.py'
        self.assertTrue(path.is_file(), 'The new image adapter is missing')
        spec = importlib.util.spec_from_file_location('new_image_provider', path)
        self.image = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.image)
        self.temp = tempfile.TemporaryDirectory(prefix='image3d-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'inputs').mkdir()
        from PIL import Image
        Image.new('RGB', (64, 64), 'red').save(self.root / 'inputs/ref.png')
        Image.new('L', (64, 64), 255).save(self.root / 'inputs/mask.png')
        self.recipe = {'id': 'fixture', 'capabilities': {'modes': ['txt2img', 'edit', 'img2img', 'inpaint', 'control'], 'maxReferenceImages': 2},
                       'defaults': {'width': 512, 'height': 512, 'steps': 4, 'cfgScale': 1, 'seed': 42},
                       'runtime': {'type': 'sd-cpp', 'family': 'klein', 'executable': 'runtime/sd-cli.exe',
                                   'components': {'diffusion': 'models/exact-Q4.gguf', 'llm': 'models/exact-text.gguf', 'vae': 'models/exact-vae.safetensors'},
                                   'extraArgs': ['--sampling-method', 'euler']}}

    def test_prompt_remains_one_argv_item_and_reference_order_is_retained(self):
        prompt = '中文 "quoted" $(never_a_shell)\nsecond line'
        request = self.image.validate_request({'prompt': prompt, 'mode': 'edit', 'imageReferences': [{'path': str(self.root/'inputs/ref.png')}, {'path': str(self.root/'inputs/ref.png')}]}, self.recipe, self.root)
        argv = self.image.build_sd_command(request, self.recipe, self.root, self.root/'image.png')
        self.assertEqual(argv[argv.index('--prompt') + 1], prompt)
        self.assertEqual(argv.count('-r'), 2)
        self.assertIn(str(self.root/'models/exact-Q4.gguf'), argv)

    def test_explicit_unsupported_mode_is_rejected_without_fallback(self):
        self.recipe['capabilities']['modes'] = ['txt2img']
        with self.assertRaisesRegex(ValueError, '模式'):
            self.image.validate_request({'prompt': 'cow', 'mode': 'edit'}, self.recipe, self.root)

    def test_edit_requires_image_and_inpaint_requires_mask(self):
        with self.assertRaisesRegex(ValueError, '参考'):
            self.image.validate_request({'prompt': 'cow', 'mode': 'edit'}, self.recipe, self.root)
        with self.assertRaisesRegex(ValueError, '蒙版'):
            self.image.validate_request({'prompt': 'cow', 'mode': 'inpaint', 'imageReferences': [{'path': str(self.root/'inputs/ref.png')}]}, self.recipe, self.root)

    def test_outside_workspace_reference_and_boolean_dimensions_are_rejected(self):
        for request in ({'prompt': 'cow', 'width': True}, {'prompt': 'cow', 'mode': 'edit', 'imageReferences': [{'path': str(self.root.parent/'outside.png')}]}, {'prompt': 'cow', 'mode': 'img2img', 'imageReferences': [{'path': str(self.root/'inputs/ref.png')}], 'strength': float('nan')}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.image.validate_request(request, self.recipe, self.root)

    def test_transparent_request_uses_documented_qwen_prompt_and_png(self):
        self.recipe['runtime']['family'] = 'qwen21'
        request = self.image.validate_request({'prompt': 'cow', 'transparentBackground': True}, self.recipe, self.root)
        self.assertIn('RGBA image with transparency', request['prompt'])
        self.assertTrue(request['transparentBackground'])

    def test_reference_pixels_are_checked_before_gpu_start(self):
        (self.root/'inputs/ref.png').write_bytes(b'bad image')
        with self.assertRaisesRegex(ValueError, '图片'):
            self.image.validate_request({'prompt': 'cow', 'mode': 'edit', 'imageReferences': [{'path': str(self.root/'inputs/ref.png')}]}, self.recipe, self.root)

    def test_generated_png_dimensions_and_requested_alpha_are_verified(self):
        from PIL import Image
        output = self.root/'image.png'
        request = {'width': 512, 'height': 512, 'transparentBackground': False}
        Image.new('RGB', (256, 512), 'red').save(output)
        with self.assertRaisesRegex(ValueError, '尺寸'):
            self.image.inspect_generated_image(output, request)
        Image.new('RGB', (512, 512), 'red').save(output)
        result = self.image.inspect_generated_image(output, request)
        self.assertEqual((result['width'], result['height']), (512, 512))
        self.assertFalse(result['hasTransparentPixels'])
        request['transparentBackground'] = True
        with self.assertRaisesRegex(ValueError, '透明'):
            self.image.inspect_generated_image(output, request)
        Image.new('RGBA', (512, 512), (255, 0, 0, 128)).save(output)
        self.assertTrue(self.image.inspect_generated_image(output, request)['hasTransparentPixels'])
        Image.new('RGBA', (512, 512), (255, 0, 0, 0)).save(output)
        with self.assertRaisesRegex(ValueError, '完全透明'):
            self.image.inspect_generated_image(output, request)

    def test_2511_uses_required_zero_condition_argument(self):
        catalog = json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))
        item = next(x for x in catalog['models'] if x['id'] == 'qwen-image-edit-2511')
        self.assertIn('qwen_image_zero_cond_t=true', item['runtime']['extraArgs'])

    def test_all_installable_files_are_pinned_and_all_modes_have_real_runners(self):
        catalog = json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))
        profiles = json.loads((ROOT/'config/image3d-profiles.json').read_text(encoding='utf-8'))
        profile_ids = {x['id'] for x in profiles['profiles']}
        ids = set()
        for item in catalog['models']:
            self.assertNotIn(item['id'], ids)
            ids.add(item['id'])
            if item.get('installable') is False or item.get('availability') in {'unreleased', 'cloud-only', 'unsupported'}:
                self.assertFalse(item.get('files'))
                self.assertTrue(item.get('remedy'))
                continue
            self.assertTrue(item.get('files') or item.get('dependencies') or item.get('deploymentProfile'))
            for file in item.get('files', []):
                if file.get('sha256'):
                    self.assertRegex(file['sha256'], r'^[a-f0-9]{64}$')
                else:
                    self.assertRegex(file.get('gitBlobSha1', ''), r'^[a-f0-9]{40}$')
                self.assertGreater(file['size'], 0)
                self.assertNotIn('/resolve/main/', file['url'])
                self.assertNotIn('/archive/refs/heads/', file['url'])
            if item['kind'] == 'image':
                self.assertTrue(item['manifest']['mediaAdapter'])
                self.assertTrue(item['capabilities']['modes'])
                self.assertTrue(item.get('required'))
            elif item['kind']=='model':
                self.assertTrue(item.get('adapters'))
                self.assertTrue(item.get('required'))
                self.assertTrue((ROOT/item['adapters'][0]['source']).is_file())
            if item.get('deploymentProfile'):
                self.assertIn(item['deploymentProfile'], profile_ids)

    def test_native_defaults_use_exact_components_and_full_checkpoint_role(self):
        catalog = json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))
        for item in catalog['models']:
            if item.get('runtime', {}).get('type') != 'sd-cpp':
                continue
            with self.subTest(engine=item['id']):
                value = {'prompt': 'a cow', 'mode': item['capabilities']['modes'][0]}
                if value['mode'] == 'edit':
                    value['imageReferences'] = [{'path': str(self.root/'inputs/ref.png')}]
                request = self.image.validate_request(value, item, self.root)
                argv = self.image.build_sd_command(request, item, self.root, self.root/'image.png')
                self.assertEqual(argv[argv.index('--width')+1], str(request['width']))
                self.assertEqual(argv[argv.index('--height')+1], str(request['height']))
                self.assertEqual(argv[argv.index('--steps')+1], str(item['defaults']['steps']))
                self.assertIn('cuda0', argv[argv.index('--backend')+1])
                components = item['runtime']['components']
                if item['runtime']['family'] in {'sdxl', 'sd35'}:
                    self.assertIn('--model', argv)
                    self.assertNotIn('--diffusion-model', argv)
                if 'edit' in item['capabilities']['modes'] and item['runtime']['family'].startswith('qwen'):
                    self.assertIn('--llm_vision', argv)
                self.assertEqual(item['runtime']['release'], 'master-948-228c707')
                self.assertNotIn('master-929', item['runtime']['executable'])

    def test_pins_are_unique_per_destination_and_gated_weights_require_auth(self):
        catalog = json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))
        pins = {}
        for item in catalog['models']:
            for file in item.get('files', []):
                with self.subTest(engine=item['id'], path=file['path']):
                    pin = (file['size'], file.get('sha256'), file.get('gitBlobSha1'))
                    if file['path'] in pins:
                        self.assertEqual(pins[file['path']], pin, 'Conflicting hashes would overwrite a different model')
                    pins[file['path']] = pin
        sd35 = next(x for x in catalog['models'] if x['id'] == 'sd35-medium')
        self.assertTrue(sd35['authRequired'])
        gated = next(x for x in sd35['files'] if x.get('sourceRepo') == 'stabilityai/stable-diffusion-3.5-medium')
        self.assertTrue(gated['authRequired'])
        self.assertFalse(gated['mirrors'], 'Authentication must not be forwarded to unreviewed mirrors')

    def test_control_and_inpaint_are_real_cli_inputs(self):
        catalog = json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))
        recipe = next(x for x in catalog['models'] if x['id'] == 'sdxl-reference-control')
        reference = str(self.root/'inputs/ref.png')
        request = self.image.validate_request({'prompt': 'cow', 'mode': 'control',
            'ipAdapterImagePath': reference, 'controlImagePath': reference}, recipe, self.root)
        argv = self.image.build_sd_command(request, recipe, self.root, self.root/'image.png')
        for flag in ('--model', '--clip_vision', '--ip-adapter', '--ip-adapter-image', '--control-net', '--control-image'):
            self.assertIn(flag, argv)
        request = self.image.validate_request({'prompt': 'cow', 'mode': 'inpaint',
            'imageReferences': [{'path': reference}], 'maskPath': str(self.root/'inputs/mask.png')}, recipe, self.root)
        argv = self.image.build_sd_command(request, recipe, self.root, self.root/'image.png')
        for flag in ('--init-img', '--strength', '--mask'):
            self.assertIn(flag, argv)

    def test_sana_profiles_and_arguments_preserve_actual_requested_resolution(self):
        catalog = json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))
        profiles = json.loads((ROOT/'config/image3d-profiles.json').read_text(encoding='utf-8'))
        for engine in ('sana-sprint-06', 'sana-sprint-16'):
            recipe = next(x for x in catalog['models'] if x['id'] == engine)
            profile = next(x for x in profiles['profiles'] if x['id'] == recipe['deploymentProfile'])
            self.assertTrue((ROOT/profile['requirements']).is_file())
            lock = (ROOT/profile['requirements']).read_text(encoding='utf-8')
            self.assertIn('torch==2.6.0+cu124', lock)
            self.assertIn('diffusers==0.35.1', lock)
            self.assertIn('--hash=sha256:', lock)
            request = self.image.validate_request({'prompt': 'cow', 'width': 768, 'height': 512}, recipe, self.root)
            args = self.image.build_sana_arguments(request, recipe['runtime'])
            self.assertEqual((args['width'], args['height']), (768, 512))
            self.assertEqual(args['num_inference_steps'], 2)
            self.assertFalse(args['use_resolution_binning'])
            self.assertEqual(args['guidance_scale'], 4.5)
            self.assertNotIn('negative_prompt', args)
            with self.assertRaisesRegex(ValueError, '反向描述'):
                self.image.validate_request({'prompt': 'cow', 'negativePrompt': 'bad'}, recipe, self.root)

    def test_hunyuan_model_plan_preserves_single_vs_joint_multiview_semantics(self):
        path = ROOT/'scripts/providers/3d-run.py'
        self.assertTrue(path.is_file(), 'The 3D adapter is missing')
        spec = importlib.util.spec_from_file_location('new_3d_provider', path)
        model = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(model)
        recipe = {'id': 'mv', 'runtime': {'family': 'hunyuan2', 'jointMultiview': True}, 'capabilities': {'maxReferenceImages': 4}, 'defaults': {'steps': 5, 'resolution': 256}}
        plan = model.validate_request({'imageReferences': [{'path': str(self.root/'inputs/ref.png'), 'view': 'front'}, {'path': str(self.root/'inputs/ref.png'), 'view': 'back'}]}, recipe, self.root)
        self.assertEqual(list(plan['views']), ['front', 'back'])
        recipe['runtime']['jointMultiview'] = False
        with self.assertRaisesRegex(ValueError, '单图'):
            model.validate_request({'imageReferences': [{'path': str(self.root/'inputs/ref.png')}, {'path': str(self.root/'inputs/ref.png')}]}, recipe, self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
