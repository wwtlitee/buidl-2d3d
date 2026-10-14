"""MV-Adapter joint-view and fixed-source contracts. v0.7.0 / Unclecow."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),ROOT/'scripts/providers'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class MultiViewTests(unittest.TestCase):
    def setUp(self):
        self.provider=load('multiview-run.py')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        from PIL import Image
        Image.new('RGB',(64,64),'red').save(self.root/'ref.png')

    def test_one_joint_call_generates_six_views_from_one_reference(self):
        request=self.provider.validate_request({'mode':'multiview','prompt':'a cow','imageReferences':[{'path':str(self.root/'ref.png')}]},self.root)
        args=self.provider.pipeline_arguments(request)
        self.assertEqual(args['num_images_per_prompt'],6)
        self.assertEqual(args['width'],512)
        self.assertEqual(args['height'],512)
        self.assertEqual(self.provider.AZIMUTHS,[0,45,90,180,270,315])

    def test_incompatible_input_is_rejected_before_runtime_load(self):
        good={'mode':'multiview','prompt':'cow','imageReferences':[{'path':str(self.root/'ref.png')}]}
        for fields in ({'imageReferences':[]},{'imageReferences':good['imageReferences']*2},{'width':512,'height':256},{'mode':'txt2img'},{'steps':True},{'cfgScale':float('nan')}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):self.provider.validate_request({**good,**fields},self.root)

    def test_offload_keeps_custom_multiview_attention_and_cond_encoder(self):
        calls=[]
        cond=SimpleNamespace(to=lambda **kw:calls.append(('cond',kw)))
        pipe=SimpleNamespace(to=lambda **kw:calls.append(('dtype',kw)),cond_encoder=cond,
            enable_vae_slicing=lambda:calls.append(('slicing',{})),enable_vae_tiling=lambda:calls.append(('tiling',{})),
            enable_model_cpu_offload=lambda:calls.append(('offload',{})))
        self.provider.configure_memory(pipe,'fp16')
        self.assertEqual([x[0] for x in calls],['dtype','cond','slicing','tiling','offload'])
        self.assertEqual(calls[1][1]['device'],'cuda')

    def test_cpu_execution_is_rejected(self):
        fake=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with patch.dict('sys.modules',{'torch':fake}),self.assertRaisesRegex(RuntimeError,'CUDA'):
            self.provider.run(self.root,{},self.root/'output')

    def test_catalog_output_count_and_pins_are_real(self):
        data=json.loads((ROOT/'config/multiview-models.json').read_text(encoding='utf-8'))
        model=next(x for x in data['models'] if x['id']=='mvadapter-i2mv-sdxl')
        self.assertEqual(model['capabilities']['modes'],['multiview'])
        self.assertEqual(model['capabilities']['outputCount'],6)
        self.assertEqual(model['capabilities']['maxReferenceImages'],1)
        self.assertFalse(model['gpuVerified'])
        self.assertFalse(model['resourceProfile']['measured'])
        self.assertEqual(model['runtime']['family'],'mvadapter-i2mv-sdxl')
        for f in model['files']:
            self.assertGreater(f['size'],0)
            self.assertTrue(f.get('sha256') or f.get('gitBlobSha1'))
            self.assertNotIn('/resolve/main/',f['url'])

    def test_published_views_are_complete_and_have_exact_dimensions(self):
        from PIL import Image
        request={'width':512,'height':512}
        images=[Image.new('RGB',(512,512),(index*30,0,0)) for index in range(6)]
        records=self.provider.publish_images(images,request,self.root)
        self.assertEqual([x['azimuthDeg'] for x in records],[0,45,90,180,270,315])
        self.assertEqual(len(list(self.root.glob('view-*.png'))),6)
        with self.assertRaisesRegex(ValueError,'完整'):
            self.provider.publish_images(images[:4],request,self.root)
        images[0]=Image.new('RGB',(256,512))
        with self.assertRaisesRegex(ValueError,'尺寸'):
            self.provider.publish_images(images,request,self.root)

    def test_unknown_source_is_not_silently_rewritten(self):
        helper=load('multiview-setup.py');target=self.root/'source.py';target.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'源码'):
            helper.patch_blob(target,'0'*40,b'new')
        self.assertEqual(target.read_bytes(),b'changed')


if __name__=='__main__':unittest.main()
