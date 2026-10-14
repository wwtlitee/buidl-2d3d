"""Private-runtime Qwen Image 2.1 adapter. v0.5.0 / Unclecow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parents[1]
SOURCE = ROOT / "config/image-model-source.json"
VARIANT_SOURCE = ROOT / "config/image-variants.json"
RELEASE = "master-929-3f8527a"
BACKEND = "diffusion=cuda0,te=cpu,vae=cuda0"
PARAMS_BACKEND = "diffusion=cpu,te=cpu,vae=cpu"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_request(value):
    if not isinstance(value, dict):
        raise ValueError("图片生产请求必须是 JSON 对象")
    prompt = value.get("prompt")
    negative = value.get("negativePrompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 6000 or "\0" in prompt:
        raise ValueError("请填写 1 至 6000 字的图片描述")
    if not isinstance(negative, str) or len(negative) > 6000 or "\0" in negative:
        raise ValueError("反向描述无效")
    width, height = value.get("width", 512), value.get("height", 512)
    for dimension in (width, height):
        if type(dimension) is not int or not 256 <= dimension <= 1024 or dimension % 32:
            raise ValueError("图像尺寸需为 256 至 1024 之间的 32 倍数")
    steps, seed = value.get("steps", 20), value.get("seed", 20261003)
    if type(steps) is not int or not 1 <= steps <= 100:
        raise ValueError("采样步数需为 1 至 100")
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("随机种子需为 0 至 2147483647")
    variant = value.get("weightVariant", "Q4_K")
    if not isinstance(variant, str) or variant not in {"Q4_K", "Q4_0", "Q4_K_M", "Q5_K_M", "Q6_K"}:
        raise ValueError("量化版本无效，请选择已安装的准确档位")
    return {"prompt": prompt.strip(), "negativePrompt": negative, "width": width,
            "height": height, "steps": steps, "seed": seed, "weightVariant": variant}


def installed_sources(variant="Q4_K"):
    metadata = json.loads(SOURCE.read_text(encoding="utf-8"))
    if metadata.get("runtimeRelease") != RELEASE:
        raise RuntimeError("文生图固定来源不匹配，请在模型与环境中重新安装")
    files = {item["role"]: item for item in metadata["files"]}
    if set(files) != {"runtime", "diffusion", "textEncoder", "vae"}:
        raise RuntimeError("文生图组件清单不完整")
    paths = {}
    for role, item in files.items():
        if role == "diffusion" and variant != "Q4_K":
            continue
        path = (ENGINE / item["relative"]).resolve()
        if not path.is_relative_to(ENGINE.resolve()) or not path.is_file() or path.stat().st_size != item["size"]:
            raise RuntimeError(f"已校验组件缺失或尺寸改变：{role}")
        paths[role] = path
    executable = (ENGINE / metadata["cli"]["executable"]).resolve()
    if not executable.is_relative_to((ENGINE / "runtime").resolve()) or not executable.is_file():
        raise RuntimeError("官方原生运行程序不存在")
    required = {"--backend", "--params-backend", "--fa", "--diffusion-model", "--llm", "--vae"}
    if not required.issubset(set(metadata["cli"]["requiredFlags"])):
        raise RuntimeError("原生 CLI 参数核验记录不完整")
    if variant != "Q4_K":
        variants = json.loads(VARIANT_SOURCE.read_text(encoding="utf-8"))
        if variants.get("engineId") != "qwen-image-21":
            raise RuntimeError("量化组件清单与工具不匹配")
        selected = next((item for item in variants.get("variants", []) if item.get("id") == variant), None)
        if (not selected or selected.get("status") not in {"ready", "installed"}
                or selected.get("verified") is not True):
            raise RuntimeError(f"量化版本 {variant} 尚未完成下载校验，不会自动切换其他档位")
        pin = selected.get("pin") or {}
        path = (ROOT / selected.get("path", "")).resolve()
        if (not path.is_relative_to((ENGINE / "runtime/models").resolve())
                or not path.is_file() or path.stat().st_size != pin.get("size")
                or path.name != pin.get("filename") or selected.get("bytes") != pin.get("size")):
            raise RuntimeError(f"量化版本 {variant} 的文件缺失或尺寸改变")
        if not all(isinstance(pin.get(key), str) and pin[key] for key in ("repo", "revision", "filename", "sha256")):
            raise RuntimeError("量化版本缺少固定来源与校验记录")
        files["diffusion"] = {"role": "diffusion", "repo": pin["repo"], "revision": pin["revision"],
                              "name": pin["filename"], "size": pin["size"], "sha256": pin["sha256"]}
        paths["diffusion"] = path
    return metadata, files, paths, executable


def cuda_support_directory():
    required = ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")
    candidates = (ROOT / "runtime/cuda12", ROOT / "engines/hunyuan3d/.venv/Lib/site-packages/torch/lib")
    for folder in candidates:
        if all((folder / name).is_file() for name in required):
            return folder
    raise RuntimeError("CUDA 运行组件缺失，请在模型与环境中重新安装文生图组件")


def build_command(request, paths, executable, image_path):
    command = [str(executable), "--diffusion-model", str(paths["diffusion"]),
               "--llm", str(paths["textEncoder"]), "--vae", str(paths["vae"]),
               "--backend", BACKEND, "--params-backend", PARAMS_BACKEND,
               "--max-vram", "cuda0=9", "--fa", "--vae-tiling",
               "--width", str(request["width"]), "--height", str(request["height"]),
               "--steps", str(request["steps"]), "--seed", str(request["seed"]),
               "--cfg-scale", "6.0", "--sampling-method", "euler", "--verbose",
               "--output", str(image_path), "--prompt", request["prompt"]]
    if request["negativePrompt"]:
        command.extend(["--negative-prompt", request["negativePrompt"]])
    return command


def run(request_path, output, validate_only=False):
    request = validate_request(json.loads(Path(request_path).read_text(encoding="utf-8-sig")))
    metadata, files, paths, executable = installed_sources(request["weightVariant"])
    output = Path(output).resolve()
    image_path, result_path = output / "image.png", output / "result.json"
    if image_path.exists() or result_path.exists():
        raise ValueError("本次产物已存在，请使用新的输出目录")
    command = build_command(request, paths, executable, image_path)
    strategy = {"compute": {"diffusion": "cuda0", "textEncoder": "cpu", "vae": "cuda0"},
                "weights": {"diffusion": "cpu", "textEncoder": "cpu", "vae": "cpu"},
                "gpuWeightStaging": "runtime graph segmentation / demand loading",
                "gpuBudgetGiB": 9, "flashAttention": True, "vaeTiling": True,
                "notes": "12 GB 显卡候选配置，参数权重驻留 RAM；真实峰值以 GPU 生成验收为准。"}
    if validate_only:
        print(json.dumps({"valid": True, "request": request, "runtimeRelease": RELEASE,
                          "diffusionFile": files["diffusion"]["name"], "diffusionSha256": files["diffusion"]["sha256"],
                          "memoryStrategy": strategy, "command": command, "gpuInferenceExecuted": False},
                         ensure_ascii=False), flush=True)
        return
    output.mkdir(parents=True, exist_ok=True)
    started, started_at = time.monotonic(), timestamp()
    factory_version = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))["version"]
    result = {"factoryVersion": factory_version, "engine": "qwen-image-21", "kind": "image", "status": "running",
              "weightVariant": request["weightVariant"],
              "request": request, "startedAt": started_at, "runtimeRelease": RELEASE,
              "runtimeCommit": metadata["runtimeCommit"], "memoryStrategy": strategy,
              "models": [{"role": role, "repo": item.get("repo"), "revision": item.get("revision"),
                          "file": item["name"], "sha256": item["sha256"]}
                         for role, item in files.items() if role != "runtime"]}
    save_json(result_path, result)
    print("FACTORY_PHASE: image-generation", flush=True)
    print(f"Qwen Image 2.1 · {request['weightVariant']} · CUDA 采样 / CPU 文本编码 / RAM 权重卸载", flush=True)
    try:
        cuda_support = cuda_support_directory()
        environment = os.environ.copy()
        environment["PATH"] = str(executable.parent) + os.pathsep + str(cuda_support) + os.pathsep + environment.get("PATH", "")
        # An argv array passes user text as data; shell parsing is never used.
        with (output / "native.log").open("w", encoding="utf-8", newline="\n") as log:
            with subprocess.Popen(command, cwd=executable.parent, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  errors="replace", bufsize=1, env=environment,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line.rstrip("\r\n"), flush=True)
                exit_code = process.wait()
        if exit_code:
            raise RuntimeError(f"原生生图程序失败，退出码 {exit_code}")
        if not image_path.is_file() or not image_path.stat().st_size:
            raise RuntimeError("引擎没有产生图片")
        with Image.open(image_path) as image:
            if image.format != "PNG" or image.size != (request["width"], request["height"]):
                raise RuntimeError("生成图片的格式或尺寸不符合请求")
            mode = image.mode
            image.verify()
        with Image.open(image_path) as image:
            alpha_range = image.getchannel("A").getextrema() if "A" in image.getbands() else (255, 255)
            if alpha_range[1] == 0:
                raise RuntimeError("生成图片完全透明，没有可用于转 3D 的可见内容")
        result.update(status="generated-unreviewed", output={"path": str(image_path), "mode": mode,
                      "width": request["width"], "height": request["height"], "bytes": image_path.stat().st_size,
                      "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(), "alphaRange": list(alpha_range)})
    except Exception as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        result.update(finishedAt=timestamp(), seconds=round(time.monotonic() - started, 3))
        save_json(result_path, result)


def main():
    # Windows pipes otherwise inherit the legacy code page; the factory reads UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    arguments = parser.parse_args()
    run(arguments.request, arguments.output, arguments.validate_only)


if __name__ == "__main__":
    main()
