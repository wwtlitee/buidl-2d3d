"""Local image preparation for installed 3D engines. v0.1.0 / Unclecow."""
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageOps

CUTOUT_ALPHA_THRESHOLD = 250


def prepare_image(source, output_dir):
    source, output_dir = Path(source).resolve(), Path(output_dir).resolve()
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert('RGBA')
    low, high = image.getchannel('A').getextrema()
    if high == 0:
        raise ValueError('Input is fully transparent')
    if low < CUTOUT_ALPHA_THRESHOLD:
        return image
    root = Path(__file__).resolve().parents[1]
    native = root / 'engines/trellis'
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / 'prepared.png'
    # Tiny alpha deviations from image decoders are not a cutout. Pass a fresh
    # RGB PNG so the native tool cannot skip removal based on the original alpha.
    with tempfile.TemporaryDirectory(prefix='prepared-input-', dir=output_dir) as staging:
        normalized = Path(staging) / 'input.png'
        image.convert('RGB').save(normalized, format='PNG')
        command = [str(native / 'runtime/trellis-cli.exe'), str(normalized), str(destination),
                   '--models', str(native / 'models-q8'), '--bg-only', '--require-gpu', '--threads', '8']
        subprocess.run(command, check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    candidates = [destination, output_dir / 'prepared_cutout.png']
    for candidate in candidates:
        if candidate.is_file():
            with Image.open(candidate) as prepared:
                return prepared.convert('RGBA')
    raise RuntimeError('Background removal returned without a cutout')
