"""Offline image extension adapters. v0.7.0 / Unclecow.

Only the shipped recipe selects executables, weights and Python pipelines.
The enclosing media launcher owns the process and final artifact receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def rooted_path(root, value, *, must_exist=False):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('组件或图片路径无效')
    path = Path(value)
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('图片或组件路径超出工具目录')
    if must_exist and not path.is_file():
        raise ValueError('图片或组件文件不存在')
    return path


def image_path(root, value):
    from PIL import Image, UnidentifiedImageError
    item = value if isinstance(value, dict) else {'path': value}
    path = rooted_path(root, item.get('path'), must_exist=True)
    if item.get('sha256'):
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item['sha256']:
            raise ValueError('参考图片已改变，请重新选择')
    try:
        with Image.open(path) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'} or max(image.size) > 8192:
                raise ValueError('参考图片格式或尺寸不支持')
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError('参考图片无法读取') from error
    return str(path)


def bounded_number(value, lower, upper, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(label + '超出支持范围')
    return value


def validate_request(value, recipe, root):
    if not isinstance(value, dict):
        raise ValueError('图片请求必须是对象')
    defaults = recipe.get('defaults', {})
    modes = recipe.get('capabilities', {}).get('modes', [])
    mode = value.get('mode', modes[0] if modes else 'txt2img')
    if mode not in modes:
        raise ValueError('当前模型不支持所选模式，不会自动换模型')
    prompt = value.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 6000 or '\0' in prompt:
        raise ValueError('请填写 1 至 6000 字的图片描述')
    negative = value.get('negativePrompt', '')
    if not isinstance(negative, str) or len(negative) > 6000 or '\0' in negative:
        raise ValueError('反向描述无效')
    if negative and recipe.get('capabilities', {}).get('supportsNegativePrompt') is False:
        raise ValueError('此模型管线不支持反向描述，请清空后继续')
    result = {'mode': mode, 'prompt': prompt.strip(), 'negativePrompt': negative}
    for key in ('width', 'height'):
        dimension = value.get(key, defaults.get(key, 512))
        if type(dimension) is not int or not 256 <= dimension <= 1024 or dimension % 32:
            raise ValueError('图片尺寸需为 256 至 1024 之间的 32 倍数')
        result[key] = dimension
    for key, lower, upper, fallback in [('steps', 1, 100, 20), ('seed', 0, 2147483647, 20261009)]:
        number = value.get(key, defaults.get(key, fallback))
        if type(number) is not int or not lower <= number <= upper:
            raise ValueError(key + '超出支持范围')
        result[key] = number
    result['strength'] = bounded_number(value.get('strength', .65), .01, 1, '重绘强度')
    result['cfgScale'] = bounded_number(value.get('cfgScale', defaults.get('cfgScale', 6)), 0, 20, '提示词强度')
    result['controlStrength'] = bounded_number(value.get('controlStrength', .8), 0, 2, '控制强度')
    result['ipAdapterStrength'] = bounded_number(value.get('ipAdapterStrength', .8), 0, 2, '参考强度')
    refs = value.get('imageReferences', [])
    if not isinstance(refs, list) or len(refs) > recipe.get('capabilities', {}).get('maxReferenceImages', 1):
        raise ValueError('参考图片数量超过当前模型限制')
    result['imageReferences'] = [image_path(root, item) for item in refs]
    if mode in {'img2img', 'edit', 'inpaint'} and not refs:
        raise ValueError('此模式需要选择参考图片')
    if mode in {'img2img', 'inpaint'} and len(refs) != 1:
        raise ValueError('传统重绘仅接收一张主参考图')
    if mode == 'txt2img' and refs:
        raise ValueError('文生图不接收参考图，请选择编辑或图生图')
    for key, component in [('maskPath', None), ('controlImagePath', 'controlNet'), ('ipAdapterImagePath', 'ipAdapter')]:
        if value.get(key):
            if key == 'maskPath' and mode != 'inpaint':
                raise ValueError('蒙版只用于局部重绘模式')
            if component and not recipe.get('runtime', {}).get('components', {}).get(component):
                raise ValueError('当前安装档不包含此参考/控制组件')
            result[key] = image_path(root, value[key])
    if mode == 'inpaint' and not result.get('maskPath'):
        raise ValueError('局部重绘需要蒙版，白色区域修改、黑色区域保留')
    if mode == 'control' and not result.get('controlImagePath') and not result.get('ipAdapterImagePath'):
        raise ValueError('参考控制模式需要轮廓控制图或外观参考图')
    transparent = value.get('transparentBackground', False)
    if type(transparent) is not bool:
        raise ValueError('透明背景选项无效')
    if transparent and recipe.get('runtime', {}).get('family') != 'qwen21':
        raise ValueError('当前模型没有已接入的透明通道能力，请选择 Qwen Image 2.1')
    if transparent:
        result['prompt'] = 'This is an RGBA image with transparency. ' + result['prompt'] + '. The image has alpha channel and the background is transparent.'
    result['transparentBackground'] = transparent
    if 'weightVariant' in value:
        expected = recipe.get('runtime', {}).get('weightVariant')
        if value['weightVariant'] != expected:
            raise ValueError('请选择该安装配方的准确量化档位，不会降级或替换')
    return result


def build_sd_command(request, recipe, root, image_output):
    runtime = recipe['runtime']
    command = [str(rooted_path(root, runtime['executable']))]
    flags = {'model': '--model', 'diffusion': '--diffusion-model', 'llm': '--llm',
             'llmVision': '--llm_vision', 'vae': '--vae', 'clipL': '--clip_l',
             'clipG': '--clip_g', 't5': '--t5xxl'}
    for role, flag in flags.items():
        if runtime.get('components', {}).get(role):
            command += [flag, str(rooted_path(root, runtime['components'][role]))]
    command += ['--backend', runtime.get('backend', 'diffusion=cuda0,te=cpu,vae=cuda0'),
                '--params-backend', runtime.get('paramsBackend', 'diffusion=cpu,te=cpu,vae=cpu'),
                '--max-vram', 'cuda0=' + str(runtime.get('maxVramGiB', 9)), '--fa', '--vae-tiling', '--verbose',
                '--width', str(request['width']), '--height', str(request['height']),
                '--steps', str(request['steps']), '--seed', str(request['seed']),
                '--cfg-scale', str(request['cfgScale']), '--output', str(image_output), '--prompt', request['prompt']]
    command += list(runtime.get('extraArgs', []))
    if request['negativePrompt']:
        command += ['--negative-prompt', request['negativePrompt']]
    if request['mode'] == 'edit':
        for path in request['imageReferences']:
            command += ['-r', path]
    elif request['mode'] in {'img2img', 'inpaint'}:
        command += ['--init-img', request['imageReferences'][0], '--strength', str(request['strength'])]
    if request.get('maskPath'):
        command += ['--mask', request['maskPath']]
    components = runtime.get('components', {})
    if request.get('controlImagePath'):
        command += ['--control-net', str(rooted_path(root, components['controlNet'])), '--control-image', request['controlImagePath'], '--control-strength', str(request['controlStrength'])]
    if request.get('ipAdapterImagePath'):
        command += ['--clip_vision', str(rooted_path(root, components['clipVision'])), '--ip-adapter', str(rooted_path(root, components['ipAdapter'])), '--ip-adapter-image', request['ipAdapterImagePath'], '--ip-adapter-strength', str(request['ipAdapterStrength'])]
    return command


def run_sd(request, recipe, root, output):
    command = build_sd_command(request, recipe, root, output/'image.png')
    executable = Path(command[0])
    for path in recipe['runtime'].get('components', {}).values():
        rooted_path(root, path, must_exist=True)
    rooted_path(root, str(executable), must_exist=True)
    env = dict(os.environ)
    cuda = rooted_path(root, recipe['runtime'].get('cudaDirectory', 'runtime/cuda12'))
    env['PATH'] = str(executable.parent) + os.pathsep + str(cuda) + os.pathsep + env.get('PATH', '')
    with (output/'native.log').open('w', encoding='utf-8', newline='\n') as log:
        with subprocess.Popen(command, cwd=executable.parent, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line.rstrip(), flush=True)
            if process.wait():
                raise RuntimeError('原生图片推理失败，请查看本次任务日志；未更换模型')
    # Explicit diffusion=cuda0 fails when CUDA is unavailable; no CPU fallback is requested.
    return {'gpuInferenceExecuted': True, 'backend': recipe['runtime'].get('backend', 'diffusion=cuda0,te=cpu,vae=cuda0'), 'runtimeRelease': recipe['runtime'].get('release')}


def build_sana_arguments(request, runtime):
    return {'prompt': request['prompt'], 'width': request['width'], 'height': request['height'],
            'num_inference_steps': request['steps'], 'guidance_scale': request['cfgScale'],
            'use_resolution_binning': runtime.get('useResolutionBinning', False)}


def run_diffusers(request, recipe, root, output):
    import torch
    from PIL import Image
    from diffusers import SanaSprintPipeline, SanaSprintImg2ImgPipeline
    if not torch.cuda.is_available():
        raise RuntimeError('未检测到 CUDA；此模型不会转为 CPU 或其他模型')
    if recipe['runtime']['family'] != 'sana-sprint':
        raise ValueError('未注册的 Diffusers 模型类型')
    cls = SanaSprintImg2ImgPipeline if request['mode'] == 'img2img' else SanaSprintPipeline
    pipe = cls.from_pretrained(str(rooted_path(root, recipe['runtime']['modelDirectory'])),
                              torch_dtype=torch.bfloat16, local_files_only=True)
    pipe.enable_model_cpu_offload()
    if hasattr(pipe.vae, 'enable_tiling'):
        pipe.vae.enable_tiling()
    args = build_sana_arguments(request, recipe['runtime'])
    args['generator'] = torch.Generator('cpu').manual_seed(request['seed'])
    if request['mode'] == 'img2img':
        with Image.open(request['imageReferences'][0]) as image:
            args['image'] = image.convert('RGB').resize((request['width'], request['height']))
        args['strength'] = request['strength']
    with torch.inference_mode():
        result = pipe(**args).images[0]
    result.save(output/'image.png')
    return {'gpuInferenceExecuted': True, 'backend': 'torch-cuda-model-cpu-offload', 'peakVramBytes': torch.cuda.max_memory_allocated()}


def inspect_generated_image(path, request):
    """Keep requested dimensions and actual alpha distinct from model capability."""
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != 'PNG':
                raise ValueError('原生图片运行器未返回 PNG')
            if image.size != (request['width'], request['height']):
                raise ValueError('生成图片尺寸与顶层请求不符')
            alpha = image.getchannel('A').getextrema() if image.mode == 'RGBA' else (255, 255)
            if alpha[1] == 0:
                raise ValueError('生成图片完全透明')
            if request['transparentBackground'] and alpha[0] == 255:
                raise ValueError('模型返回的 PNG 没有透明像素，请调整描述后重试')
            return {'width': image.width, 'height': image.height, 'imageMode': image.mode,
                    'hasAlphaChannel': image.mode == 'RGBA', 'hasTransparentPixels': alpha[0] < 255}
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError('生成图片无法解码') from error


def load_recipe(root, engine_id):
    data = json.loads((Path(root)/'config/image3d-models.json').read_text(encoding='utf-8'))
    item = next((item for item in data['models'] if item.get('engineId', item['id']) == engine_id), None)
    if item is None or item.get('availability') in {'unreleased', 'cloud-only', 'unsupported'}:
        raise ValueError('此模型没有可执行本地配方')
    return item


def run(root, engine_id, request_path, output_path, validate_only=False):
    root = Path(root).resolve()
    recipe = load_recipe(root, engine_id)
    request = validate_request(json.loads(Path(request_path).read_text(encoding='utf-8-sig')), recipe, root)
    output = rooted_path(root, str(output_path))
    if validate_only:
        print(json.dumps({'valid': True, 'engine': engine_id, 'request': request, 'gpuInferenceExecuted': False}, ensure_ascii=False))
        return
    if (output/'image.png').exists() or (output/'output-adapter.json').exists():
        raise ValueError('本次输出已存在，请使用新任务目录')
    output.mkdir(parents=True, exist_ok=True)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DIFFUSERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    print('FACTORY_PHASE: image-generation', flush=True)
    evidence = run_sd(request, recipe, root, output) if recipe['runtime']['type'] == 'sd-cpp' else run_diffusers(request, recipe, root, output)
    evidence.update(inspect_generated_image(output/'image.png', request))
    evidence.update(engine=engine_id, mode=request['mode'], requestedTransparency=request['transparentBackground'],
                    weightVariant=recipe['runtime'].get('weightVariant'), sourceRevision=recipe['runtime'].get('revision'))
    (output/'output-adapter.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    run(args.root, args.engine, args.request, args.output, args.validate_only)


if __name__ == '__main__':
    main()
