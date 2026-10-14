"""Owned, offline WanGP Python API adapter. v0.7.0 / Unclecow.

The desktop queue owns this process; no Gradio server or browser is started.
Weights are provisioned exclusively by Setup's reviewed download manifest.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def contained(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('视频组件路径越界')
    return target


def load_model(root, engine_id):
    source = contained(root, 'config/video-models.json')
    catalog = json.loads(source.read_text(encoding='utf-8'))
    model = next((item for item in catalog['models'] if item['engineId'] == engine_id), None)
    if model is None or not model.get('installable', True):
        raise ValueError('这个视频模型尚未提供可安装配方')
    return model


def verify_installed(root, model):
    for item in model.get('required', []):
        path = contained(root, item['path'])
        if item.get('directory'):
            if not path.is_dir():
                raise RuntimeError('请在「模型与环境」部署缺失目录：' + item['path'])
            continue
        if not path.is_file():
            raise RuntimeError('请在「模型与环境」下载部署缺失组件：' + item['path'])
        if item.get('size') and path.stat().st_size != item['size']:
            raise RuntimeError('视频组件大小校验失败，请在「模型与环境」重新校验：' + item['path'])
        if item.get('sha256'):
            checksum = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                    checksum.update(chunk)
            if checksum.hexdigest() != item['sha256']:
                raise RuntimeError('视频组件哈希校验失败，请在「模型与环境」重新校验：' + item['path'])


def integer(request, key, default, minimum, maximum):
    value = request.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{key} 必须是 {minimum} 到 {maximum} 之间的整数')
    return value


def build_settings(request, model, defaults):
    if not isinstance(request, dict):
        raise ValueError('视频请求必须是对象')
    prompt = request.get('prompt', '')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 24000 or '\0' in prompt:
        raise ValueError('请输入视频描述，最多 24000 字')
    mode = request.get('mode', 'txt2video')
    capabilities = model['capabilities']
    if mode not in capabilities['modes']:
        raise ValueError('所选模型不支持这个视频输入方式')
    configured = model['defaults']; limits = model['limits']
    width = integer(request, 'width', configured['width'], 128, 2048)
    height = integer(request, 'height', configured['height'], 128, 2048)
    if width % limits['sizeMultiple'] or height % limits['sizeMultiple'] or width * height > limits['maxPixels']:
        raise ValueError('视频尺寸超出所选模型的本地试跑范围')
    rule = limits['frameRule']
    frames = integer(request, 'frames', configured['frames'], rule['min'], rule['max'])
    if (frames - rule['offset']) % rule['multiple']:
        raise ValueError('视频帧数不符合所选模型的编码规则')
    fps = integer(request, 'fps', configured['fps'], 1, 60)
    if fps not in limits['fps']:
        raise ValueError('所选模型不支持这个帧率')
    steps = integer(request, 'steps', configured['steps'], 1, limits['maxSteps'])
    if limits.get('allowedSteps') and steps not in limits['allowedSteps']:
        raise ValueError('此蒸馏配方固定采样步数，不会由运行器静默改写请求')
    seed = integer(request, 'seed', 12345, 0, 2147483647)
    references = request.get('imageReferences', [])
    if not isinstance(references, list) or len(references) > capabilities.get('maxReferenceImages', 0):
        raise ValueError('参考图片数量超出所选模型范围')
    if any(not isinstance(item, dict) or not isinstance(item.get('path'), str) or not item['path'] for item in references):
        raise ValueError('参考图片路径无效')
    if mode == 'txt2video' and references:
        raise ValueError('纯文字视频请求不能携带图片，请选择图片生视频')
    if mode == 'img2video' and not references:
        raise ValueError('图片生视频至少需要一张起始图片')
    if mode == 'reference-video' and not (references or request.get('referenceVideoPath') or request.get('referenceAudioPath')):
        raise ValueError('参考生成至少需要一项参考素材')
    settings = dict(defaults)
    settings.update(model.get('settings', {}))
    settings.update(model_type=model['runtimeModelType'], prompt=prompt.strip(), resolution=f'{width}x{height}',
                    video_length=frames, force_fps=fps, duration_seconds=(frames - 1) / fps,
                    num_inference_steps=steps, seed=seed)
    negative = request.get('negativePrompt', '')
    if not isinstance(negative, str) or len(negative) > 24000 or '\0' in negative:
        raise ValueError('反向描述无效')
    if negative and capabilities.get('supportsNegativePrompt') is False:
        raise ValueError('此蒸馏模型不支持反向描述，请清空后继续')
    if negative:
        settings['negative_prompt'] = negative
    # Pristine defaults can enable reference inputs; reset before applying this request.
    for key in ('image_start', 'image_end', 'image_refs', 'video_guide', 'audio_guide'):
        settings.pop(key, None)
    settings.update(image_prompt_type='', video_prompt_type='', audio_prompt_type='')
    if mode == 'img2video':
        if len(references) > 2 or (len(references) == 2 and not capabilities.get('endImage')):
            raise ValueError('所选模型只支持一张起始图片')
        settings.update(image_start=[references[0]['path']], image_prompt_type='S')
        if len(references) == 2:
            settings.update(image_end=[references[1]['path']], image_prompt_type='SE')
    elif mode == 'reference-video':
        if references:
            settings.update(image_refs=[item['path'] for item in references], video_prompt_type='I')
        if request.get('referenceVideoPath'):
            if not capabilities.get('referenceVideo'):
                raise ValueError('所选模型没有开放参考视频输入')
            settings['video_guide'] = request['referenceVideoPath']
            settings['video_prompt_type'] += 'V-U'
        if request.get('referenceAudioPath'):
            if not capabilities.get('referenceAudio'):
                raise ValueError('所选模型没有开放参考音频输入')
            settings.update(audio_guide=request['referenceAudioPath'], audio_prompt_type='A')
    return settings


def disable_network():
    """A missing optional asset must stop the child instead of downloading silently."""
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                      GRADIO_ANALYTICS_ENABLED='False', HF_HUB_DISABLE_TELEMETRY='1')
    def blocked(*_args, **_kwargs):
        raise RuntimeError('推理阶段禁止联网下载，请在「模型与环境」安装缺失组件')
    socket.create_connection = blocked
    socket.socket.connect = blocked


def open_session(root, model, output):
    runtime = contained(root, 'video-lab/wangp/src')
    sys.path.insert(0, str(runtime))
    disable_network()
    from shared.api import init
    config_folder = output / 'runtime-config'
    config_folder.mkdir(parents=True, exist_ok=True)
    config = dict(model['runtimeConfig'])
    config.update(checkpoints_paths=[str(contained(root, 'video-lab/wangp/models'))],
                  save_path=str(output / 'media'), save_path_image=str(output / 'media'),
                  save_path_audio=str(output / 'media'), metadata_type='settings',
                  deepy_first_launch_prime_enabled=False)
    config_path = config_folder / 'wgp_config.json'
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    return init(root=runtime, config_path=config_path, output_dir=output / 'media',
                cli_args=['--attention', 'sdpa', '--profile', '5', '--preload', '0', '--bf16'],
                console_output=True, console_isatty=False)


def run(root, model, request, output):
    verify_installed(root, model)
    # Reject invalid parameters before importing the GPU runtime.
    build_settings(request, model, {})
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('视频模型需要可用的 NVIDIA CUDA 环境，不会转为 CPU 生成')
    output.mkdir(parents=True, exist_ok=True)
    session = open_session(root, model, output)
    settings = build_settings(request, model, session.get_default_settings(model['runtimeModelType']))
    torch.cuda.reset_peak_memory_stats()
    job = session.submit_task(settings)
    for event in job.events.iter(timeout=0.2):
        if event.kind in ('status', 'info'):
            print(str(event.data), flush=True)
        elif event.kind == 'progress':
            print(json.dumps({'phase': event.data.phase, 'progress': event.data.progress}, ensure_ascii=False), flush=True)
    result = job.result()
    if not result.success:
        raise RuntimeError('；'.join(error.message for error in result.errors) or '视频运行器未成功完成')
    torch.cuda.synchronize()
    peak_vram = torch.cuda.max_memory_allocated()
    if peak_vram <= 0:
        raise RuntimeError('本次任务未记录 CUDA 内存活动，不能标记 GPU 推理完成')
    files = [Path(name).resolve() for name in result.generated_files if Path(name).suffix.lower() == '.mp4']
    if not files or any(not path.is_file() or not path.is_relative_to(output.resolve()) for path in files):
        raise RuntimeError('视频运行器没有返回本次任务目录内的 MP4')
    published = []
    for index, path in enumerate(files, 1):
        target = output / ('generated.mp4' if len(files) == 1 else f'generated-{index}.mp4')
        if target.resolve() != path:
            shutil.copy2(path, target)
        published.append(target.resolve())
    adapter = {'provider': 'WanGP Python API', 'runtimeRevision': model['runtimeRevision'],
               'modelType': model['runtimeModelType'], 'precision': model['precision'],
               'memoryProfile': 5, 'attention': 'sdpa', 'gpuInferenceExecuted': True,
               'peakVramBytes': peak_vram,
               'frames': settings['video_length'], 'fps': settings['force_fps'],
               'outputs': [{'path': str(path)} for path in published]}
    (output / 'output-adapter.json').write_text(json.dumps(adapter, ensure_ascii=False, indent=2), encoding='utf-8')
    return adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--engine', required=True)
    parser.add_argument('--request')
    parser.add_argument('--output')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve(); model = load_model(root, args.engine)
    if args.check:
        verify_installed(root, model)
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('视频模型需要可用的 NVIDIA CUDA 环境')
        # Importing and reading its schema does not load model weights or run inference.
        session = open_session(root, model, contained(root, 'cache/video-probe/' + model['engineId']))
        if not session.get_model_schema(model['runtimeModelType']):
            raise RuntimeError('固定版本运行器没有注册所选模型')
        print(json.dumps({'ready': True, 'gpuInferenceExecuted': False, 'modelType': model['runtimeModelType']}))
        return
    if not args.request or not args.output:
        parser.error('generation requires --request and --output')
    request = json.loads(Path(args.request).read_text(encoding='utf-8-sig'))
    run(root, model, request, Path(args.output).resolve())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
