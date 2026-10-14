"""Media contracts and credential isolation, without model downloads. Unclecow."""
from __future__ import annotations
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_queue import Factory
from factory_setup import FactorySetup, extract_archive, verify_file


class MediaExtensionChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='factory-media-')
        self.root = Path(self.temp.name)
        (self.root / 'VERSION.json').write_text('{"version":"0.7.0"}', encoding='utf-8')
        folder = self.root / 'engines/image-fixture'
        folder.mkdir(parents=True)
        (folder / 'run.ps1').write_text("throw 'not executed'", encoding='utf-8')
        manifest = {'id':'image-fixture','title':'Fixture image','kind':'image','status':'installed',
            'mediaAdapter':True, 'entrypoint':'run.ps1', 'capabilities':{
                'modes':['txt2img','edit'], 'maxInputImages':1, 'sizeMultiple':32,
                'frameRule':{'multiple':8,'offset':1,'min':9,'max':129}},
            'defaults':{'width':320,'height':256,'steps':4,'frames':17,'fps':16}}
        (folder / 'engine.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.factory = Factory(self.root, start_worker=False)

    def tearDown(self):
        self.factory.close()
        self.temp.cleanup()

    def request(self, **changes):
        return {'engineId':'image-fixture','mode':'txt2img','prompt':'一只小牛向右转头',**changes}

    def submit(self, **changes):
        self.assertTrue(hasattr(self.factory, 'create_media_job'), 'generic media queue must exist')
        return self.factory.create_media_job(self.request(**changes))

    def test_image_adapter_is_a_real_serial_queue_job(self):
        job = self.submit()
        self.assertEqual(job['kind'], 'image')
        self.assertEqual(job['request']['width'],320)
        self.assertEqual(job['status'],'queued')
        self.assertIn(job['id'],[x['id'] for x in self.factory.state()['mediaJobs']])

    def test_image_engine_discovery_exposes_precise_modes(self):
        state=self.factory.state()
        self.assertIn('mediaEngines',state)
        engine=next(x for x in state['mediaEngines'] if x['id']=='image-fixture')
        self.assertEqual(engine['capabilities']['modes'],['txt2img','edit'])

    def test_setup_color_information_comes_from_the_installed_recipe(self):
        model = {'id':'colored-fixture', 'kind':'model', 'manifest':{'appearance':'texture'}}
        hardware = {'gpuAvailable':True, 'gpus':[{'memoryMiB':12288}], 'ramGiB':32,
                    'platform':'Windows', 'architecture':'AMD64', 'freeDiskBytes':10**12}
        value = self.factory.setup._public_model(model, hardware)
        self.assertEqual(value['appearance'], 'texture')

    def test_invalid_image_dimensions_do_not_enqueue(self):
        self.assertTrue(hasattr(self.factory,'create_media_job'))
        with self.assertRaises(ValueError):self.submit(width=257)
        self.assertEqual(self.factory.state()['jobs'],[])

    def test_client_cannot_choose_arbitrary_local_reference_paths(self):
        self.assertTrue(hasattr(self.factory,'create_media_job'))
        with self.assertRaises(ValueError):self.submit(imageReferences=[{'path':'C:/private.png'}])

    def test_image_mode_requires_reference(self):
        self.assertTrue(hasattr(self.factory,'create_media_job'))
        with self.assertRaises(ValueError):self.submit(mode='edit')

    def test_input_reference_keeps_server_hash(self):
        image=BytesIO();Image.new('RGB',(32,32),'red').save(image,format='PNG')
        record=self.factory.upload_image(image.getvalue(),'a.png')
        job=self.submit(mode='edit',inputImageIds=[record['id']])
        reference=job['request']['imageReferences'][0]
        self.assertEqual(reference['sha256'],record['sha256'])
        self.assertTrue(Path(reference['path']).is_relative_to(self.root))

    def test_duplicate_request_id_does_not_start_two_generations(self):
        import uuid
        key=uuid.uuid4().hex
        first=self.submit(clientRequestId=key)
        second=self.submit(clientRequestId=key)
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(len(self.factory.state()['jobs']),1)
        with self.assertRaises(ValueError):self.submit(clientRequestId=key,prompt='不同的输入')

    def test_video_output_is_not_classified_as_data(self):
        folder=self.root/'outputs/image-fixture/test'
        folder.mkdir(parents=True)
        (folder/'receipt.json').write_text('{"kind":"video","inputMode":"txt2video"}',encoding='utf-8')
        (folder/'generated.mp4').write_bytes(b'fixture output classification only')
        asset=next(x for x in self.factory.assets(folder) if x['name']=='generated.mp4')
        self.assertEqual(asset['kind'],'video')
        self.assertIsNotNone(asset['previewUrl'])

    def test_generator_parameters_are_not_silently_discarded(self):
        job=self.submit(cfgScale=4.0,controlStrength=0.8,ipAdapterStrength=1.2)
        self.assertEqual(job['request'].get('cfgScale'),4.0)
        with self.assertRaises(ValueError):self.submit(cfgScale=float('inf'))

    def test_fixed_step_and_negative_prompt_limits_are_checked_before_queue(self):
        p=self.root/'engines/image-fixture/engine.json'
        m=json.loads(p.read_text(encoding='utf-8'));m['capabilities'].update(allowedSteps=[4],supportsNegativePrompt=False)
        p.write_text(json.dumps(m),encoding='utf-8')
        with self.assertRaises(ValueError):self.submit(steps=5)
        with self.assertRaises(ValueError):self.submit(negativePrompt='bad')
        self.assertEqual(self.factory.state()['jobs'],[])


class SetupExtensionChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='factory-extra-install-')
        self.root=Path(self.temp.name)
        self.hardware={'platform':'Windows','architecture':'AMD64','gpuAvailable':True,
            'gpus':[{'memoryMiB':12288}],'ramGiB':32,'freeDiskBytes':10**12}

    def tearDown(self):self.temp.cleanup()

    def manager(self,models):
        return FactorySetup(self.root,catalog={'version':'0.7.0','models':models},
                            hardware=self.hardware,start_worker=False)

    def test_gated_recipe_accepts_in_app_credential_and_never_serializes_it(self):
        model={'id':'gated','title':'Gated fixture','kind':'image','gated':True,
            'authRequired':True,'minimumVramMiB':1024,'required':[{'path':'engines/gated/w.bin','size':1}],
            'files':[{'path':'engines/gated/w.bin','size':1,'sha256':hashlib.sha256(b'a').hexdigest(),
                'url':'https://huggingface.co/owner/repo/resolve/'+('a'*40)+'/w.bin'}]}
        m=self.manager([model])
        try:
            self.assertTrue(hasattr(m,'configure_credentials'),'in-app credentials must exist')
            token='hf_'+('A'*32)
            result=m.configure_credentials({'provider':'huggingface','token':token})
            self.assertEqual(result,{'configured':True})
            state=m.snapshot()
            self.assertNotIn(token,json.dumps(state))
            self.assertTrue(state['models'][0]['canInstall'])
            job=m.install({'modelId':'gated','acceptLicense':True})
            self.assertNotIn(token,json.dumps(job))
            self.assertNotIn(token,m.state_file.read_text(encoding='utf-8'))
            for path in (self.root/'logs/setup').glob('*'):
                if path.is_file():self.assertNotIn(token.encode(),path.read_bytes())
            self.assertEqual(m.configure_credentials({'provider':'huggingface','token':''}),{'configured':False})
            self.assertFalse(m.snapshot()['models'][0]['canInstall'])
        finally:m.close()

    def test_archive_can_strip_reviewed_source_prefix(self):
        archive=self.root/'source.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('project-fixed/entry.py','print(1)')
        import inspect
        self.assertIn('strip_prefix',inspect.signature(extract_archive).parameters)
        extract_archive(archive,self.root/'provider',strip_prefix='project-fixed/')
        self.assertTrue((self.root/'provider/entry.py').exists())
        self.assertFalse((self.root/'provider/project-fixed').exists())

    def test_gated_small_file_uses_fixed_git_blob_and_local_sha256(self):
        p=self.root/'config.yaml'; data=b'model: fixture\n';p.write_bytes(data)
        pin=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        self.assertTrue('gitBlobSha1' in (ROOT/'scripts/factory_setup.py').read_text(encoding='utf-8'),
                        'git-backed gated config must have a verified content pin')
        self.assertEqual(verify_file(p,{'size':len(data),'gitBlobSha1':pin}),hashlib.sha256(data).hexdigest())
        with self.assertRaises(ValueError):verify_file(p,{'size':len(data),'gitBlobSha1':'0'*40})

    def test_strip_prefix_cannot_hide_archive_traversal(self):
        archive=self.root/'source.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('project-fixed/../outside.txt','bad')
        import inspect
        self.assertIn('strip_prefix',inspect.signature(extract_archive).parameters)
        with self.assertRaises(ValueError):extract_archive(archive,self.root/'provider',strip_prefix='project-fixed/')
        self.assertFalse((self.root/'outside.txt').exists())

    def test_research_only_entry_is_not_installable_despite_profile_name(self):
        m=self.manager([{'id':'unreleased','title':'Not released','kind':'image','deploymentProfile':'x',
                        'releaseStatus':'unreleased','required':[{'path':'engines/x/p.bin','size':1}]}])
        try:self.assertFalse(m.snapshot()['models'][0]['canInstall'])
        finally:m.close()

    def test_resource_advisory_remains_visible(self):
        m=self.manager([{'id':'experiment','title':'Experiment','kind':'image','required':[],
             'resourceProfile':{'tier':'experimental','note':'12GB speed unverified'},
             'capabilities':{'modes':['txt2img']},'validationStage':'not-gpu-tested'}])
        try:
            result=m.snapshot()['models'][0]
            self.assertEqual(result.get('resourceProfile',{}).get('tier'),'experimental')
            self.assertEqual(result.get('capabilities',{}).get('modes'),['txt2img'])
        finally:m.close()

    def test_credentials_are_scoped_to_hf_and_removed_on_cross_host_redirect(self):
        from factory_credentials import CredentialStore,ScopedAuthRedirect
        from urllib.request import Request
        token='hf_'+('B'*32)
        store=CredentialStore(self.root);store.configure({'provider':'huggingface','token':token})
        self.assertEqual(store.headers('https://hf-mirror.com/a'),{})
        self.assertEqual(store.headers('https://huggingface.co.evil.example/a'),{})
        self.assertEqual(store.headers('http://huggingface.co/a'),{})
        request=Request('https://huggingface.co/a',headers=store.headers('https://huggingface.co/a'))
        redirected=ScopedAuthRedirect().redirect_request(request,None,302,'',{},'https://cdn.example/a')
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_new_catalogs_are_discovered_on_empty_install(self):
        (self.root/'config').mkdir()
        (self.root/'config/setup-catalog.json').write_text('{"models":[]}',encoding='utf-8')
        (self.root/'config/python-models.json').write_text('{"models":[]}',encoding='utf-8')
        for name,kind in (('image3d','image'),('video','video')):
            (self.root/f'config/{name}-models.json').write_text(json.dumps({'models':[{'id':name+'-fixture','title':name,'kind':kind,'required':[]}]}),encoding='utf-8')
        m=FactorySetup(self.root,hardware=self.hardware,start_worker=False)
        try:
            self.assertEqual({x['id'] for x in m.snapshot()['models']},{'image3d-fixture'})
            self.assertTrue({'image3d-fixture','video-fixture'}.issubset({x['id'] for x in m.catalog['models']}))
        finally:m.close()

    def test_reserved_ram_does_not_block_a_32gb_machine(self):
        import os
        if os.name!='nt':self.skipTest('Windows hardware API')
        import ctypes
        from unittest.mock import patch
        from factory_setup import detect_hardware
        def memory(pointer):
            pointer._obj.totalPhys=int(31.8*1024**3);pointer._obj.availPhys=4*1024**3;return 1
        def installed(pointer):pointer._obj.value=32*1024**2;return 1
        with patch.object(ctypes.windll.kernel32,'GlobalMemoryStatusEx',side_effect=memory),patch.object(ctypes.windll.kernel32,'GetPhysicallyInstalledSystemMemory',side_effect=installed):
            result=detect_hardware(self.root)
        self.assertEqual(result['ramGiB'],32)
        self.assertEqual(result['usableRamGiB'],31.8)

    def test_compiler_prerequisite_is_checked_before_any_download(self):
        model={'id':'compiler-fixture','title':'Compiler fixture','kind':'utility','required':[{'path':'r.bin','size':1}],
          'files':[{'path':'r.bin','size':1,'sha256':hashlib.sha256(b'a').hexdigest(),'url':'https://example.org/a'}],
          'prerequisites':[{'probe':'msvc','remedy':'Install the C++ compiler first'}]}
        m=self.manager([model])
        try:
            self.assertFalse(m.snapshot()['models'][0]['canInstall'])
            m.hardware_override['buildTools']={'msvc':True}
            self.assertTrue(m.snapshot()['models'][0]['canInstall'])
        finally:m.close()


if __name__=='__main__':unittest.main()
