"""Real owned CPU media subprocesses; no models/GPU/main browser. Unclecow."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from factory_queue import Factory
from legacy_queue_fixture import accept_legacy_job

class MediaQueueChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=ROOT/'cache/qa-media-0.7'
        cls.python=cls.root/'runtime/qa/.venv/Scripts/python.exe'
        if not cls.python.is_file():raise RuntimeError('Prepare the isolated PyAV/Pillow QA environment first')
        (cls.root/'scripts').mkdir(parents=True,exist_ok=True)
        (cls.root/'config').mkdir(exist_ok=True)
        (cls.root/'VERSION.json').write_text('{"version":"0.7.0"}',encoding='utf-8')
        for name in ('setup-catalog','python-models','image3d-models','video-models'):
            (cls.root/f'config/{name}.json').write_text('{"models":[]}',encoding='utf-8')
        for path in (ROOT/'scripts').glob('*.py'):shutil.copy2(path,cls.root/'scripts'/path.name)
        text=(ROOT/'scripts/media-job.ps1').read_text(encoding='utf-8').replace(r'Local\Image2D3DFactoryGPU',r'Local\FactoryMediaQA07')
        (cls.root/'scripts/media-job.ps1').write_text(text,encoding='utf-8')
        code="""import av,sys
c=av.open(sys.argv[1],'w');s=c.add_stream('libx264',rate=16);s.width=320;s.height=256;s.pix_fmt='yuv420p'
for i in range(17):
 f=av.VideoFrame(320,256,'rgb24');f.planes[0].update(bytes([i*10,40,80])*(320*256))
 for p in s.encode(f):c.mux(p)
for p in s.encode():c.mux(p)
c.close()
"""
        subprocess.run([str(cls.python),'-c',code,str(cls.root/'fixture.mp4')],check=True)

    def setUp(self):
        state=self.root/'logs/visual-factory/state.json'
        state.unlink(missing_ok=True)
        folder=self.root/'engines/cpu-video';folder.mkdir(parents=True,exist_ok=True)
        manifest={'id':'cpu-video','title':'CPU fixture video','kind':'video','status':'installed','mediaAdapter':True,
            'entrypoint':'run.ps1','runtime':{'python':'runtime/qa/.venv/Scripts/python.exe'},
            'capabilities':{'modes':['txt2video'],'sizeMultiple':32,'frameRule':{'multiple':8,'offset':1,'min':9,'max':129}},
            'defaults':{'width':320,'height':256,'steps':4,'frames':17,'fps':16}}
        (folder/'engine.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.entry=folder/'run.ps1';self.entry.write_text("param($RequestJson,$OutputDirectory)\nCopy-Item -LiteralPath (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'fixture.mp4') -Destination (Join-Path $OutputDirectory 'generated.mp4')\n",encoding='utf-8')
        self.factory=Factory(self.root,start_worker=True)

    def tearDown(self):self.factory.close()

    def job(self):
        # The release no longer creates video jobs. Seed a v0.8 accepted queue item
        # to keep actual worker, cancellation and output-validation coverage.
        request={'engineId':'cpu-video','mode':'txt2video','prompt':'CPU fixture',
                 'width':320,'height':256,'steps':4,'frames':17,'fps':16,'seed':7,'imageReferences':[]}
        with self.factory.lock:
            job, _ = accept_legacy_job(self.factory,'cpu-video',request,media=True)
        self.factory.wake.set()
        return job

    def wait(self,job,terminal=True):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            value=self.factory._find_job(job['id'])
            if (value['status'] in {'generated-unreviewed','failed','cancelled','interrupted'}) if terminal else self.factory.process is not None:
                return value
            time.sleep(.1)
        self.fail('Owned media invocation did not finish')

    def test_complete_cpu_mp4_is_decoded_and_retained_as_video(self):
        job=self.job();done=self.wait(job)
        self.assertEqual(done['status'],'generated-unreviewed',done.get('error'))
        receipt=self.factory._receipt(self.factory.run_root(job))
        self.assertEqual(receipt['outputs'][0]['frames'],17)
        self.assertFalse(receipt['gpuInferenceExecuted'])
        state=self.factory.state();public=next(j for j in state['mediaJobs'] if j['id']==job['id'])
        self.assertTrue(any(a['kind']=='video' and a['previewUrl'] for a in public['assets']))
        history=next(x for x in state['history'] if x['runId']==job['runId'])
        self.assertEqual(history['kind'],'video')

    def test_exit_zero_with_corrupt_mp4_is_failed(self):
        self.entry.write_text("param($RequestJson,$OutputDirectory)\n'bad video' | Set-Content -LiteralPath (Join-Path $OutputDirectory 'generated.mp4')\n",encoding='utf-8')
        job=self.job();done=self.wait(job)
        self.assertEqual(done['status'],'failed')
        self.assertEqual(self.factory._receipt(self.factory.run_root(job))['status'],'failed')

    def test_cancel_does_not_leave_an_owned_media_process(self):
        self.entry.write_text("param($RequestJson,$OutputDirectory)\nStart-Sleep -Seconds 60\n",encoding='utf-8')
        job=self.job();self.wait(job,False)
        process=self.factory.process
        self.factory.cancel_job(job['id'])
        done=self.wait(job)
        self.assertEqual(done['status'],'cancelled')
        self.assertIsNotNone(process.poll())

if __name__=='__main__':unittest.main()
