"""Build fixed native image recipes without downloading weights. v0.7.0 / Unclecow.

The first generation imports reviewed HF API metadata with --metadata; later runs
use the compact checked-in source lock. Existing 3D/Python recipes are preserved.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.7.0'
RELEASE = 'master-948-228c707'
EXE = f'runtime/sd-cpp/{RELEASE}/sd-cli.exe'
LOCK = ROOT / 'scripts/providers/image-native-source-lock.json'
OWNED = {'sd-cpp-image-runtime-948', 'z-image-turbo', 'flux2-klein-4b',
         'qwen-image-21-edit', 'qwen-image-edit-2511', 'sdxl-base',
         'sdxl-reference-control', 'sd35-medium', 'flux1-schnell', 'pixart-sigma',
         'sana-sprint-06', 'sana-sprint-16'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(metadata):
    pins = {}

    def hf(repo, filename, target):
        source = metadata[repo]
        if not source.get('revision') or source.get('error'):
            raise ValueError('缺少已核验来源：' + repo)
        entry = next(x for x in source['files'] if x['rfilename'] == filename)
        item = {'path': target, 'size': entry['size'],
                'url': f'https://huggingface.co/{repo}/resolve/{source["revision"]}/{filename}',
                'mirrors': [] if source.get('gated') else [f'https://hf-mirror.com/{repo}/resolve/{source["revision"]}/{filename}'],
                'sourceRepo': repo, 'sourceRevision': source['revision']}
        if entry.get('lfs'):
            item['sha256'] = entry['lfs']['sha256']
        else:
            item['gitBlobSha1'] = entry['blobId']
        if source.get('gated'):
            item['authRequired'] = True
        pins.setdefault(repo, {'revision': source['revision'], 'gated': source.get('gated', False),
                               'license': source.get('license'), 'files': []})['files'].append(entry)
        return item

    old = next(x for x in read(ROOT/'config/setup-catalog.json')['models'] if x['id'] == 'qwen-image-21')
    cuda_files = [copy.deepcopy(x) for x in old['files'] if x.get('extractTo') == 'runtime/cuda12']
    # This adapter loads DLLs only from runtime/cuda12; another engine's torch
    # directory cannot satisfy the private runtime dependency.
    cuda_required = [{'path': x['path']} for x in old['required'] if x['path'].startswith('runtime/cuda12/')]
    runtime_file = {'path': f'cache/setup/sd-{RELEASE}-win-cuda12-x64.zip',
                    'size': 337979057, 'sha256': '14e7a91053809b2c22a6259d5264d3696447828d2afaaa5a4280dbf41f472ba6',
                    'url': f'https://github.com/leejet/stable-diffusion.cpp/releases/download/{RELEASE}/sd-master-228c707-bin-win-cuda12-x64.zip',
                    'mirrors': [], 'extractTo': f'runtime/sd-cpp/{RELEASE}'}
    models = [{'id': 'sd-cpp-image-runtime-948', 'engineId': 'sd-cpp-image-runtime-948',
               'title': '原生图片共用运行器 · stable-diffusion.cpp 948', 'kind': 'utility', 'version': VERSION,
               'description': '独立目录中的固定 CUDA 12 运行器，复用已核验 CUDA DLL；保留既有 Qwen 929 运行器。',
               'sourceUrl': f'https://github.com/leejet/stable-diffusion.cpp/releases/tag/{RELEASE}',
               'files': [runtime_file] + cuda_files,
               'required': [{'path': EXE}, {'path': f'runtime/sd-cpp/{RELEASE}/ggml-cuda.dll'}] + cuda_required,
               'minimumVramMiB': 0, 'minimumRamGiB': 8, 'extractReserveBytes': 2 * 1024**3,
               'license': {'label': 'stable-diffusion.cpp MIT / NVIDIA CUDA 运行库许可',
                           'url': 'https://github.com/leejet/stable-diffusion.cpp/blob/228c707/LICENSE',
                           'notice': '运行器适用 MIT，CUDA 运行库适用 NVIDIA 许可；此项不包含模型授权。',
                           'requiresAcceptance': True}, 'validationStage': 'researched',
               'probes': [{'path': EXE, 'args': ['--help'], 'requiredFlags': ['--backend', '--params-backend', '--llm_vision', '--ip-adapter', '--control-net'], 'timeout': 45}]}]

    def native(engine, title, family, roles, modes, refs, steps, cfg, extra, license_repo,
               description, *, dependencies=None, variant=None, ram=16, transparency=False):
        components = {}
        files = []
        for role, repo, filename in roles:
            target = f'engines/{engine}/models/{Path(filename).name}'
            components[role] = target
            files.append(hf(repo, filename, target))
        caps = {'modes': modes, 'maxReferenceImages': refs, 'maxWidth': 1024, 'maxHeight': 1024,
                'supportsTransparency': transparency, 'textToImage': 'txt2img' in modes,
                'imageEditing': 'edit' in modes, 'imageToImage': 'img2img' in modes,
                'inpainting': 'inpaint' in modes, 'referenceConditioning': 'control' in modes}
        runtime = {'type': 'sd-cpp', 'family': family, 'python': 'runtime/core/.venv/Scripts/python.exe',
                   'executable': EXE, 'release': RELEASE, 'cudaDirectory': 'runtime/cuda12',
                   'components': components, 'extraArgs': extra, 'weightVariant': variant or 'official',
                   'revision': metadata[license_repo]['revision'], 'backend': 'diffusion=cuda0,te=cpu,vae=cuda0',
                   'paramsBackend': 'diffusion=cpu,te=cpu,vae=cpu', 'maxVramGiB': 9}
        manifest = {'version': VERSION, 'source': 'https://huggingface.co/' + license_repo,
                    'entrypoint': 'run.ps1', 'mediaAdapter': True, 'runtime': runtime,
                    'capabilities': caps, 'appearance': {'category': 'image', 'badge': variant or family}}
        item = {'id': engine, 'engineId': engine, 'title': title, 'kind': 'image', 'version': VERSION,
                'mediaAdapter': True, 'description': description, 'sourceUrl': manifest['source'],
                'recommended': False, 'minimumVramMiB': 12288, 'minimumRamGiB': ram,
                'extractReserveBytes': 2 * 1024**3, 'manifest': manifest,
                'appearance': manifest['appearance'], 'capabilities': caps,
                'defaults': {'width': 512, 'height': 512, 'steps': steps, 'cfgScale': cfg, 'seed': 20261009},
                'runtime': runtime, 'files': files,
                'required': [{'path': f['path'], 'size': f['size']} for f in files] + [{'path': f'engines/{engine}/run.ps1'}, {'path': EXE}, {'path': 'runtime/core/.venv/Scripts/python.exe'}],
                'dependencies': ['sd-cpp-image-runtime-948'] + (dependencies or []),
                'adapters': [{'source': 'scripts/providers/image-run.ps1', 'target': f'engines/{engine}/run.ps1'}],
                'license': {'label': metadata[license_repo].get('license') or '官方模型许可',
                            'url': 'https://huggingface.co/' + license_repo,
                            'notice': '模型与文本编码器、VAE、参考控制组件分别适用来源许可；安装前阅读对应模型卡。', 'requiresAcceptance': True},
                'resourceProfile': {'targetGpu': 'RTX 3060 12 GB', 'defaultResolution': '512x512',
                                    'memoryPolicy': 'CUDA 执行扩散与 VAE，文本编码及权重置于主机内存；VAE 分块。',
                                    'measured': False, 'minimumRamGiB': ram},
                'validationStage': 'researched', 'gpuVerified': False,
                'remedy': '在应用内安装固定配方；安装检测只核对组件与 CLI 参数，实际 CUDA 生成需首次任务验证。',
                'probes': [{'path': EXE, 'args': ['--help'], 'requiredFlags': ['--backend', '--params-backend', '--width', '--height'] + (['--llm_vision', '--ref-image'] if 'edit' in modes and family.startswith('qwen') else []), 'timeout': 45}]}
        if any(f.get('authRequired') for f in files):
            item['authRequired'] = True
        models.append(item)
        return item

    t5 = ('t5', 'city96/t5-v1_1-xxl-encoder-gguf', 't5-v1_1-xxl-encoder-Q4_K_M.gguf')
    native('z-image-turbo', '快速文生图 · Z-Image Turbo Q4_K', 'z-image', [
        ('diffusion', 'leejet/Z-Image-Turbo-GGUF', 'z_image_turbo-Q4_K.gguf'),
        ('llm', 'unsloth/Qwen3-4B-Instruct-2507-GGUF', 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'),
        ('vae', 'Comfy-Org/z_image_turbo', 'split_files/vae/ae.safetensors')],
        ['txt2img'], 0, 8, 1, ['--sampling-method', 'euler'], 'leejet/Z-Image-Turbo-GGUF',
        '8 步快速文字生成，固定 Q4_K 扩散权重和 Qwen3 文本编码器。', variant='Q4_K')
    native('flux2-klein-4b', '快速生图与多图编辑 · FLUX.2 klein 4B Q4_0', 'klein', [
        ('diffusion', 'leejet/FLUX.2-klein-4B-GGUF', 'flux-2-klein-4b-Q4_0.gguf'),
        ('llm', 'unsloth/Qwen3-4B-GGUF', 'Qwen3-4B-Q4_K_M.gguf'),
        ('vae', 'black-forest-labs/FLUX.2-small-decoder', 'full_encoder_small_decoder.safetensors')],
        ['txt2img', 'edit'], 3, 4, 1, ['--sampling-method', 'euler'], 'leejet/FLUX.2-klein-4B-GGUF',
        '4 步文字生成与参考图编辑；多参考图按选择顺序提交。', variant='Q4_0')
    qwen = native('qwen-image-21-edit', 'Qwen Image 2.1 · 多图编辑与透明 PNG', 'qwen21', [
        ('llmVision', 'Qwen/Qwen3-VL-8B-Instruct-GGUF', 'mmproj-Qwen3VL-8B-Instruct-F16.gguf')],
        ['txt2img', 'edit'], 3, 20, 6, ['--sampling-method', 'euler'], 'Qwen/Qwen3-VL-8B-Instruct-GGUF',
        '复用已接入的 Qwen 2.1 权重，增加独立视觉投影器以支持多图编辑和透明背景提示。',
        dependencies=['qwen-image-21'], variant='Q4_K_M', ram=32, transparency=True)
    for role, name in [('diffusion', 'qwen-image-2.1-Q4_K_M.gguf'), ('llm', 'Qwen3VL-8B-Instruct-Q4_K_M.gguf'), ('vae', 'qwen_image_2.1_vae_bf16.safetensors')]:
        qwen['runtime']['components'][role] = f'engines/qwen-image-21/runtime/models/{name}'
        qwen['required'].append({'path': qwen['runtime']['components'][role]})
    diffusion_source = next(x for x in old['files'] if x['path'] == qwen['runtime']['components']['diffusion'])
    qwen['runtime']['revision'] = diffusion_source['url'].split('/resolve/', 1)[1].split('/', 1)[0]
    qwen['runtime']['visionRevision'] = metadata['Qwen/Qwen3-VL-8B-Instruct-GGUF']['revision']
    qwen['manifest']['source'] = 'https://huggingface.co/Comfy-Org/Qwen-Image-2.1'
    qwen['sourceUrl'] = qwen['manifest']['source']
    qwen['license']['url'] = qwen['manifest']['source']
    qwen['license']['label'] = 'Qwen Image 2.1 / Qwen3 VL 官方模型许可'
    native('qwen-image-edit-2511', '多图编辑 · Qwen Image Edit 2511 Q4_K_M', 'qwen2511', [
        ('diffusion', 'unsloth/Qwen-Image-Edit-2511-GGUF', 'qwen-image-edit-2511-Q4_K_M.gguf'),
        ('llm', 'mradermacher/Qwen2.5-VL-7B-Instruct-GGUF', 'Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf'),
        ('llmVision', 'mradermacher/Qwen2.5-VL-7B-Instruct-GGUF', 'Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf'),
        ('vae', 'Comfy-Org/Qwen-Image_ComfyUI', 'split_files/vae/qwen_image_vae.safetensors')],
        ['edit'], 3, 20, 2.5, ['--sampling-method', 'euler', '--flow-shift', '3', '--model-args', 'qwen_image_zero_cond_t=true'],
        'unsloth/Qwen-Image-Edit-2511-GGUF', '固定 2511 编辑模型，启用官方要求的零条件时间参数；主机内存预留 32 GB。', variant='Q4_K_M', ram=32)
    sdxl = native('sdxl-base', '经典生图与局部重绘 · SDXL 1.0', 'sdxl', [
        ('model', 'stabilityai/stable-diffusion-xl-base-1.0', 'sd_xl_base_1.0.safetensors'),
        ('vae', 'madebyollin/sdxl-vae-fp16-fix', 'sdxl.vae.safetensors')],
        ['txt2img', 'img2img', 'inpaint'], 1, 25, 6, ['--sampling-method', 'dpm++2m', '--scheduler', 'karras'],
        'stabilityai/stable-diffusion-xl-base-1.0', '完整 SDXL checkpoint 使用 --model；支持原图重绘和蒙版局部修改。', variant='FP16')
    ctrl = native('sdxl-reference-control', 'SDXL · 外观参考与轮廓控制', 'sdxl', [
        ('clipVision', 'h94/IP-Adapter', 'models/image_encoder/model.safetensors'),
        ('ipAdapter', 'h94/IP-Adapter', 'sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors'),
        ('controlNet', 'xinsir/controlnet-canny-sdxl-1.0', 'diffusion_pytorch_model.safetensors')],
        ['txt2img', 'img2img', 'inpaint', 'control'], 1, 25, 6, ['--sampling-method', 'dpm++2m', '--scheduler', 'karras'],
        'h94/IP-Adapter', '复用 SDXL 底模，IP-Adapter Plus 保持外观，Canny ControlNet 约束轮廓；控制图应为已提取的边缘。',
        dependencies=['sdxl-base'], ram=32)
    ctrl['runtime']['components'].update(sdxl['runtime']['components'])
    ctrl['runtime']['backend'] = 'diffusion=cuda0,te=cpu,vae=cuda0,controlnet=cuda0'
    ctrl['runtime']['paramsBackend'] = 'diffusion=cpu,te=cpu,vae=cpu,controlnet=cpu'
    ctrl['required'] += [copy.deepcopy(x) for x in sdxl['required'] if x['path'].startswith('engines/sdxl-base/models/')]
    ctrl['probes'][0]['requiredFlags'] += ['--clip_vision', '--ip-adapter', '--control-net', '--control-image', '--mask']
    native('sd35-medium', '通用文字生图 · Stable Diffusion 3.5 Medium', 'sd35', [
        ('model', 'stabilityai/stable-diffusion-3.5-medium', 'sd3.5_medium.safetensors'),
        ('clipL', 'Comfy-Org/stable-diffusion-3.5-fp8', 'text_encoders/clip_l.safetensors'),
        ('clipG', 'Comfy-Org/stable-diffusion-3.5-fp8', 'text_encoders/clip_g.safetensors'), t5],
        ['txt2img'], 0, 28, 4.5, ['--sampling-method', 'euler'], 'stabilityai/stable-diffusion-3.5-medium',
        '固定官方 Medium checkpoint，T5 使用明确 Q4_K_M 档位；官方许可需先接受并提供 HF 读取令牌。', variant='FP16+T5-Q4_K_M', ram=32)
    native('flux1-schnell', '4 步文生图 · FLUX.1 schnell Q4_0', 'flux1', [
        ('diffusion', 'leejet/FLUX.1-schnell-gguf', 'flux1-schnell-q4_0.gguf'),
        ('clipL', 'comfyanonymous/flux_text_encoders', 'clip_l.safetensors'), t5,
        ('vae', 'Comfy-Org/z_image_turbo', 'split_files/vae/ae.safetensors')],
        ['txt2img'], 0, 4, 1, ['--sampling-method', 'euler'], 'leejet/FLUX.1-schnell-gguf',
        '固定 schnell Q4_0 与 Q4_K_M T5，4 步文字生成；不自动替换为 dev。', variant='Q4_0', ram=32)
    native('pixart-sigma', '轻量文字生图 · PixArt Sigma XL-2', 'pixart', [
        ('diffusion', 'PixArt-alpha/PixArt-Sigma-XL-2-1024-MS', 'transformer/diffusion_pytorch_model.safetensors'), t5,
        ('vae', 'PixArt-alpha/PixArt-Sigma-XL-2-1024-MS', 'vae/diffusion_pytorch_model.safetensors')],
        ['txt2img'], 0, 20, 4.5, ['--sampling-method', 'euler'], 'PixArt-alpha/PixArt-Sigma-XL-2-1024-MS',
        '固定 Sigma XL-2 diffusion transformer、配套 VAE 与 Q4_K_M T5；保留官方 1024 模型身份。', variant='FP32+T5-Q4_K_M')
    # Same basename in transformer/vae requires role-specific destinations.
    pixart = models[-1]
    for role, f in zip(('diffusion', 't5', 'vae'), pixart['files']):
        if role in {'diffusion', 'vae'}:
            f['path'] = f'engines/pixart-sigma/models/{role}.safetensors'
            pixart['runtime']['components'][role] = f['path']
    pixart['required'] = [{'path': f['path'], 'size': f['size']} for f in pixart['files']] + [{'path': 'engines/pixart-sigma/run.ps1'}, {'path': EXE}, {'path': 'runtime/core/.venv/Scripts/python.exe'}]
    profiles = []
    for engine, size in [('sana-sprint-06', '0.6'), ('sana-sprint-16', '1.6')]:
        repo = f'Efficient-Large-Model/Sana_Sprint_{size}B_1024px_diffusers'
        files = [hf(repo, x['rfilename'], f'engines/{engine}/models/{x["rfilename"]}')
                 for x in metadata[repo]['files'] if x['rfilename'] not in {'.gitattributes', 'README.md'}]
        caps = {'modes': ['txt2img', 'img2img'], 'maxReferenceImages': 1,
                'maxWidth': 1024, 'maxHeight': 1024, 'supportsTransparency': False,
                'supportsNegativePrompt': False, 'textToImage': True, 'imageToImage': True}
        runtime = {'type': 'diffusers', 'family': 'sana-sprint',
                   'python': f'engines/{engine}/.venv/Scripts/python.exe',
                   'modelDirectory': f'engines/{engine}/models', 'weightVariant': f'{size}B-BF16',
                   'revision': metadata[repo]['revision'], 'useResolutionBinning': False,
                   'diffusersVersion': '0.35.1'}
        manifest = {'version': VERSION, 'source': 'https://huggingface.co/' + repo,
                    'entrypoint': 'run.ps1', 'mediaAdapter': True, 'runtime': runtime,
                    'capabilities': caps, 'appearance': {'category': 'image', 'badge': f'{size}B'}}
        model = {'id': engine, 'engineId': engine, 'title': f'2 步生图与重绘 · Sana Sprint {size}B',
                 'kind': 'image', 'version': VERSION, 'mediaAdapter': True,
                 'description': '固定 Diffusers 原生管线，2 步生成与图生图；关闭内部尺寸分桶，按顶层请求生成。',
                 'sourceUrl': manifest['source'], 'recommended': False,
                 'minimumVramMiB': 12288, 'minimumRamGiB': 16, 'extractReserveBytes': 10 * 1024**3,
                 'files': files, 'manifest': manifest, 'appearance': manifest['appearance'],
                 'capabilities': caps, 'runtime': runtime,
                 'defaults': {'width': 512, 'height': 512, 'steps': 2, 'cfgScale': 4.5, 'seed': 20261009},
                 'adapters': [{'source': 'scripts/providers/image-run.ps1', 'target': f'engines/{engine}/run.ps1'}],
                 'dependencies': [], 'deploymentProfile': engine,
                 'required': [{'path': f['path'], 'size': f['size']} for f in files] + [{'path': f'engines/{engine}/run.ps1'}, {'path': runtime['python']}],
                 'license': {'label': 'Apache-2.0 / Gemma 文本编码器许可', 'url': manifest['source'],
                             'notice': 'Sana Sprint 适用 Apache-2.0；随模型发布的 Gemma 文本编码器同时适用 Gemma 使用条款。请阅读来源模型卡和 Google Gemma 条款。',
                             'requiresAcceptance': True},
                 'resourceProfile': {'targetGpu': 'RTX 3060 12 GB', 'defaultResolution': '512x512',
                                     'memoryPolicy': 'CUDA 执行，模型逐模块移入 GPU，VAE 分块。', 'measured': False},
                 'validationStage': 'researched', 'gpuVerified': False,
                 'remedy': '应用内安装固定权重与独立 Python 3.11 环境；首次 GPU 生成结果仍需验收。'}
        models.append(model)
        profiles.append({'id': engine, 'version': VERSION, 'python': '3.11.14',
                         'environment': f'engines/{engine}/.venv',
                         'requirements': 'config/deployment-requirements/sana-sprint.txt',
                         'torchIndex': 'https://download.pytorch.org/whl/cu124',
                         'verifyCommands': [['-c', 'import torch; from diffusers import SanaSprintPipeline,SanaSprintImg2ImgPipeline; assert torch.cuda.is_available(), "CUDA unavailable"; print("Sana Sprint pipelines/imports and CUDA checked; weights not loaded")']]})
    # Deduplicate lock entries used by several recipes.
    for source in pins.values():
        source['files'] = list({x['rfilename']: x for x in source['files']}.values())
    return models, pins, profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, default=LOCK)
    args = parser.parse_args()
    models, pins, generated_profiles = build(read(args.metadata))
    catalog = ROOT/'config/image3d-models.json'
    old = read(catalog)['models'] if catalog.is_file() else []
    write(catalog, {'version': VERSION, 'author': 'Unclecow', 'models': models + [x for x in old if x['id'] not in OWNED]})
    profiles = ROOT/'config/image3d-profiles.json'
    old_profiles = read(profiles)['profiles'] if profiles.is_file() else []
    write(profiles, {'version': VERSION, 'author': 'Unclecow', 'profiles': generated_profiles + [x for x in old_profiles if x['id'] not in OWNED]})
    write(LOCK, pins)
    print('Wrote 9 native + 2 Sana image models and 1 shared runtime; existing extension recipes preserved.')


if __name__ == '__main__':
    main()
