"""Actual CPU image output acceptance and fail-closed receipts. Unclecow."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

class MediaRunnerChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='media-output-')
        self.root=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def module(self):
        self.assertTrue((ROOT/'scripts/factory_media_job.py').exists(),'media generation must verify outputs before success')
        import factory_media_job
        return factory_media_job

    def test_image_bytes_must_decode(self):
        p=self.root/'generated.png';p.write_bytes(b'bad')
        with self.assertRaises((ValueError,OSError)):self.module().inspect_outputs(self.root,{'kind':'image'},{'width':256,'height':256})

    def test_image_dimensions_and_hash_are_retained(self):
        Image.new('RGBA',(256,256),(10,20,30,255)).save(self.root/'generated.png')
        values=self.module().inspect_outputs(self.root,{'kind':'image'},{'width':256,'height':256})
        self.assertEqual(values[0]['width'],256)
        self.assertEqual(len(values[0]['sha256']),64)

    def test_wrong_size_output_does_not_silently_succeed(self):
        Image.new('RGB',(32,32)).save(self.root/'generated.png')
        with self.assertRaises(ValueError):self.module().inspect_outputs(self.root,{'kind':'image'},{'width':256,'height':256})

    def test_no_outputs_is_failure(self):
        with self.assertRaises(ValueError):self.module().inspect_outputs(self.root,{'kind':'image'},{'width':256,'height':256})

    def test_video_header_alone_is_not_success(self):
        (self.root/'generated.mp4').write_bytes(b'\x00\x00\x00\x20ftypisom'+b'\0'*100)
        with self.assertRaises((ValueError,RuntimeError)):self.module().inspect_outputs(self.root,{'kind':'video'},{'width':320,'height':256,'frames':17,'fps':16})

if __name__=='__main__':unittest.main()
