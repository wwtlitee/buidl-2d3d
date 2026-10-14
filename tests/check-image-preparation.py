"""Run actual native background removal on opaque JPEG. v0.0.1 / Unclecow."""
import json
from pathlib import Path
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from factory_image import prepare_image

folder = ROOT / 'outputs' / '_checks' / 'opaque-jpeg'
folder.mkdir(parents=True, exist_ok=True)
rgba = Image.open(ROOT / 'inputs/kaiju-reference.png').convert('RGBA')
background = Image.new('RGB', rgba.size, (220, 220, 220))
background.paste(rgba, mask=rgba.getchannel('A'))
source = folder / 'input.jpg'
background.save(source, quality=95)
cutout = prepare_image(source, folder)
low, high = cutout.getchannel('A').getextrema()
assert low < 255 and high > 0, (low, high)
assert min(cutout.size) > 0
assert cutout.getchannel('A').getbbox() is not None
result = {'input': str(source), 'size': cutout.size, 'alphaRange': [low, high], 'passed': True}
(folder / 'check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
