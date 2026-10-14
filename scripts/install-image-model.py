"""Install pinned Qwen Image 2.1 T2I; optional transport controls. v0.2.0 / Unclecow."""
from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engines/qwen-image-21"
SOURCE = ROOT / "config/image-model-source.json"
RELEASE = "master-929-3f8527a"
RUNTIME_COMMIT = "3f8527a46c54ecf4cb4ed6003da8e8982283c73c"
PINNED = [
    {"role": "runtime", "name": "sd-master-3f8527a-bin-win-cuda12-x64.zip", "size": 337915440,
     "sha256": "217d6dead9abd3f827fc338268555cc179234e7e6330ef21ecb1c985e19d2dc7",
     "relative": "runtime/archives/sd-master-3f8527a-bin-win-cuda12-x64.zip",
     "url": f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{RELEASE}/sd-master-3f8527a-bin-win-cuda12-x64.zip"},
    {"role": "diffusion", "repo": "leejet/Qwen-Image-2.1-GGUF",
     "revision": "cc11433936a06e9765f7c0c0b1f0436cfd2b9856", "name": "qwen_image_2.1-Q4_K.gguf",
     "size": 4197494816, "sha256": "29f9c83c249ff0292fb2943fceddfa2319b446601866c82a4f8be062abea72c2",
     "relative": "runtime/models/qwen_image_2.1-Q4_K.gguf"},
    {"role": "textEncoder", "repo": "Qwen/Qwen3-VL-8B-Instruct-GGUF",
     "revision": "f982a07559d4a2f6c8744d840bf6fccab30eea96", "name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
     "size": 5027784800, "sha256": "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2",
     "relative": "runtime/models/Qwen3VL-8B-Instruct-Q4_K_M.gguf"},
    {"role": "vae", "repo": "Comfy-Org/Qwen-Image-2.1",
     "revision": "cb504a4090723e43f17ad01cec0359490e2de613", "name": "vae/qwen_image_2.1_vae_bf16.safetensors",
     "size": 675509688, "sha256": "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9",
     "relative": "runtime/models/qwen_image_2.1_vae_bf16.safetensors"},
]
OUTPUT_LOCK = threading.Lock()
RUNTIME_TRANSPORTS = [
    "https://gh-proxy.com/" + PINNED[0]["url"],
    "https://ghfast.top/" + PINNED[0]["url"],
    "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/assets/593525187",
    PINNED[0]["url"],
]


def emit(value):
    with OUTPUT_LOCK:
        print(json.dumps(value, ensure_ascii=False), flush=True)


def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "2D3DFactory/0.1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def source_metadata():
    repositories = []
    for item in PINNED:
        if not item.get("repo"):
            continue
        url = f"https://huggingface.co/api/models/{item['repo']}/revision/{item['revision']}?blobs=true"
        metadata = read_json_url(url)
        if metadata.get("private") or metadata.get("gated") or metadata.get("disabled"):
            raise ValueError("Only public non-gated weights are allowed")
        if metadata.get("sha") != item["revision"] or metadata.get("id") != item["repo"]:
            raise ValueError(f"Repository identity/revision mismatch: {item['repo']}")
        actual = next((entry for entry in metadata["siblings"] if entry["rfilename"] == item["name"]), None)
        if not actual or actual.get("size") != item["size"] or actual.get("lfs", {}).get("sha256") != item["sha256"]:
            raise ValueError(f"Pinned official LFS metadata mismatch: {item['name']}")
        repositories.append({"repo": item["repo"], "revision": item["revision"], "metadataUrl": url,
                             "private": False, "gated": False, "file": actual})
    # gh uses the configured GitHub API transport without reading/exposing credentials.
    response = subprocess.run(["gh", "api", f"repos/leejet/stable-diffusion.cpp/releases/tags/{RELEASE}"],
                              capture_output=True, check=True, timeout=45, encoding="utf-8", errors="replace")
    release = json.loads(response.stdout)
    asset = next((entry for entry in release["assets"] if entry["name"] == PINNED[0]["name"]), None)
    if (release.get("tag_name") != RELEASE or release.get("target_commitish") != RUNTIME_COMMIT
            or not asset or asset.get("size") != PINNED[0]["size"]
            or asset.get("digest") != "sha256:" + PINNED[0]["sha256"]):
        raise ValueError("Official release asset does not match the pinned digest")
    return {"version": "0.1.0", "author": "Unclecow", "engineId": "qwen-image-21", "kind": "image",
            "runtimeRelease": RELEASE, "runtimeCommit": RUNTIME_COMMIT, "retrievedAt": timestamp(),
            "files": PINNED, "repositories": repositories,
            "runtimeAsset": {key: asset[key] for key in ("id", "name", "size", "digest", "browser_download_url")},
            "status": "downloading", "gpuVerified": False, "receipts": []}


def verify(path, item):
    if path.stat().st_size != item["size"]:
        raise ValueError(f"Size mismatch; existing file retained: {item['name']}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != item["sha256"]:
        raise ValueError(f"Official SHA256 mismatch; existing file retained: {item['name']}")
    return actual


def download(item, verify_only=False, *, source_urls=None, use_environment_proxy=False):
    target = ENGINE / item["relative"]
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"role": item["role"], "file": item["name"], "size": item["size"], "verified": False}
    try:
        if target.exists():
            record.update(sha256=verify(target, item), verified=True, source="verified-local")
            return record
        if verify_only:
            raise FileNotFoundError(f"Not installed: {item['name']}")
        partial = target.with_name(target.name + ".part")
        if partial.exists() and partial.stat().st_size > item["size"]:
            raise ValueError(f"Oversized partial retained: {item['name']}")
        if partial.exists() and partial.stat().st_size == item["size"]:
            record.update(sha256=verify(partial, item), verified=True, source="verified-partial")
            partial.rename(target)
            return record
        urls = list(source_urls) if source_urls is not None else (
            RUNTIME_TRANSPORTS if "url" in item else [
                f"https://{host}/{item['repo']}/resolve/{item['revision']}/{item['name']}"
                for host in ("hf-mirror.com", "huggingface.co")
            ]
        )
        attempts = []
        for url in urls:
            command = ["curl.exe", "--fail", "--location", "--silent", "--show-error"]
            if not use_environment_proxy:
                command.extend(["--noproxy", "*"])
            command.extend(["--retry", "2", "--retry-delay", "2", "--retry-all-errors", "--connect-timeout", "20",
                       "--max-time", "3600", "--speed-time", "90", "--speed-limit", "1024",
                       "--continue-at", "-", "--output", str(partial), url])
            if url.startswith("https://api.github.com/"):
                command.extend(["--header", "Accept: application/octet-stream"])
            errors_file = target.with_name(target.name + ".download-errors.txt")
            with errors_file.open("wb") as errors_stream:
                with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors_stream) as process:
                    started = time.monotonic()
                    while process.poll() is None:
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            downloaded = partial.stat().st_size if partial.exists() else 0
                            emit({"stage": "download", "file": item["name"], "bytes": downloaded,
                                  "totalBytes": item["size"], "percent": round(downloaded * 100 / item["size"], 1),
                                  "seconds": round(time.monotonic() - started, 1)})
            if process.returncode:
                attempts.append({"url": url, "exitCode": process.returncode,
                                 "error": errors_file.read_text(encoding="utf-8", errors="replace")[-2000:]})
                emit({"stage": "fallback", "file": item["name"], "exitCode": process.returncode})
                continue
            record.update(sha256=verify(partial, item), verified=True, source=url, attempts=attempts)
            partial.rename(target)
            break
        if not record["verified"]:
            raise RuntimeError(f"Download failed: {attempts}")
    except Exception as error:
        record["error"] = str(error)
    finally:
        emit(record)
    return record


