"""Camera-only MV-Adapter image pipeline package patch. v0.7.0 / Unclecow."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def patch_blob(path,expected,replacement):
    data=path.read_bytes()
    if data==replacement:return
    digest=hashlib.sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()
    if digest!=expected:
        raise ValueError('MV-Adapter 固定相机包源码已改变，拒绝修改未知源')
    backup=path.with_name(path.name+'.factory-original-0.7.0')
    if not backup.exists():backup.write_bytes(data)
    path.write_bytes(replacement)


def patch_camera_only(root):
    lock=json.loads((root/'scripts/providers/multiview-source-lock.json').read_text(encoding='utf-8'))
    base=root/'engines/mvadapter-i2mv-sdxl/src/mvadapter/utils/mesh_utils'
    replacement=b'# Native image-to-multiview camera API only. v0.7.0 / Unclecow.\nfrom .camera import Camera,get_c2w,get_camera,get_orthogonal_camera,get_orthogonal_projection_matrix,get_projection_matrix\n'
    patch_blob(base/'__init__.py',lock['meshInitGitBlobSha1'],replacement)
    camera=base/'camera.py';original=camera.with_name(camera.name+'.factory-original-0.7.0')
    text=(original if original.is_file() else camera).read_text(encoding='utf-8')
    # LIST_TYPE is an annotation alias; importing it from mesh utils would pull
    # in the unrelated NVDiffrast renderer even for plain i2mv camera controls.
    text=text.replace('from .utils import LIST_TYPE','from typing import Any as LIST_TYPE')
    pin=next(f['gitBlobSha1'] for f in lock['files'] if f['path'].endswith('mesh_utils/camera.py'))
    patch_blob(camera,pin,text.encode('utf-8'))
    utils_init=base.parent/'__init__.py'
    pin=next(f['gitBlobSha1'] for f in lock['files'] if f['path'].endswith('mvadapter/utils/__init__.py'))
    replacement=b'# Native i2mv controls; plotting/export helpers are unused. v0.7.0 / Unclecow.\nfrom .mesh_utils.camera import get_camera,get_orthogonal_camera\nfrom .geometry import get_plucker_embeds_from_cameras_ortho\n'
    patch_blob(utils_init,pin,replacement)
    print('Fixed MV-Adapter camera-only package prepared; no weights loaded.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT)
    patch_camera_only(parser.parse_args().root.resolve())
