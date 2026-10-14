"""Install four exact public Qwen Image 2.1 GGUF variants. v0.2.0 / Unclecow."""
from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engines/qwen-image-21"
SOURCE = ROOT / "config/image-variants.json"
BASELINE = ROOT / "config/image-model-source.json"
MANIFEST = ENGINE / "engine.json"
VERSION = "0.2.0"
DEFAULT_VARIANT = "Q4_K_M"
PARALLEL_DOWNLOADS = 4
PINNED = [
    {"id": "Q4_0", "label": "Q4_0", "role": "diffusion", "repo": "leejet/Qwen-Image-2.1-GGUF",
     "revision": "cc11433936a06e9765f7c0c0b1f0436cfd2b9856", "name": "qwen_image_2.1-Q4_0.gguf",
     "size": 4197494816, "sha256": "8d992dc95dcbf13196a4aca893697fb56a5c9263e19e7b40c898be2cf29e9147",
     "relative": "runtime/models/qwen_image_2.1-Q4_0.gguf"},
    {"id": "Q4_K_M", "label": "Q4_K_M", "role": "diffusion", "repo": "unsloth/Qwen-Image-2.1-GGUF",
     "revision": "2c31ccd392b367a6637841a143813320a02dff55", "name": "qwen-image-2.1-Q4_K_M.gguf",
     "size": 4199565024, "sha256": "631d532e7ca71e8d90a87c71d3699761a812039d22e3370e87498d87754660fe",
     "relative": "runtime/models/qwen-image-2.1-Q4_K_M.gguf"},
    {"id": "Q5_K_M", "label": "Q5_K_M", "role": "diffusion", "repo": "unsloth/Qwen-Image-2.1-GGUF",
     "revision": "2c31ccd392b367a6637841a143813320a02dff55", "name": "qwen-image-2.1-Q5_K_M.gguf",
     "size": 5390223072, "sha256": "4b53321654dd3bf0aa8dd6bb821fdcb1c9053ca735070e359a23cbb9f97268ce",
     "relative": "runtime/models/qwen-image-2.1-Q5_K_M.gguf"},
    {"id": "Q6_K", "label": "Q6_K", "role": "diffusion", "repo": "unsloth/Qwen-Image-2.1-GGUF",
     "revision": "2c31ccd392b367a6637841a143813320a02dff55", "name": "qwen-image-2.1-Q6_K.gguf",
     "size": 6271551200, "sha256": "2ead1ee665e8ebc5fec517d287a167f5c6a7e985acbf3b1b93143437a04616ac",
     "relative": "runtime/models/qwen-image-2.1-Q6_K.gguf"},
]


