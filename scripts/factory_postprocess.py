"""GLB postprocessing contracts for the shared factory queue. v0.4.0 / Unclecow."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from PIL import Image

from factory_glb import inspect_glb, MOTIONS


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_model(root, url, expected_sha=None, require_skin=False):
    if not isinstance(url, str) or not url.startswith("/files/outputs/"):
        raise ValueError("请选择工厂已生成的 GLB 模型")
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment or parsed.netloc:
        raise ValueError("模型地址不能包含查询参数或外部地址")
    base = (Path(root) / "outputs").resolve()
    path = (base / unquote(parsed.path[len("/files/outputs/"):])).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() != ".glb" or not path.is_file():
        raise ValueError("模型不存在或超出产物目录")
    summary = inspect_glb(path, require_skin=require_skin)
    sha = file_sha256(path)
    if expected_sha is not None and sha != expected_sha:
        raise ValueError("原模型已改变，请重新选择；不会用不同输入重试")
    return {"path": path.relative_to(Path(root).resolve()).as_posix(),
            "url": "/files/" + quote(path.relative_to(Path(root).resolve()).as_posix()),
            "sha256": sha, "bytes": path.stat().st_size, "summary": summary}


def checked_reference_image(root, url, expected_sha=None):
    if not isinstance(url, str) or not url.startswith(("/files/inputs/", "/files/outputs/")):
        raise ValueError("参考图需为工厂上传图片或原模型的输入图片")
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment or parsed.netloc:
        raise ValueError("参考图地址不能包含查询参数或外部地址")
    root = Path(root).resolve()
    path = (root / unquote(parsed.path[len("/files/"):])).resolve()
    if (not any(path.is_relative_to(root / folder) for folder in ("inputs", "outputs"))
            or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            or not 0 < path.stat().st_size <= 24 * 1024 * 1024):
        raise ValueError("参考图片不存在、超出工厂目录或超过24MB")
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
            raise ValueError("参考图片格式或尺寸无效")
        image.verify()
    sha = file_sha256(path)
    if expected_sha is not None and sha != expected_sha:
        raise ValueError("原参考图已改变，请重新选择；不会用不同图片重试")
    return {"path": path.relative_to(root).as_posix(), "url": "/files/" + quote(path.relative_to(root).as_posix()),
            "sha256": sha, "bytes": path.stat().st_size}


def checked_reference_set(root, records, require_sha=False):
    if not isinstance(records, list) or not 2 <= len(records) <= 4:
        raise ValueError("多图上色需2至4张参考图，并包含正面图")
    selected, seen_views, seen_images = [], set(), set()
    for record in records:
        if not isinstance(record, dict) or record.get("view") not in {"front", "left", "back", "right"}:
            raise ValueError("参考图方向无效")
        if require_sha and not re.fullmatch(r"[a-f0-9]{64}", str(record.get("sha256", ""))):
            raise ValueError("上色请求缺少固定参考图哈希")
        image = checked_reference_image(root, record.get("url"), record.get("sha256"))
        view = record["view"]
        if view in seen_views or image["sha256"] in seen_images:
            raise ValueError("参考图方向或图片内容不能重复")
        seen_views.add(view); seen_images.add(image["sha256"])
        selected.append({"view": view, **image})
    if "front" not in seen_views:
        raise ValueError("多图上色需要正面图")
    return sorted(selected, key=lambda record: ("front", "left", "back", "right").index(record["view"]))


def validate_request(factory, payload):
    from factory_capabilities import require_creation
    if not isinstance(payload, dict):
        raise ValueError("无效模型后处理请求")
    stage = payload.get("stage", "rig")
    require_creation({'stage': stage}, payload)
    if not isinstance(stage, str) or stage not in {"texture", "rig", "animation"}:
        raise ValueError("后处理阶段需为 texture、rig 或 animation")
    engine = next((e for e in factory.engines() if e["id"] == payload.get("engineId")), None)
    require_creation(engine)
    if not engine or engine["kind"] != stage or engine["status"] not in {"ready", "installed"}:
        raise ValueError("该阶段工具尚未安装就绪，请查看真实安装状态")
    model = checked_model(factory.root, payload.get("modelUrl"), require_skin=stage == "animation")
    if stage == "texture":
        if model["summary"].get("skins") or model["summary"].get("animationCount"):
            raise ValueError("请先对未绑骨模型上色，再绑定骨骼；不会移除已有骨架")
        provided = payload.get("images")
        if provided is None:
            references = factory.model_reference_images(model["url"])
        else:
            if not isinstance(provided, list):
                raise ValueError("参考图片需为列表")
            references = []
            for item in provided:
                if not isinstance(item, dict):
                    raise ValueError("参考图片记录无效")
                image = factory._image(item.get("imageId"))
                references.append({"view": item.get("view"), "url": image["url"]})
        references = checked_reference_set(factory.root, references)
        return engine, model, {"modelUrl": model["url"], "sourceSha256": model["sha256"],
                               "referenceImages": references, "objectName": str(payload.get("name") or "多图模型上色").strip()[:100]}
    motion = payload.get("motion", "joint-test")
    supported = {"standard-run"} if engine["id"] == "mia-motion" else set(MOTIONS)
    if stage == "animation" and motion not in supported:
        raise ValueError("未知动作")
    return engine, model, {"modelUrl": model["url"], "sourceSha256": model["sha256"],
                            "motion": motion if stage == "animation" else None,
                            "objectName": str(payload.get("name") or "模型后处理").strip()[:100]}
