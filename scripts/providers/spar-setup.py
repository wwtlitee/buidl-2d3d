"""Build SPAR3D's private MSVC/CUDA extensions. v0.7.0 / Unclecow.

Only fixed source is patched. No system installer or background downloader is used.
The optional source/python/build-root arguments are for isolated compile validation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def detect_msvc():
    finder = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    if not finder.is_file():
        raise RuntimeError('请先安装 Visual Studio 2022 C++ Build Tools；不会自动安装系统组件')
    result = subprocess.run([str(finder), '-latest', '-products', '*', '-requires',
                             'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                            capture_output=True, text=True, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    candidates = [Path(x.strip())/'VC/Auxiliary/Build/vcvars64.bat' for x in result.stdout.splitlines() if x.strip()]
    path = next((x for x in candidates if x.is_file()), None)
    if path is None:
        raise RuntimeError('Visual Studio 缺少 x64 C++ 工具链，请先补齐；不会自动修改系统')
    return path


def compiler_environment(toolchain):
    vcvars = detect_msvc()
    # Pass the command line unchanged: list2cmdline would backslash-escape the
    # embedded quotes, which cmd.exe does not interpret as C-style escapes.
    result = subprocess.run(f'cmd.exe /d /s /c ""{vcvars}" >nul && set"',
                            capture_output=True, text=True, encoding='utf-8', errors='replace', check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # Windows names are case insensitive; an ordinary dict containing both
    # Path and PATH would let the inherited value mask the portable SDK path.
    env = {key.upper(): value for key, value in os.environ.items()}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition('=')
        if separator and key:
            env[key.upper()] = value
    cuda = toolchain/'cuda-12.4'
    headers = toolchain/'windows-sdk/microsoft.windows.sdk.cpp/c/Include/10.0.26100.0'
    libs = toolchain/'windows-sdk/microsoft.windows.sdk.cpp.x64/c'
    sdk_bin = toolchain/'windows-sdk/microsoft.windows.sdk.cpp/c/bin/10.0.26100.0/x64'
    if not (cuda/'bin/nvcc.exe').is_file() or not (headers/'ucrt/stddef.h').is_file():
        raise RuntimeError('固定 CUDA12.4 / Windows SDK 工具链尚未部署')
    env.update(CUDA_HOME=str(cuda), CUDA_PATH=str(cuda), TORCH_CUDA_ARCH_LIST='8.6',
               USE_CUDA='1', USE_METAL='0', MAX_JOBS='2', DISTUTILS_USE_SDK='1', PYTHONUTF8='1')
    env['PATH'] = str(sdk_bin) + os.pathsep + str(cuda/'bin') + os.pathsep + env.get('PATH', '')
    env['INCLUDE'] = ';'.join(str(headers/x) for x in ('ucrt', 'um', 'shared')) + ';' + env.get('INCLUDE', '')
    env['LIB'] = ';'.join(str(libs/x/'x64') for x in ('ucrt', 'um')) + ';' + env.get('LIB', '')
    return env


def patch_checked(path, expected_sha256, replacement):
    before = path.read_bytes()
    after = replacement.encode('utf-8')
    if before == after:
        return
    if hashlib.sha256(before).hexdigest() != expected_sha256:
        raise ValueError('SPAR 固定源码已改变，拒绝在未知源上应用 Windows 补丁：' + str(path))
    backup = path.with_name(path.name + '.factory-original-0.7.0')
    if not backup.exists():
        backup.write_bytes(before)
    path.write_bytes(after)


def extension_setup(name):
    cuda = name == 'texture_baker'
    kind = 'CUDAExtension' if cuda else 'CppExtension'
    return f'''# Controlled MSVC build patch. v0.7.0 / Unclecow.
from pathlib import Path
from setuptools import setup, find_packages
from torch.utils.cpp_extension import BuildExtension, {kind}
base = Path(__file__).resolve().parent
sources = sorted(str(x) for x in (base / "{name}/csrc").glob("*.cpp"))
''' + (f'sources += sorted(str(x) for x in (base / "{name}/csrc").glob("*.cu"))\n' if cuda else '') + f'''
assert sources, "Native extension source missing"
flags = {{"cxx": ["/O2", "/openmp", "/std:c++17"]}}
''' + ('flags["nvcc"] = ["-O3", "-std=c++17", "--allow-unsupported-compiler"]\n' if cuda else '') + f'''
extension = {kind}("{name}._C", sources, extra_compile_args=flags)
''' + ('extension.libraries = ["cudart_static" if x == "cudart" else x for x in extension.libraries]\n' if cuda else '') + f'''
setup(name="{name}", version="0.0.1", packages=find_packages(),
      ext_modules=[extension], cmdclass={{"build_ext": BuildExtension.with_options(use_ninja=False)}})
'''


def check_auxiliary_config(config):
    if not isinstance(config, dict):
        raise ValueError('SPAR 受限模型配置必须为对象')
    for key in ('image_tokenizer', 'pdiff_image_tokenizer'):
        value = config.get(key) or {}
        if not isinstance(value, dict):
            raise ValueError('DINO 配置格式无效')
        repo = value.get('pretrained_model_name_or_path', 'facebook/dinov2-large')
        if repo != 'facebook/dinov2-large':
            raise ValueError('固定配方未提供这个 DINO 编码器：' + str(repo))
    clip_config = config.get('image_estimator') or {}
    if not isinstance(clip_config, dict):
        raise ValueError('CLIP 配置格式无效')
    clip_name = clip_config.get('model', 'ViT-L/14@336px')
    variants = {'ViT-L/14': 'ViT-L-14.pt', 'ViT-L/14@336px': 'ViT-L-14-336px.pt'}
    if clip_name not in variants:
        raise ValueError('固定配方未提供这个 AlphaCLIP 基础权重：' + str(clip_name))
    return {'dinoRepo': 'facebook/dinov2-large', 'clipName': clip_name, 'clipFile': variants[clip_name]}


def patch_source(root, source):
    lock = json.loads((root/'scripts/providers/spar-source-lock.json').read_text(encoding='utf-8'))
    for name in ('texture_baker', 'uv_unwrapper'):
        relative = name + '/setup.py'
        patch_checked(source/relative, lock['sourceHashes'][relative], extension_setup(name))
    relative = 'spar3d/models/tokenizers/image.py'
    original = (source/relative).with_name('image.py.factory-original-0.7.0')
    text = (original if original.is_file() else source/relative).read_text(encoding='utf-8')
    text = 'import os\n' + text.replace('Dinov2Model.from_pretrained(self.cfg.pretrained_model_name_or_path)',
        'Dinov2Model.from_pretrained(os.environ["SPAR3D_DINOV2_PATH"], local_files_only=True)')
    patch_checked(source/relative, lock['sourceHashes'][relative], text)
    relative = 'spar3d/utils.py'
    original = (source/relative).with_name('utils.py.factory-original-0.7.0')
    text = (original if original.is_file() else source/relative).read_text(encoding='utf-8')
    # The native adapter uses the app's BiRefNet preparation. Keep the unused
    # upstream removal function's annotation without importing its downloader.
    text = text.replace('from transparent_background import Remover', 'from typing import Any as Remover')
    patch_checked(source/relative, lock['sourceHashes'][relative], text)
    relative = 'spar3d/models/image_estimator/clip_based_estimator.py'
    original = (source/relative).with_name('clip_based_estimator.py.factory-original-0.7.0')
    text = (original if original.is_file() else source/relative).read_text(encoding='utf-8')
    text = text.replace('self.cfg.model, download_root=os.environ.get("ALPHA_CLIP_PATH", None)',
        'os.path.join(os.environ["ALPHA_CLIP_PATH"], os.environ["SPAR3D_CLIP_FILE"]), device="cpu"')
    text = text.replace('self.model.eval()', 'self.model.half()\n        self.model.eval()', 1)
    patch_checked(source/relative, lock['sourceHashes'][relative], text)


def build(root, source, python, toolchain, build_root, native, readonly_dependencies=None, build_only=False):
    env = compiler_environment(toolchain)
    patch_source(root, source)
    build_root.mkdir(parents=True, exist_ok=True)
    native.mkdir(parents=True, exist_ok=True)
    for name in ('texture_baker', 'uv_unwrapper'):
        prefix = 'import torch,sys,runpy;'
        if readonly_dependencies:
            prefix += f'sys.path.append({str(readonly_dependencies)!r});'
        prefix += "runpy.run_path('setup.py',run_name='__main__')"
        command = [str(python), '-c', prefix, 'build_ext', '--build-temp', str(build_root/name), '--build-lib', str(native)]
        log = build_root/(name+'.log')
        with log.open('wb') as output:
            result = subprocess.run(command, cwd=source/name, env=env, stdout=output, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('SPAR Windows 扩展编译失败：' + name + '；日志：' + str(log))
        for path in (source/name/name).glob('*.py'):
            target = native/name/path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    if not build_only:
        import yaml
        config = yaml.safe_load((root/'engines/spar3d/models/config.yaml').read_text(encoding='utf-8'))
        selection = check_auxiliary_config(config)
        (root/'engines/spar3d/models/factory-config.json').write_text(json.dumps(selection, indent=2)+'\n', encoding='utf-8')
        (root/'engines/spar3d/NOTICE.txt').write_text(
            'Powered by Stability AI\nThis Stability AI Model is licensed under the Stability AI Community License, Copyright © Stability AI Ltd. All Rights Reserved.\n'
            'Unclecow v0.7.0 modifications: private MSVC/CUDA build flags; offline DINOv2/CLIP paths; native BiRefNet background preparation.\n', encoding='utf-8')
    print(json.dumps({'compiled': True, 'gpuInferenceExecuted': False, 'nativeDirectory': str(native)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--toolchain', type=Path)
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--native', type=Path)
    parser.add_argument('--readonly-dependencies', type=Path)
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--check-tools', action='store_true')
    args = parser.parse_args()
    if args.check_tools:
        print(json.dumps({'msvc': str(detect_msvc()), 'gpuInferenceExecuted': False})); return
    root = args.root.resolve()
    build(root, args.source or root/'engines/spar3d/src', args.python,
          args.toolchain or root/'runtime/toolchains/spar3d', args.build_root or root/'cache/build/spar3d',
          args.native or root/'engines/spar3d/native', args.readonly_dependencies, args.build_only)


if __name__=='__main__':
    main()
