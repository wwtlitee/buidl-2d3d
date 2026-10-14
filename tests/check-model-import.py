"""User GLB import & install/generation separation. v0.5.0 / Unclecow."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import struct
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('import_fixture',ROOT/'tests/check-animation-pipeline.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


class ModelImportTests(fixture.AnimationPipelineTests):
    def test_local_glb_appears_in_history_without_generation(self):
        self.assertTrue(hasattr(self.factory,'upload_model'),'Local GLB upload is required')
        data=self.path.read_bytes()
        result=self.factory.upload_model(data,'我的模型.glb')
        self.assertEqual(result['sha256'],hashlib.sha256(data).hexdigest())
        self.assertEqual(self.factory.state()['jobs'],[])
        task=next(t for t in self.factory.state()['resultTasks'] if t['name']=='我的模型')
        self.assertEqual(task['jobs'][0]['engine'],'imported-model')
        self.assertEqual(task['jobs'][0]['assets'][0]['modelInfo']['skins'],1)

    def test_invalid_model_creates_no_success_receipt(self):
        self.assertTrue(hasattr(self.factory,'upload_model'),'Local GLB upload is required')
        with self.assertRaises(ValueError):self.factory.upload_model(b'not-a-model','broken.glb')
        self.assertEqual(list((self.root/'outputs/imported-model').glob('*/receipt.json')),[])

    def test_invalid_geometry_accessor_is_an_actionable_input_error(self):
        document,binary=fixture.read_glb(self.path)
        document['meshes'][0]['primitives'][0]['attributes']['POSITION']=999
        encoded=json.dumps(document).encode();encoded+=b' '*(-len(encoded)%4)
        data=(struct.pack('<4sII',b'glTF',2,28+len(encoded)+len(binary))+
              struct.pack('<II',len(encoded),0x4E4F534A)+encoded+
              struct.pack('<II',len(binary),0x004E4942)+binary)
        try:self.factory.upload_model(data,'bad.glb')
        except ValueError:return
        except Exception as error:self.fail('Invalid GLB must return ValueError, received '+type(error).__name__)
        self.fail('Invalid geometry was accepted')

    def test_unskinned_position_buffer_must_cover_declared_vertices(self):
        fixture.fixture_glb(self.path,skinned=False)
        document,binary=fixture.read_glb(self.path)
        document['buffers'][0]['byteLength']=12
        document['bufferViews'][0]['byteLength']=12
        binary=binary[:12]
        encoded=json.dumps(document).encode();encoded+=b' '*(-len(encoded)%4)
        data=(struct.pack('<4sII',b'glTF',2,28+len(encoded)+len(binary))+
              struct.pack('<II',len(encoded),0x4E4F534A)+encoded+
              struct.pack('<II',len(binary),0x004E4942)+binary)
        with self.assertRaisesRegex(ValueError,'网格|数据|位置'):
            self.factory.upload_model(data,'truncated.glb')
        self.assertEqual(list((self.root/'outputs/imported-model').glob('*/receipt.json')),[])

    def test_generation_is_blocked_while_installation_active(self):
        self.factory.setup.jobs.append({'id':'fixture','modelId':'basic-motion','status':'running'})
        with self.assertRaisesRegex(ValueError,'安装'):
            self.factory.create_model_job({'engineId':'basic-motion','modelUrl':self.url,'motion':'joint-test'})

    def test_install_is_blocked_while_production_active(self):
        self.assertTrue(hasattr(self.factory,'install_model'),'Install guard must be wired to factory')
        self.factory.data['batches'].append({'jobs':[{'status':'running'}]})
        with self.assertRaisesRegex(ValueError,'生成任务'):
            self.factory.install_model({'modelId':'anything','acceptLicense':True})


if __name__=='__main__':
    names=[n for n in ModelImportTests.__dict__ if n.startswith('test_')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ModelImportTests(n) for n in names))
    sys.exit(not result.wasSuccessful())
