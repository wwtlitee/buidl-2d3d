"""CPU-only image preparation checks with a mocked native CLI. v0.1.0 / Unclecow."""
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_image import prepare_image


class ImagePreparationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='factory-image-contract-')
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def source(self, filename, alpha=None):
        path = self.folder / filename
        image = Image.new('RGB', (7, 5), (41, 103, 159))
        if alpha is not None:
            image = image.convert('RGBA')
            image.putalpha(alpha)
        image.save(path)
        return path

    def native_cutout(self, source, output, fallback=False):
        original = source.read_bytes()
        captured = {}

        def fake_run(command, *, check, creationflags):
            self.assertTrue(check)
            self.assertEqual(creationflags, getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertIn('--bg-only', command)
            native_source = Path(command[1])
            self.assertNotEqual(native_source.resolve(), source.resolve())
            self.assertTrue(native_source.is_relative_to(output.resolve()))
            with Image.open(native_source) as normalized:
                self.assertEqual(normalized.format, 'PNG')
                self.assertEqual(normalized.mode, 'RGB')
                self.assertEqual(normalized.convert('RGBA').getchannel('A').getextrema(), (255, 255))
                self.assertEqual(normalized.getpixel((2, 2)), (41, 103, 159))
                captured['input'] = native_source
            cutout = Image.new('RGBA', (7, 5), (41, 103, 159, 0))
            cutout.putpixel((2, 2), (41, 103, 159, 255))
            destination = output / 'prepared_cutout.png' if fallback else Path(command[2])
            cutout.save(destination)
            return subprocess.CompletedProcess(command, 0)

        with patch('factory_image.subprocess.run', side_effect=fake_run) as native:
            result = prepare_image(source, output)
        native.assert_called_once()
        self.assertEqual(result.mode, 'RGBA')
        self.assertEqual(result.getchannel('A').getextrema(), (0, 255))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(sha256(source.read_bytes()).hexdigest(), sha256(original).hexdigest())
        self.assertFalse(captured['input'].exists())
        return result

    def test_fully_transparent_input_is_rejected_without_native(self):
        source = self.source('empty.png', 0)
        original = source.read_bytes()
        with patch('factory_image.subprocess.run') as native:
            with self.assertRaisesRegex(ValueError, 'fully transparent'):
                prepare_image(source, self.folder / 'empty-output')
        native.assert_not_called()
        self.assertEqual(source.read_bytes(), original)

    def test_real_cutout_preserves_alpha_without_native(self):
        for low in (0, 128, 249):
            with self.subTest(low=low):
                source = self.source(f'cutout-{low}.png', low)
                with Image.open(source) as opened:
                    image = opened.copy()
                image.putpixel((2, 2), (41, 103, 159, 255))
                image.save(source)
                original = source.read_bytes()
                with patch('factory_image.subprocess.run') as native:
                    result = prepare_image(source, self.folder / f'cutout-output-{low}')
                native.assert_not_called()
                self.assertEqual(result.getchannel('A').getextrema(), (low, 255))
                self.assertEqual(source.read_bytes(), original)

    def test_nearly_opaque_png_uses_normalized_native_input(self):
        for low in (250, 253, 254):
            with self.subTest(low=low):
                source = self.source(f'near-opaque-{low}.png', low)
                with Image.open(source) as opened:
                    image = opened.copy()
                image.putpixel((2, 2), (41, 103, 159, 255))
                image.save(source)
                self.native_cutout(source, self.folder / f'near-output-{low}')

    def test_fully_opaque_rgba_and_rgb_still_remove_background(self):
        for alpha in (255, None):
            with self.subTest(alpha=alpha):
                source = self.source(f'opaque-{alpha}.png', alpha)
                self.native_cutout(source, self.folder / f'opaque-output-{alpha}')

    def test_native_fallback_cutout_is_supported(self):
        source = self.source('fallback.png', 253)
        self.native_cutout(source, self.folder / 'fallback-output', fallback=True)

    def test_native_failure_preserves_source_and_cleans_normalized_input(self):
        source = self.source('failure.png', 253)
        original = source.read_bytes()
        output = self.folder / 'failure-output'
        captured = {}

        def fail(command, *, check, creationflags):
            self.assertTrue(check)
            self.assertEqual(creationflags, getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            captured['input'] = Path(command[1])
            self.assertTrue(captured['input'].is_file())
            raise subprocess.CalledProcessError(2, command)

        with patch('factory_image.subprocess.run', side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_image(source, output)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(captured['input'].exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
