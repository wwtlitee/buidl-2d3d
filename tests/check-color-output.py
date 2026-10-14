"""Declared textured modeling must return real embedded UV textures, not a white GLB."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_queue import Factory

spec = importlib.util.spec_from_file_location('color_fixture', ROOT / 'tests/check-texture-pipeline.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ColorOutputChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='factory-color-')
        self.root = Path(self.temp.name)
        (self.root / 'VERSION.json').write_text('{"version":"0.7.0"}', encoding='utf-8')
        (self.root / 'config').mkdir()
        (self.root / 'config/setup-catalog.json').write_text('{"models":[]}', encoding='utf-8')
        self.factory = Factory(self.root, start_worker=False)
        self.output = self.root / 'outputs/color-fixture/run'
        self.output.mkdir(parents=True)

    def tearDown(self):
        self.factory.close()
        self.temp.cleanup()

    def test_white_geometry_cannot_pass_a_textured_model_contract(self):
        fixture.fixture.fixture_glb(self.output / 'model.glb', skinned=False)
        with self.assertRaisesRegex(ValueError, '贴图'):
            self.factory._validate_model_color({'appearance':'texture'}, self.output)

    def test_real_embedded_texture_has_separate_unreviewed_quality(self):
        fixture.textured_fixture(self.output / 'model.glb')
        result = self.factory._validate_model_color({'appearance':'texture'}, self.output)
        self.assertEqual(result['visualQuality'], 'unreviewed')
        self.assertEqual(result['outputs'][0]['embeddedTextureImages'], 1)

    def test_explicit_geometry_can_still_finish_without_texture(self):
        fixture.fixture.fixture_glb(self.output / 'model.glb', skinned=False)
        self.assertIsNone(self.factory._validate_model_color({'appearance':'geometry'}, self.output))

    def test_owned_worker_marks_a_white_output_and_receipt_failed(self):
        (self.root / 'VERSION.json').write_text('{"version":"0.7.0"}', encoding='utf-8')
        scripts = self.root / 'scripts'
        scripts.mkdir()
        shutil.copy2(ROOT / 'scripts/factory.ps1', scripts / 'factory.ps1')
        folder = self.root / 'engines/color-fixture'
        folder.mkdir(parents=True)
        (folder / 'engine.json').write_text(json.dumps({'id':'color-fixture','title':'Color fixture',
            'kind':'model','status':'installed','appearance':'texture','entrypoint':'run.ps1'}), encoding='utf-8')
        white = self.root / 'white.glb'
        fixture.fixture.fixture_glb(white, skinned=False)
        (folder / 'run.ps1').write_text("param($InputImage,$OutputDirectory)\nCopy-Item -LiteralPath (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'white.glb') -Destination (Join-Path $OutputDirectory 'model.glb')\n", encoding='utf-8')
        self.factory.close()
        self.factory = Factory(self.root, start_worker=True)
        image = self.factory.upload_image(self._png(), 'reference.png')
        batch = self.factory.create_batch({'objects':[{'mainImageId':image['id']}], 'engineIds':['color-fixture']})
        job_id = batch['jobs'][0]['id']
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = self.factory._find_job(job_id)
            if job['status'] not in {'queued','running'}:
                break
            time.sleep(.1)
        self.assertEqual(job['status'], 'failed', job.get('error'))
        receipt = self.factory._receipt(self.factory.run_root(job))
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('贴图', receipt['error'])

    @staticmethod
    def _png():
        from io import BytesIO
        from PIL import Image
        stream = BytesIO()
        Image.new('RGB', (8,8), 'orange').save(stream, format='PNG')
        return stream.getvalue()


if __name__ == '__main__':
    unittest.main()
