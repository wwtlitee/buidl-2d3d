"""Local SPAR3D single image to textured UV GLB. v0.7.0 / Unclecow."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]


def inside(root, value):
    path = Path(value)
    path = (root/path if not path.is_absolute() else path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('SPAR 输入或组件超出工厂目录')
    return path


def validate_input(root, image, input_set):
    image = inside(root, image)
    if not image.is_file():
        raise ValueError('SPAR 主图片不存在')
    if input_set:
        views = json.loads(inside(root, input_set).read_text(encoding='utf-8-sig'))
        if not isinstance(views, dict) or set(views) != {'front'} or inside(root, views['front']) != image:
            raise ValueError('SPAR 是单图建模，不能提交多图联合视角')
    from PIL import Image
    with Image.open(image) as opened:
        if opened.format not in {'PNG','JPEG','WEBP'} or max(opened.size)>8192:
            raise ValueError('SPAR 图片格式或尺寸不支持')
        opened.verify()
    return image


def offline():
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      SPAR3D_LOW_VRAM='1', SPAR3D_USE_CPU='0')
    def blocked(*_args, **_kwargs):
        raise RuntimeError('SPAR 推理期间禁止下载，请在模型与环境完成固定组件安装')
    socket.create_connection = blocked
    socket.socket.connect = blocked


def paths(root):
    os.environ['PATH']=str(root/'runtime/cuda12')+os.pathsep+os.environ.get('PATH','')
    sys.path[:0] = [str(root/'engines/spar3d/native'), str(root/'engines/spar3d/vendor'),
                   str(root/'engines/spar3d/src'), str(root/'scripts')]
    os.environ['SPAR3D_DINOV2_PATH'] = str(root/'engines/spar3d/models/dinov2-large')
    os.environ['ALPHA_CLIP_PATH'] = str(root/'engines/spar3d/models/clip')
    selection = json.loads((root/'engines/spar3d/models/factory-config.json').read_text(encoding='utf-8'))
    os.environ['SPAR3D_CLIP_FILE'] = selection['clipFile']


def run(root, image, output, texture_resolution=1024):
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('SPAR3D 需要 CUDA，不会自动切换为 CPU 生成')
    if texture_resolution not in {512,1024}:
        raise ValueError('本地 SPAR 纹理尺寸仅开放 512 或 1024')
    offline(); paths(root)
    from factory_image import prepare_image
    from spar3d.system import SPAR3D
    from spar3d.utils import foreground_crop
    output.mkdir(parents=True, exist_ok=True)
    if (output/'model.glb').exists():
        raise ValueError('SPAR 本次输出已存在，请使用新任务目录')
    prepared = foreground_crop(prepare_image(image, output/'work/input'), 1.3)
    prepared.save(output/'prepared.png')
    model = SPAR3D.from_pretrained(str(root/'engines/spar3d/models'), config_name='config.yaml',
                                 weight_name='model.safetensors', low_vram_mode=True)
    model.to('cuda').eval()
    torch.manual_seed(20261009)
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
        mesh, _ = model.run_image(prepared, bake_resolution=texture_resolution, remesh='none',
                                  vertex_count=-1, return_points=True)
    torch.cuda.synchronize()
    peak = torch.cuda.max_memory_allocated()
    if peak <= 0 or mesh is None or not len(mesh.vertices) or not len(mesh.faces):
        raise RuntimeError('SPAR 未返回有效 CUDA 网格')
    target = output/'model.glb'
    mesh.export(target, include_normals=True)
    from factory_glb import inspect_texture_glb
    checks = inspect_texture_glb(target)
    (output/'output-adapter.json').write_text(json.dumps({'engine':'spar3d','version':'0.7.0',
        'gpuInferenceExecuted':True,'peakVramBytes':peak,'appearance':'texture','jointMultiview':False,
        'sourceRevision':'fdc311b16809e6a8adc2f5a3407ebb3db1a95bd1','textureResolution':texture_resolution,
        'seed':20261009,
        'outputSha256':hashlib.sha256(target.read_bytes()).hexdigest(),'checks':checks},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--input');parser.add_argument('--input-set');parser.add_argument('--output')
    parser.add_argument('--texture-resolution',type=int,default=1024);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();root=args.root.resolve()
    if args.check:
        offline(); paths(root)
        import torch, texture_baker, uv_unwrapper, alpha_clip, clip
        from spar3d.system import SPAR3D
        if not torch.cuda.is_available():raise RuntimeError('CUDA 未就绪')
        print(json.dumps({'imports':True,'cuda':True,'gpuInferenceExecuted':False}));return
    if not args.input or not args.output:parser.error('--input and --output required')
    image=validate_input(root,args.input,args.input_set)
    run(root,image,inside(root,args.output),args.texture_resolution)


if __name__=='__main__':main()