def load_base_installer():
    spec = importlib.util.spec_from_file_location("factory_image_installer", ROOT / "scripts/install-image-model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.ENGINE.resolve() != ENGINE.resolve():
        raise RuntimeError("Shared downloader engine directory does not match")
    return module


BASE = load_base_installer()


def workspace_path(path):
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def model_path(item):
    path = (ENGINE / item["relative"]).resolve()
    if not path.is_relative_to((ENGINE / "runtime/models").resolve()):
        raise ValueError("Variant path is outside the model directory")
    return path


def pin(item):
    return {"repo": item["repo"], "revision": item["revision"], "filename": item["name"],
            "size": item["size"], "sha256": item["sha256"]}


def baseline_variant():
    metadata = json.loads(BASELINE.read_text(encoding="utf-8"))
    if metadata.get("status") != "verified" or not metadata.get("gpuVerified"):
        raise RuntimeError("The verified, GPU-tested baseline must be retained before installing variants")
    expected = next(item for item in BASE.PINNED if item["role"] == "diffusion")
    actual = next(item for item in metadata["files"] if item["role"] == "diffusion")
    if actual != expected:
        raise RuntimeError("Baseline diffusion does not match its original immutable pin")
    receipts = metadata.get("receipts", [])
    if len(receipts) != 4 or not all(record.get("verified") for record in receipts):
        raise RuntimeError("Shared runtime, text encoder and VAE are not fully verified")
    for item in metadata["files"]:
        path = (ENGINE / item["relative"]).resolve()
        if not path.is_relative_to(ENGINE.resolve()) or not path.is_file() or path.stat().st_size != item["size"]:
            raise RuntimeError(f"Shared baseline component is missing or changed: {item['name']}")
    return {"id": "Q4_K", "label": "Q4_K（旧基线）", "bytes": actual["size"], "status": "ready",
            "verified": True, "gpuVerified": True, "legacy": True,
            "sourceConfig": workspace_path(BASELINE), "path": workspace_path(model_path(actual)),
            "pin": pin(actual), "verification": metadata.get("gpuVerification")}


def variant_record(item):
    return {"id": item["id"], "label": item["label"], "bytes": item["size"], "status": "installing",
            "verified": False, "gpuVerified": False, "legacy": False,
            "sourceConfig": workspace_path(SOURCE), "path": workspace_path(model_path(item)), "pin": pin(item)}


def metadata_sources():
    repositories = []
    for repo, revision in dict.fromkeys((item["repo"], item["revision"]) for item in PINNED):
        url = f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"
        metadata = BASE.read_json_url(url)
        if metadata.get("private") or metadata.get("gated") or metadata.get("disabled"):
            raise ValueError("Only public non-gated original-model quantizations are allowed")
        if metadata.get("id") != repo or metadata.get("sha") != revision:
            raise ValueError(f"Author/revision mismatch: {repo}")
        card = metadata.get("cardData") or {}
        original = card.get("base_model")
        if original not in ("Qwen/Qwen-Image-2.1", ["Qwen/Qwen-Image-2.1"]):
            raise ValueError(f"The author's model card does not identify the original Qwen Image 2.1: {repo}")
        files = []
        for item in (candidate for candidate in PINNED if candidate["repo"] == repo):
            actual = next((entry for entry in metadata["siblings"] if entry["rfilename"] == item["name"]), None)
            if not actual or actual.get("size") != item["size"] or actual.get("lfs", {}).get("sha256") != item["sha256"]:
                raise ValueError(f"Pinned public LFS metadata mismatch: {item['name']}")
            files.append(actual)
        repositories.append({"repo": repo, "revision": revision, "metadataUrl": url, "private": False,
                             "gated": False, "baseModel": original, "modelCard": card, "files": files})
    return repositories


def preserve_gpu_verification(record, existing):
    if (record["verified"] and existing.get("verified") and existing.get("gpuVerified")
            and record["pin"] == existing.get("pin") and record["path"] == existing.get("path")):
        record["gpuVerified"] = True
        record["status"] = "ready"
        if existing.get("verification"):
            record["verification"] = existing["verification"]


def publish(metadata, register=False):
    metadata["updatedAt"] = BASE.timestamp()
    if not register:
        BASE.save_json(SOURCE, metadata)
        return
    # Re-read immediately before the small merge; preserve all unrelated engine fields.
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    existing = {item["id"]: item for item in manifest.get("variants", [])}
    for variant in metadata["variants"]:
        preserve_gpu_verification(variant, existing.get(variant["id"], {}))
    BASE.save_json(SOURCE, metadata)
    manifest["variants"] = [dict(item) for item in metadata["variants"]]
    manifest["defaultWeightVariant"] = DEFAULT_VARIANT
    BASE.save_json(MANIFEST, manifest)


def sample_progress(metadata):
    records = {item["id"]: item for item in metadata["variants"]}
    for item in PINNED:
        target = model_path(item)
        partial = target.with_name(target.name + ".part")
        record = records[item["id"]]
        path = target if target.is_file() else partial
        downloaded = path.stat().st_size if path.is_file() else 0
        record["downloadedBytes"] = downloaded
        record["downloadPercent"] = round(downloaded * 100 / item["size"], 2)


def download_variant(item, verify_only):
    urls = [f"https://{host}/{item['repo']}/resolve/{item['revision']}/{item['name']}"
            for host in ("huggingface.co", "hf-mirror.com")]
    return BASE.download(item, verify_only, source_urls=urls, use_environment_proxy=True)


def install(metadata, verify_only, prior_variants=None):
    records = {item["id"]: item for item in metadata["variants"]}
    prior = {item["id"]: item for item in (prior_variants or [])}
    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as pool:
        pending = {pool.submit(download_variant, item, verify_only): item for item in PINNED}
        last_update = 0
        while pending:
            completed, _ = concurrent.futures.wait(pending, timeout=5, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in completed:
                item = pending.pop(future)
                try:
                    receipt = future.result()
                except Exception as error:
                    receipt = {"role": "diffusion", "file": item["name"], "size": item["size"],
                               "verified": False, "error": str(error)}
                receipt["variantId"] = item["id"]
                metadata["receipts"] = [entry for entry in metadata["receipts"] if entry.get("variantId") != item["id"]]
                metadata["receipts"].append(receipt)
                record = records[item["id"]]
                record.update(verified=receipt["verified"], status="installed" if receipt["verified"] else "installing")
                preserve_gpu_verification(record, prior.get(item["id"], {}))
                if receipt.get("error"):
                    record["error"] = receipt["error"]
                else:
                    record.pop("error", None)
                sample_progress(metadata)
                publish(metadata, register=True)
                BASE.emit({"stage": "variant-registered", "id": item["id"], "status": record["status"],
                           "verified": record["verified"], "gpuVerified": record["gpuVerified"]})
            if time.monotonic() - last_update >= 10:
                sample_progress(metadata)
                publish(metadata)
                last_update = time.monotonic()


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--use-saved-metadata", action="store_true")
    parser.add_argument("--log-stem", default="logs/qwen-image-variants")
    args = parser.parse_args()
    log_stem = (ROOT / args.log_stem).resolve()
    if not log_stem.is_relative_to((ROOT / "logs").resolve()):
        raise ValueError("Installer logs must remain in this workspace's logs directory")
    lock_path = ROOT / "logs/image-variants.install.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        import msvcrt
        if lock.seek(0, os.SEEK_END) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another variant installer is already running") from error
        legacy = baseline_variant()
        command_line = subprocess.list2cmdline([sys.executable, *sys.argv])
        if os.name == "nt":
            ctypes.windll.kernel32.GetCommandLineW.restype = ctypes.c_wchar_p
            command_line = ctypes.windll.kernel32.GetCommandLineW()
        metadata = {"version": VERSION, "author": "Unclecow", "engineId": "qwen-image-21",
                    "defaultWeightVariant": DEFAULT_VARIANT, "baselineSourceConfig": workspace_path(BASELINE),
                    "sharedComponents": "Existing pinned runtime, Qwen3-VL text encoder and dedicated VAE",
                    "files": PINNED, "variants": [variant_record(item) for item in PINNED] + [legacy],
                    "status": "installing", "parallelDownloads": PARALLEL_DOWNLOADS, "receipts": [], "repositories": [],
                    "process": {"pid": os.getpid(), "parentPid": os.getppid(), "interpreter": sys.executable,
                                "commandLine": command_line,
                                "startedAt": BASE.timestamp(), "status": "running",
                                "stdoutLog": workspace_path(log_stem.with_suffix(".log")),
                                "stderrLog": workspace_path(log_stem.with_suffix(".error.log"))},
                    "gpuInferenceExecuted": False}
        previous = json.loads(SOURCE.read_text(encoding="utf-8")) if SOURCE.is_file() else None
        prior_variants = json.loads(MANIFEST.read_text(encoding="utf-8")).get("variants", [])
        if previous:
            if previous.get("files") != PINNED:
                raise ValueError("Existing variant pins differ; configuration retained")
            old_records = {item["id"]: item for item in previous.get("variants", [])}
            for item in metadata["variants"]:
                preserve_gpu_verification(item, old_records.get(item["id"], {}))
        publish(metadata, register=True)
        try:
            if args.use_saved_metadata or args.verify_only:
                if not previous or not previous.get("repositories"):
                    raise RuntimeError("No previously validated public metadata is available")
                metadata["repositories"] = previous["repositories"]
            else:
                metadata["repositories"] = metadata_sources()
            metadata["metadataCheckedAt"] = BASE.timestamp()
            publish(metadata)
            BASE.emit({"stage": "start", "version": VERSION, "pid": os.getpid(), "parallel": PARALLEL_DOWNLOADS,
                       "files": len(PINNED), "bytes": sum(item["size"] for item in PINNED)})
            install(metadata, args.verify_only, prior_variants)
            complete = all(item["verified"] for item in metadata["variants"] if not item.get("legacy"))
            metadata["status"] = "installed" if complete else "installing"
            metadata["process"].update(status="completed" if complete else "failed", finishedAt=BASE.timestamp())
            sample_progress(metadata)
            publish(metadata, register=True)
            BASE.emit({"stage": "complete", "installed": complete, "gpuInferenceExecuted": False})
            raise SystemExit(0 if complete else 1)
        except Exception as error:
            metadata["error"] = str(error)
            metadata["process"].update(status="failed", finishedAt=BASE.timestamp())
            publish(metadata, register=True)
            raise


if __name__ == "__main__":
    main()
