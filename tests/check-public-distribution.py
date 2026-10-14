"""Public 0.9.1 distribution contracts. No model download, credentials or GPU use."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from factory_setup import FactorySetup

def read(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))

def files_digest(model):
    canonical=json.dumps(model['files'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

class PublicDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.publication=read('PUBLICATION.json')
        cls.profiles={}
        for path in (ROOT/'config').glob('*profiles.json'):
            cls.profiles.update({p['id']:p for p in json.loads(path.read_text(encoding='utf-8')).get('profiles',[])})
        cls.catalog={'models':[]}
        for name in ('setup-catalog','python-models','image3d-models','video-models','spar-models','multiview-models'):
            cls.catalog['models'].extend(read('config/'+name+'.json')['models'])
        cls.models={m['id']:m for m in cls.catalog['models']}

    def manager(self):
        temporary=tempfile.TemporaryDirectory(prefix='buidl-public-')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        shutil.copytree(ROOT/'config',root/'config')
        shutil.copy2(ROOT/'VERSION.json',root/'VERSION.json')
        hardware={'platform':'Windows','architecture':'AMD64','gpuAvailable':True,
                  'gpus':[{'name':'Test fixture, no inference','memoryMiB':32768}],
                  'ramGiB':128,'freeDiskBytes':10**13,'buildTools':{'msvc':True},'requirements':[]}
        manager=FactorySetup(root,hardware=hardware,start_worker=False)
        self.addCleanup(manager.close)
        return manager

    def test_public_version_and_ui_are_aligned(self):
        version=read('VERSION.json')
        self.assertEqual(version['version'],'0.9.1')
        self.assertEqual(version['basedOn'],'0.9.0a')
        self.assertEqual(self.publication['version'],version['version'])
        for name in self.publication['versionedConfigs']:
            self.assertEqual(read(name)['productVersion'],version['version'],name)
        html=(ROOT/'visual/index.html').read_text(encoding='utf-8')
        self.assertGreaterEqual(html.count('0.9.1'),3)
        self.assertNotIn('0.9.0a',html)

    def test_only_license_reviewed_source_bundle_is_shipped(self):
        archives={p.name for p in (ROOT/'config/deployment-bundles').glob('*.zip')}
        self.assertEqual(archives,{'triposr-0.5.0.zip'})
        with zipfile.ZipFile(ROOT/'config/deployment-bundles/triposr-0.5.0.zip') as archive:
            names=archive.namelist()
            self.assertIn('engines/triposr/src/LICENSE',names)
            self.assertIn('engines/triposr/torchmcubes/LICENSE',names)
            for name in names:
                self.assertNotIn(Path(name).suffix.lower(),{'.pyd','.dll','.exe','.safetensors','.gguf','.pt','.pth','.ckpt'})

    def test_all_missing_bundles_and_their_dependents_are_blocked(self):
        missing_profiles={pid for pid,p in self.profiles.items()
                          if any(not (ROOT/item['path']).is_file() for item in p.get('bundles',[]))}
        expected={mid for mid,m in self.models.items() if m.get('deploymentProfile') in missing_profiles}
        while True:
            expanded=expected|{mid for mid,m in self.models.items() if set(m.get('dependencies',[]))&expected}
            if expanded==expected:break
            expected=expanded
        marked={mid for mid,m in self.models.items() if m.get('publicDistribution')=='source-bundle-not-included'}
        self.assertEqual(marked,expected)
        self.assertEqual(marked,set(self.publication['excludedBundleModelIds']))
        for mid in marked:
            self.assertIs(self.models[mid].get('installable'),False,mid)

    def test_blocked_candidates_cannot_enqueue_even_with_enough_hardware(self):
        manager=self.manager()
        for mid in self.publication['excludedBundleModelIds']:
            with self.subTest(model=mid),self.assertRaises(ValueError):
                manager.install({'modelId':mid,'acceptLicense':True})
        self.assertEqual(manager.jobs,[])

    def test_default_recipes_preserve_download_pins_and_can_enqueue(self):
        manager=self.manager()
        visible={m['id']:m for m in manager.snapshot()['models']}
        for mid,expected in self.publication['unchangedDefaultDownloadPinsSha256'].items():
            with self.subTest(model=mid):
                self.assertEqual(files_digest(self.models[mid]),expected)
                self.assertTrue(visible[mid]['canInstall'],visible[mid]['blockers'])
                job=manager.install({'modelId':mid,'acceptLicense':True})
                self.assertEqual(job['status'],'queued')
                self.assertFalse(job['gpuInferenceExecuted'])
        self.assertIsNone(manager.worker)
        self.assertFalse((manager.root/'engines').exists())

    def test_clean_source_does_not_claim_a_local_install_or_gpu_acceptance(self):
        baseline=read('config/image-model-source.json')
        self.assertFalse(baseline['gpuVerified'])
        self.assertEqual(baseline['status'],'not-installed')
        self.assertNotIn('gpuVerification',baseline)
        self.assertNotIn('receipts',baseline)
        self.assertNotIn('vendorDependencies',baseline['cli'])
        for variant in read('config/image-variants.json')['variants']:
            self.assertFalse(variant['verified'])
            self.assertFalse(variant['gpuVerified'])
            self.assertEqual(variant['status'],'missing')
            self.assertNotIn('verification',variant)

    def test_frontend_dependency_and_license_files_are_complete(self):
        viewer=ROOT/'visual/vendor/model-viewer.min.js'
        self.assertEqual(hashlib.sha256(viewer.read_bytes()).hexdigest(),
                         '283b0672384614b4847636c306fc93fe4b1fcadc76d668b4e47f0ca76bcf033b')
        self.assertIn('Apache License',(ROOT/'visual/vendor/model-viewer.LICENSE').read_text(encoding='utf-8'))
        self.assertIn('MIT License',(ROOT/'LICENSE').read_text(encoding='utf-8'))
        self.assertTrue((ROOT/'docs/images/workbench.jpg').is_file())

if __name__=='__main__':
    unittest.main(verbosity=2)
