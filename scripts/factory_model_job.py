"""Run one model postprocessing job and verify its receipt. v0.4.0 / Unclecow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess

from factory_glb import animate_glb, inspect_glb, inspect_texture_glb
from factory_postprocess import checked_model, checked_reference_set, file_sha256
from factory_runtime import powershell_command
from factory_capabilities import require_accepted_execution


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_job(root, engine_id, request_path, run_id):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-z0-9_-]+", engine_id) or not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{6,32}", run_id):
        raise ValueError("工具或运行标识无效")
    folder = root / "engines" / engine_id
    manifest = json.loads((folder / "engine.json").read_text(encoding="utf-8-sig"))
    kind = manifest.get("kind")
    if manifest.get("id") != engine_id or kind not in {"texture", "rig", "animation"} or manifest.get("status") not in {"ready", "installed"}:
        raise ValueError("模型后处理工具尚未就绪")
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    require_accepted_execution(root, manifest, run_id, request_path, request)
    if not isinstance(request, dict) or not re.fullmatch(r"[a-f0-9]{64}", request.get("sourceSha256", "")):
        raise ValueError("后处理请求缺少固定输入哈希")
    source = checked_model(root, request.get("modelUrl"), request["sourceSha256"], kind == "animation")
    if kind == "texture" and (source["summary"].get("skins") or source["summary"].get("animationCount")):
        raise ValueError("请先上色再绑骨，不会移除已有骨架")
    references = checked_reference_set(root, request.get("referenceImages"), require_sha=True) if kind == "texture" else []
    output = root / "outputs" / engine_id / run_id
    output.mkdir(parents=True, exist_ok=False)
    input_copy = output / "input.glb"
    shutil.copyfile(root / source["path"], input_copy)
    if file_sha256(input_copy) != source["sha256"]:
        raise ValueError("复制期间原模型发生变化，请重新选择")
    write_json(output / "request.json", request)
    receipt = {"factoryVersion": json.loads((root / "VERSION.json").read_text(encoding="utf-8-sig"))["version"],
               "runId": run_id, "engine": engine_id, "engineVersion": manifest["version"],
               "inputMode": "model", "pipelineStage": kind, "objectName": request.get("objectName"),
               "source": str(root / source["path"]), "sourceModel": source,
               "inputSha256": source["sha256"], "request": request, "status": "running",
               "startedAt": datetime.now(timezone.utc).isoformat()}
    write_json(output / "receipt.json", receipt)
    provider_output = output / "work" / "provider"
    try:
        views = {}
        copied_references = []
        for item in references:
            reference_path = root / item["path"]
            destination = output / "references" / (item["view"] + reference_path.suffix.lower())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reference_path, destination)
            if file_sha256(destination) != item["sha256"]:
                raise ValueError("复制期间参考图发生变化，请重新选择")
            views[item["view"]] = str(destination)
            copied_references.append({**item, "source": str(reference_path), "path": str(destination)})
        if references:
            receipt["inputs"] = copied_references
            receipt["viewCount"] = len(references)
            write_json(output / "reference-views.json", views)
            write_json(output / "receipt.json", receipt)
        if kind == "animation" and engine_id == "basic-motion":
            print("FACTORY_PHASE: animation", flush=True)
            animate_glb(input_copy, output / "animated.glb", request.get("motion", "joint-test"))
        else:
            entry = (folder / (manifest.get("entrypoint") or "run.ps1")).resolve()
            if not entry.is_relative_to(folder.resolve()) or not entry.is_file():
                raise ValueError("绑定入口不存在或超出工具目录")
            print("FACTORY_PHASE: " + {"rig": "rigging", "animation": "animation", "texture": "texturing"}[kind], flush=True)
            provider_output.parent.mkdir(parents=True, exist_ok=True)
            command = [powershell_command(root), "-NoLogo", "-NoProfile", "-File", str(entry),
                       "-InputModel", str(input_copy), "-OutputDirectory", str(provider_output)]
            if references:
                command.extend(["-ViewsJson", str(output / "reference-views.json")])
            with (output / "run.log").open("wb") as log:
                process = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
            if process.returncode:
                tail = (output / "run.log").read_text(encoding="utf-8", errors="replace")[-1600:]
                raise RuntimeError(f"模型后处理工具退出码 {process.returncode}\n{tail}")
        provider_result = provider_output / "result.json"
        if provider_result.is_file():
            result = json.loads(provider_result.read_text(encoding="utf-8"))
            primary = result.get("primaryOutput")
            if not isinstance(primary, str) or not primary:
                raise ValueError("绑定工具未声明成品 GLB")
            selected = (provider_output / primary).resolve()
            if not selected.is_relative_to(provider_output.resolve()) or selected.suffix.lower() != ".glb" or not selected.is_file():
                raise ValueError("绑定工具的成品路径无效")
            if kind == "texture":
                inspect_texture_glb(selected)
            else:
                inspect_glb(selected, require_skin=True, require_animation=kind == "animation")
            promoted = output / selected.name
            if promoted.exists():
                raise ValueError("绑定工具的成品名与工厂已有文件冲突")
            shutil.copyfile(selected, promoted)
            if file_sha256(selected) != file_sha256(promoted):
                raise ValueError("绑定成品复制校验失败")
            write_json(output / "result.json", {**result, "primaryOutput": promoted.name, "providerOutputDirectory": str(provider_output)})
            models = [promoted]
        else:
            models = [p for p in sorted(output.glob("*.glb")) if p.name != "input.glb" and p.stat().st_size]
        if not models:
            raise ValueError("工具未输出可复用的 GLB")
        summaries = {p.name: inspect_texture_glb(p) if kind == "texture" else inspect_glb(p, require_skin=True, require_animation=kind == "animation") for p in models}
        receipt.update(status="generated-unreviewed", modelChecks=summaries,
                       outputs=[{"path": str(p), "bytes": p.stat().st_size, "sha256": file_sha256(p)} for p in models])
        print(json.dumps({"event": "progress", "stage": "exporting", "current": 1, "total": 1}, ensure_ascii=False), flush=True)
    except Exception as error:
        receipt.update(status="failed", error=str(error))
        raise
    finally:
        receipt["finishedAt"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "receipt.json", receipt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run_job(args.root, args.engine, args.request, args.run_id)
