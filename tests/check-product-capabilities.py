"""v0.9 scope boundary: isolated files and HTTP fixtures, no providers or downloads."""
import copy
from io import BytesIO
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_capabilities import creation_allowed, require_accepted_execution, SCOPE_MESSAGE
from factory_deploy import deploy_profile
from factory_media_job import run as run_media
from factory_model_job import run_job
from factory_queue import Factory
from factory_setup import FactorySetup
from factory_web import FactoryServer
from legacy_queue_fixture import accept_legacy_job


class ProductCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='factory-scope-')
        self.root = Path(self.temp.name)
        (self.root / 'VERSION.json').write_text('{"version":"0.9.0"}', encoding='utf-8')
        (self.root / 'config').mkdir()
        (self.root / 'config/setup-catalog.json').write_text('{"models":[]}', encoding='utf-8')
        for engine, kind, modes in [('image-fixture','image',['txt2img','edit','multiview']), ('model-fixture','model',[]),
                                    ('texture-fixture','texture',[]), ('video-fixture','video',['txt2video']),
                                    ('rig-fixture','rig',[]), ('animation-fixture','animation',[])]:
            folder = self.root / 'engines' / engine
            folder.mkdir(parents=True)
            (folder / 'run.ps1').write_text("throw 'never execute'", encoding='utf-8')
            (folder / 'engine.json').write_text(json.dumps({'id':engine, 'title':engine, 'version':'0.8.0a', 'kind':kind,
                'status':'installed','entrypoint':'run.ps1', 'mediaAdapter':kind in {'image','video'},
                'capabilities':{'modes':modes, 'maxInputImages':1}, 'defaults':{'width':256,'height':256,'steps':2}}), encoding='utf-8')
        self.factory = Factory(self.root, start_worker=False)
        self.hardware = {'gpuAvailable':False,'gpus':[],'ramGiB':32,'freeDiskBytes':10**12,'platform':'Windows','architecture':'AMD64'}
        self.factory.setup.hardware_override = self.hardware

    def tearDown(self):
        self.factory.close()
        self.temp.cleanup()

    def test_new_retired_jobs_and_image_pipeline_injections_never_enqueue(self):
        calls = [lambda:self.factory.create_media_job({'engineId':'video-fixture','prompt':'x'}),
                 lambda:self.factory.create_image_job({'engineId':'video-fixture','prompt':'x'}),
                 lambda:self.factory.create_model_job({'stage':'rig','engineId':'rig-fixture'}),
                 lambda:self.factory.create_model_job({'stage':'animation','engineId':'animation-fixture'}),
                 lambda:self.factory.create_pipeline({'imageEngineId':'video-fixture','modelEngineIds':['model-fixture'],'prompt':'x'}),
                 lambda:self.factory.create_pipeline({'imageEngineId':'image-fixture','modelEngineIds':['model-fixture','rig-fixture'],'prompt':'x'})]
        before = copy.deepcopy(self.factory.data)
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(ValueError,'首版'):
                call()
            self.assertEqual(self.factory.data, before)

    def test_image_adapter_edit_multiview_and_pipeline_are_still_available(self):
        image = BytesIO(); Image.new('RGB',(32,32),'orange').save(image,format='PNG')
        record = self.factory.upload_image(image.getvalue(),'source.png')
        for mode in ('txt2img','edit','multiview'):
            payload = {'engineId':'image-fixture','mode':mode,'prompt':'x'}
            if mode != 'txt2img': payload['inputImageIds']=[record['id']]
            self.assertEqual(self.factory.create_media_job(payload)['kind'],'image')
        pipeline = self.factory.create_pipeline({'imageEngineId':'image-fixture','modelEngineIds':['model-fixture'],'prompt':'x'})
        self.assertEqual(pipeline['imageEngineId'],'image-fixture')
        self.assertTrue(next(e for e in self.factory.creation_engines() if e['id']=='texture-fixture'))

    def test_default_and_explicit_video_mode_on_image_engine_are_rejected(self):
        path = self.root / 'engines/image-fixture/engine.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['capabilities']['modes'] = ['txt2video','txt2img']
        path.write_text(json.dumps(manifest),encoding='utf-8')
        for extra in ({}, {'mode':'txt2video'}, {'mode':'img2video'}):
            with self.assertRaisesRegex(ValueError,'首版'):
                self.factory.create_media_job({'engineId':'image-fixture','prompt':'x',**extra})
        engine = next(e for e in self.factory.state()['mediaEngines'] if e['id']=='image-fixture')
        self.assertEqual(engine['capabilities']['modes'],['txt2img'])
        for field in ('kind','stage','mode','id','engineId'):
            self.assertTrue(creation_allowed({field:[]}))
        for mode in ([],{}):
            with self.assertRaises(ValueError):self.factory.create_media_job({'engineId':'image-fixture','mode':mode,'prompt':'x'})

    def test_retired_retry_preserves_original_record_and_output(self):
        for kind in ('video','rig','animation'):
            job, _ = accept_legacy_job(self.factory, kind+'-fixture', {'prompt':'old'},media=kind=='video')
            self.factory._find_job(job['id'])['status']='failed'
            before = copy.deepcopy(self.factory.data)
            with self.assertRaisesRegex(ValueError,'首版'):self.factory.retry_job(job['id'])
            self.assertEqual(self.factory.data,before)

    def test_catalog_and_new_installs_exclude_retired_dependencies_but_keep_jobs(self):
        models=[{'id':kind+'-install','title':kind,'kind':kind} for kind in ('image','model','texture','video','rig','animation')]
        models += [{'id':'wangp-video-runtime','title':'runtime','kind':'utility'},
                   {'id':'dependent-image','title':'blocked dependency','kind':'image','dependencies':['video-install']}]
        manager=FactorySetup(self.root,catalog={'models':models},hardware=self.hardware,start_worker=False)
        try:
            manager.jobs=[{'id':'old','modelId':'video-install','status':'failed','licenseAccepted':True}]
            state=manager.snapshot()
            self.assertEqual({m['id'] for m in state['models']},{'image-install','model-install','texture-install'})
            self.assertEqual(state['jobs'],manager.jobs)
            for identifier in ('video-install','rig-install','animation-install','wangp-video-runtime','dependent-image'):
                with self.assertRaisesRegex(ValueError,'首版'):manager.install({'modelId':identifier})
            with self.assertRaisesRegex(ValueError,'首版'):manager.retry('old')
        finally:manager.close()

    def test_direct_retired_deployment_is_rejected_before_bundle_or_process(self):
        for identifier in ('wangp-video','mia-rig','mia-motion','unirig','riganything','basic-motion'):
            with patch('factory_deploy.deploy_bundle') as bundle, patch('factory_deploy.run_command') as command:
                with self.assertRaisesRegex(ValueError,'首版'):
                    deploy_profile(self.root,identifier,profiles={'profiles':[{'id':identifier}]})
                bundle.assert_not_called();command.assert_not_called()

    def test_old_queue_survives_reload_and_runner_accepts_only_exact_request(self):
        for kind in ('video','rig','animation'):
            engine=kind+'-fixture'; request={'engineId':engine,'mode':'txt2video' if kind=='video' else 'model','prompt':'old'}
            job, request_path = accept_legacy_job(self.factory,engine,request,media=kind=='video')
            manifest=json.loads((self.root/'engines'/engine/'engine.json').read_text(encoding='utf-8'))
            require_accepted_execution(self.root,manifest,job['runId'],request_path,request)
            for run_id, file, value in [(job['runId']+'1',request_path,request),(job['runId'],self.root/'new.json',request),
                                        (job['runId'],request_path,{**request,'prompt':'changed'})]:
                with self.assertRaisesRegex(ValueError,'首版'):require_accepted_execution(self.root,manifest,run_id,file,value)
        before=copy.deepcopy(self.factory.data['batches'])
        restored=Factory(self.root,start_worker=False)
        try:self.assertEqual(restored.data['batches'],before)
        finally:restored.close()

    def test_direct_runners_refuse_new_work_without_creating_output(self):
        request_path=self.factory.storage/'unaccepted.json';request_path.write_text('{}',encoding='utf-8')
        for runner,engine in [(run_media,'video-fixture'),(run_job,'rig-fixture'),(run_job,'animation-fixture')]:
            with self.assertRaisesRegex(ValueError,'首版'):runner(self.root,engine,request_path,'20261013-120000-abcdef')
        self.assertFalse((self.root/'outputs').exists())

    def test_history_previews_downloads_and_retired_labels_remain(self):
        for kind in ('video','rig','animation'):
            folder=self.root/'outputs'/(kind+'-fixture')/'historic'
            folder.mkdir(parents=True)
            (folder/'receipt.json').write_text(json.dumps({'status':'generated-unreviewed','kind':kind,'pipelineStage':kind,
                'inputMode':'txt2video' if kind=='video' else 'model','startedAt':'2026-10-01T00:00:00Z'}),encoding='utf-8')
            (folder/('output.mp4' if kind=='video' else 'output.glb')).write_bytes(b'fixture classification')
        state=self.factory.state()
        self.assertEqual(len(state['history']),3);self.assertEqual(len(state['resultTasks']),3)
        self.assertEqual(state['rigEngines'],[]);self.assertEqual(state['animationEngines'],[])
        self.assertTrue(all(e['kind']=='image' for e in state['mediaEngines']))
        self.assertEqual(len(state['historicalEngines']),6)
        for task in state['resultTasks']:
            self.assertFalse(task['creationEnabled']);self.assertTrue(task['downloadUrl'])
            self.assertTrue(any(asset['previewUrl'] for asset in task['jobs'][0]['assets']))
            archive = self.factory.archive(task_id=task['id'])
            self.assertGreater(archive.stat().st_size,0)
            with zipfile.ZipFile(archive) as download:
                suffix = 'mp4' if task['kind']=='video' else 'glb'
                self.assertIn(f"{task['jobs'][0]['engine']}/historic/output.{suffix}", download.namelist())

    def test_http_retired_creation_returns_400_and_retains_state(self):
        server=FactoryServer(('127.0.0.1',0),self.factory)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for endpoint,payload in [('/api/media-jobs',{'engineId':'video-fixture','prompt':'x'}),
                                    ('/api/model-jobs',{'stage':'rig','engineId':'rig-fixture'}),
                                    ('/api/image-jobs',{'engineId':'video-fixture','prompt':'x'})]:
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                connection.request('POST',endpoint,json.dumps(payload),{'Content-Type':'application/json','X-Factory-Request':'1'})
                response=connection.getresponse();body=json.loads(response.read());connection.close()
                self.assertEqual(response.status,400);self.assertIn('首版',body['error'])
            self.assertEqual(self.factory._jobs(),[])
        finally:server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
