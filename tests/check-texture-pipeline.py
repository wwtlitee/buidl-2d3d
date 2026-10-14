"""Isolated multi-view input, embedded texture and queue tests. v0.4.0 / Unclecow."""
from __future__ import annotations
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_glb import read_glb, inspect_texture_glb
from factory_postprocess import checked_reference_image, checked_reference_set, file_sha256
from factory_model_job import run_job
from factory_queue import Factory

spec = importlib.util.spec_from_file_location('animation_fixture', ROOT / 'tests/check-animation-pipeline.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

def write_glb(path, doc, binary):
    binary += b'\0' * (-len(binary) % 4)
    doc['buffers'][0]['byteLength'] = len(binary)
    raw = json.dumps(doc).encode(); raw += b' ' * (-len(raw) % 4)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28 + len(raw) + len(binary)) + struct.pack('<II', len(raw), 0x4e4f534a) + raw + struct.pack('<II', len(binary), 0x004e4942) + binary)

def textured_fixture(path):
    fixture.fixture_glb(path, skinned=False)
    doc, binary = read_glb(path)
    uv = struct.pack('<6f', 0,0,1,0,0,1)
    doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(uv)})
    doc['accessors'].append({'bufferView':len(doc['bufferViews'])-1,'componentType':5126,'type':'VEC2','count':3})
    primitive = doc['meshes'][0]['primitives'][0]
    primitive['attributes']['TEXCOORD_0'] = len(doc['accessors'])-1
    binary += uv
    png = io.BytesIO(); Image.new('RGB',(2,2),'orange').save(png,format='PNG')
    doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(png.getvalue())})
    binary += png.getvalue()
    doc['images']=[{'bufferView':len(doc['bufferViews'])-1,'mimeType':'image/png'}]
    doc['textures']=[{'source':0}]
    doc['materials']=[{'pbrMetallicRoughness':{'baseColorTexture':{'index':0}}}]
    primitive['material']=0
    write_glb(path,doc,binary)

class TexturePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='factory-texture-')
        self.root = Path(self.temp.name)
        (self.root/'VERSION.json').write_text('{"version":"0.4.0"}',encoding='utf-8')
        (self.root/'config').mkdir()
        # This fake painting adapter is independent of the shipped real-weight recipes.
        for catalog in ('setup-catalog.json','python-models.json'):
            (self.root/'config'/catalog).write_text('{"models":[]}',encoding='utf-8')
        for name,kind in [('hunyuan-paint','texture'),('source','model')]:
            folder=self.root/'engines'/name;folder.mkdir(parents=True)
            (folder/'run.ps1').write_text("throw 'Fixture must never run GPU'",encoding='utf-8')
            (folder/'engine.json').write_text(json.dumps({'id':name,'kind':kind,'title':name,'version':'0.4.0','status':'installed','capabilities':{'multiView':True,'views':['left','back','right']}}),encoding='utf-8')
        self.model=self.root/'outputs/source/run/model.glb'
        fixture.fixture_glb(self.model,skinned=False)
        self.url='/files/outputs/source/run/model.glb'
        self.factory=Factory(self.root,start_worker=False)
        self.images=[]
        for view,color in [('front','red'),('left','blue'),('back','green')]:
            png=io.BytesIO();Image.new('RGB',(3,3),color).save(png,format='PNG')
            image=self.factory.upload_image(png.getvalue(),view+'.png')
            self.images.append({'view':view,'imageId':image['id'],'url':image['url']})
        self.payload={'stage':'texture','engineId':'hunyuan-paint','modelUrl':self.url,'images':self.images}
    def tearDown(self):
        self.factory.close();self.temp.cleanup()
    def test_valid_embedded_texture_is_used_on_visible_mesh(self):
        textured_fixture(self.model)
        summary=inspect_texture_glb(self.model)
        self.assertEqual(summary['embeddedTextureImages'],1)
        self.assertEqual(summary['texturedPrimitives'],1)
        self.assertEqual(summary['textureImages'][0]['width'],2)
    def test_white_mesh_and_unused_material_do_not_count(self):
        with self.assertRaisesRegex(ValueError,'贴图'): inspect_texture_glb(self.model)
        textured_fixture(self.model);doc,binary=read_glb(self.model)
        del doc['meshes'][0]['primitives'][0]['material'];write_glb(self.model,doc,binary)
        with self.assertRaisesRegex(ValueError,'贴图'): inspect_texture_glb(self.model)
    def test_unused_textured_mesh_does_not_count(self):
        textured_fixture(self.model);doc,binary=read_glb(self.model)
        doc['scenes'][0]['nodes']=[0];write_glb(self.model,doc,binary)
        with self.assertRaisesRegex(ValueError,'贴图'): inspect_texture_glb(self.model)
    def test_missing_invalid_and_out_of_bounds_uv_rejected(self):
        for failure in ['missing','nan','count','overrun']:
            with self.subTest(failure=failure):
                textured_fixture(self.model);doc,binary=read_glb(self.model)
                primitive=doc['meshes'][0]['primitives'][0]; uv_index=primitive['attributes']['TEXCOORD_0']
                if failure=='missing': del primitive['attributes']['TEXCOORD_0']
                elif failure=='count': doc['accessors'][uv_index]['count']=4
                elif failure=='overrun': doc['accessors'][uv_index]['byteOffset']=99999
                else:
                    offset=doc['bufferViews'][doc['accessors'][uv_index]['bufferView']]['byteOffset']
                    edited=bytearray(binary);struct.pack_into('<f',edited,offset,float('nan'));binary=bytes(edited)
                write_glb(self.model,doc,binary)
                with self.assertRaisesRegex(ValueError,'UV'): inspect_texture_glb(self.model)
    def test_external_and_corrupt_embedded_images_rejected(self):
        for invalid in ['external','corrupt']:
            textured_fixture(self.model);doc,binary=read_glb(self.model)
            if invalid=='external': doc['images'][0]={'uri':'https://example.com/texture.png'}
            else:
                offset=doc['bufferViews'][doc['images'][0]['bufferView']]['byteOffset'];binary=binary[:offset]+b'x'*(len(binary)-offset)
            write_glb(self.model,doc,binary)
            with self.assertRaises(ValueError): inspect_texture_glb(self.model)
    def test_manual_views_are_frozen_and_preserved_in_shared_queue(self):
        job=self.factory.create_model_job(self.payload)
        self.assertEqual(job['kind'],'texture')
        self.assertEqual([r['view'] for r in job['request']['referenceImages']],['front','left','back'])
        self.assertTrue(all(len(r['sha256'])==64 for r in job['request']['referenceImages']))
        self.assertEqual(self.factory.state()['textureEngines'][0]['id'],'hunyuan-paint')
    def test_too_few_missing_front_and_duplicate_views_rejected(self):
        for images in [self.images[:1],self.images[1:],self.images+[self.images[0]], [self.images[0],{**self.images[1],'view':'front'}]]:
            with self.assertRaises(ValueError): self.factory.create_model_job({**self.payload,'images':images})
        self.assertEqual(self.factory.data['batches'],[])
    def test_same_image_content_cannot_be_two_viewpoints(self):
        duplicate={**self.images[0],'view':'back'}
        with self.assertRaisesRegex(ValueError,'重复'): self.factory.create_model_job({**self.payload,'images':[self.images[0],duplicate]})
    def test_reference_url_cannot_escape_or_fetch_remote(self):
        for url in ['https://example.com/a.png','/files/inputs/../../secret.png',self.images[0]['url']+'?x=1']:
            with self.assertRaises(ValueError): checked_reference_image(self.root,url)
    def test_already_rigged_mesh_is_not_stripped_for_painting(self):
        fixture.fixture_glb(self.model,skinned=True)
        with self.assertRaisesRegex(ValueError,'骨架'): self.factory.create_model_job(self.payload)
    def test_original_receipt_views_are_inherited_without_upload(self):
        records=[{'view':i['view'],'path':str(self.root/self.factory._image(i['imageId'])['path']),'sha256':file_sha256(self.root/self.factory._image(i['imageId'])['path'])} for i in self.images]
        (self.model.parent/'receipt.json').write_text(json.dumps({'inputs':records}),encoding='utf-8')
        payload={k:v for k,v in self.payload.items() if k!='images'}
        job=self.factory.create_model_job(payload)
        self.assertEqual(len(job['request']['referenceImages']),3)
    def test_retry_refuses_changed_reference_sha(self):
        job=self.factory.create_model_job(self.payload)
        self.factory._find_job(job['id'])['status']='failed'
        selected=self.root/job['request']['referenceImages'][0]['path']
        Image.new('RGB',(3,3),'yellow').save(selected)
        with self.assertRaisesRegex(ValueError,'改变'): self.factory.retry_job(job['id'])
        self.assertEqual(len(self.factory._jobs()),1)
    def test_execution_requires_frozen_reference_hashes(self):
        with self.assertRaisesRegex(ValueError,'固定参考图哈希'):
            checked_reference_set(self.root,[{'view':i['view'],'url':i['url']} for i in self.images],require_sha=True)
    def test_runner_passes_all_copied_views_and_promotes_only_valid_texture(self):
        job=self.factory.create_model_job(self.payload); request=self.root/'request.json'
        request.write_text(json.dumps(job['request']),encoding='utf-8')
        captured=[]
        def provider(command,**_kwargs):
            captured.append(command)
            output=Path(command[command.index('-OutputDirectory')+1]);self.assertFalse(output.exists());output.mkdir()
            refs=json.loads(Path(command[command.index('-ViewsJson')+1]).read_text(encoding='utf-8'))
            self.assertEqual(set(refs),{'front','left','back'})
            self.assertTrue(all(Path(p).is_file() for p in refs.values()))
            textured_fixture(output/'colored.glb')
            (output/'result.json').write_text('{"primaryOutput":"colored.glb"}',encoding='utf-8')
            return SimpleNamespace(returncode=0)
        with patch('factory_model_job.subprocess.run',provider): run_job(self.root,'hunyuan-paint',request,job['runId'])
        output=self.root/'outputs/hunyuan-paint'/job['runId']
        receipt=json.loads((output/'receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['status'],'generated-unreviewed')
        self.assertEqual(receipt['viewCount'],3)
        self.assertEqual(receipt['modelChecks']['colored.glb']['embeddedTextureImages'],1)
        assets=self.factory.assets(output)
        self.assertEqual([a['name'] for a in assets if a['kind']=='model'],['colored.glb'])
        self.assertFalse(any(a['kind']=='texture' for a in assets))
        self.assertEqual(len(self.factory._receipt_inputs(output,receipt)),3)

if __name__=='__main__': unittest.main()
