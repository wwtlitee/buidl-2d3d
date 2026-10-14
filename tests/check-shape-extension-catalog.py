"""Shape extension deployment closures, without installing weights. Unclecow."""
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]

class ShapeCatalogChecks(unittest.TestCase):
    def setUp(self):self.models=json.loads((ROOT/'config/image3d-models.json').read_text(encoding='utf-8'))['models']
    def test_turbo_and_21_have_executable_recipes(self):
        by={m['id']:m for m in self.models}
        for key in ['hunyuan3d-mini-turbo','hunyuan3d-mv-turbo','hunyuan3d-21-shape']:
            self.assertIn(key,by,'new shape model must be installable inside the tool')
            model=by[key]
            self.assertTrue(model.get('files'))
            self.assertEqual(model['kind'],'model')
            self.assertEqual(model['appearance'],'geometry')
            self.assertTrue(model['runtime']['python'].endswith('python.exe'))
        self.assertTrue(by['hunyuan3d-mv-turbo']['capabilities']['multiView'])
        self.assertFalse(by['hunyuan3d-mini-turbo']['capabilities']['multiView'])
    def test_no_shape_profile_rewrites_old_runtime(self):
        profiles=json.loads((ROOT/'config/image3d-profiles.json').read_text(encoding='utf-8'))['profiles']
        selected=[p for p in profiles if p['id'].startswith('hunyuan-shape')]
        self.assertEqual(len(selected),2)
        for p in selected:self.assertTrue(p['environment'].startswith('model-lab/'))
    def test_turbo_vae_source_is_explicit_local_component(self):
        by={m['id']:m for m in self.models}
        self.assertIn('hunyuan3d-mini-turbo',by)
        for key in ['hunyuan3d-mini-turbo','hunyuan3d-mv-turbo']:
            self.assertIn('turboVae',by[key]['runtime'])
            self.assertNotIn('hf://',by[key]['runtime']['turboVae']['directory'])
    def test_higher_vram_or_cloud_entries_never_offer_fake_installation(self):
        by={m['id']:m for m in self.models}
        for key in ['sam3d-objects-info','hunyuan3d-cloud-info']:
            self.assertIn(key,by)
            self.assertFalse(by[key]['installable'])
            self.assertTrue(by[key]['remedy'])

if __name__=='__main__':unittest.main()