def install_runtime():
    archive = ENGINE / PINNED[0]["relative"]
    destination = ENGINE / "runtime" / RELEASE
    destination.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Runtime archive contains an unsafe path")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as stream:
                contents = stream.read()
            sha256 = hashlib.sha256(contents).hexdigest()
            if target.exists():
                with target.open("rb") as stream:
                    existing = hashlib.file_digest(stream, "sha256").hexdigest()
                if existing != sha256:
                    raise ValueError(f"Runtime file differs; retained: {entry.filename}")
            else:
                temporary = target.with_name(target.name + ".part")
                temporary.write_bytes(contents)
                temporary.rename(target)
            extracted.append({"path": target.relative_to(ENGINE).as_posix(), "size": len(contents), "sha256": sha256})
    candidates = list(destination.rglob("sd-cli.exe"))
    if len(candidates) != 1:
        raise RuntimeError("Official runtime must contain exactly one sd-cli.exe")
    return candidates[0], extracted


def check_cli(executable):
    environment = os.environ.copy()
    cuda_support = ROOT / "engines/hunyuan3d/.venv/Lib/site-packages/torch/lib"
    dependencies = []
    for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll"):
        path = cuda_support / name
        if not path.is_file():
            raise RuntimeError(f"Existing CUDA 12 vendor dependency missing: {name}")
        with path.open("rb") as stream:
            dependencies.append({"file": name, "path": str(path), "size": path.stat().st_size,
                                 "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                                 "source": "existing official PyTorch CUDA 12 environment"})
    environment["PATH"] = str(executable.parent) + os.pathsep + str(cuda_support) + os.pathsep + environment.get("PATH", "")
    response = subprocess.run([str(executable), "--help"], cwd=executable.parent,
                              capture_output=True, timeout=30, encoding="utf-8", errors="replace", env=environment)
    help_text = response.stdout + response.stderr
    if response.returncode:
        raise RuntimeError(f"Native CLI help failed: {response.returncode}; {help_text[-1200:]}")
    required = ["--diffusion-model", "--llm", "--vae", "--backend", "--params-backend", "--fa",
                "--max-vram", "--vae-tiling", "--width", "--height", "--steps", "--seed",
                "--cfg-scale", "--sampling-method", "--output", "--prompt", "--verbose"]
    missing = [option for option in required if option not in help_text]
    if missing:
        raise RuntimeError(f"Pinned CLI does not support required flags: {missing}")
    help_path = ENGINE / "runtime/cli-help.txt"
    help_path.write_text(help_text, encoding="utf-8")
    return {"executable": executable.relative_to(ENGINE).as_posix(), "helpExitCode": response.returncode,
            "helpSha256": hashlib.sha256(help_text.encode()).hexdigest(), "requiredFlags": required,
            "vendorDependencies": dependencies, "gpuInferenceExecuted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--use-saved-metadata", action="store_true")
    args = parser.parse_args()
    if args.verify_only or args.use_saved_metadata:
        metadata = json.loads(SOURCE.read_text(encoding="utf-8"))
        if metadata.get("files") != PINNED or metadata.get("runtimeRelease") != RELEASE:
            raise ValueError("Saved metadata does not match the approved public pins")
    else:
        metadata = source_metadata()
        save_json(SOURCE, metadata)
    metadata["runtimeTransportMirrors"] = RUNTIME_TRANSPORTS[:2]
    metadata["runtimeTransportPolicy"] = "Same official release bytes only; final official SHA256 is mandatory"
    emit({"stage": "start", "files": len(PINNED), "bytes": sum(item["size"] for item in PINNED), "parallel": 2})
    # Runtime starts first, while the first large weight is transferred concurrently.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: download(item, args.verify_only), PINNED))
    complete = all(record["verified"] for record in results)
    metadata.update(receipts=results, checkedAt=timestamp())
    if complete:
        try:
            executable, extracted = install_runtime()
            metadata["runtimeFiles"] = extracted
            metadata["cli"] = check_cli(executable)
        except Exception as error:
            metadata["runtimeError"] = str(error)
            complete = False
    metadata["status"] = "verified" if complete else "failed"
    save_json(SOURCE, metadata)
    manifest_path = ENGINE / "engine.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if complete:
        if manifest.get("status") != "ready":
            manifest["status"] = "installed"
            manifest["notes"] = "公开 Q4 文生图组件及官方 CUDA12 运行包已完整校验；CLI 检查通过，本机 GPU 生成待验收。"
        manifest["runtimeEntrypoint"] = metadata["cli"]["executable"]
        manifest["modelPins"] = {item["role"]: item.get("revision") or RELEASE for item in PINNED}
    else:
        manifest["status"] = "blocked"
        manifest["notes"] = "公开文生图组件安装未完成，请查看 config/image-model-source.json 的校验结果。"
    save_json(manifest_path, manifest)
    emit({"stage": "complete", "verified": complete, "engineStatus": manifest["status"],
          "runtimeError": metadata.get("runtimeError"), "gpuInferenceExecuted": False})
    raise SystemExit(0 if complete else 1)


if __name__ == "__main__":
    main()
