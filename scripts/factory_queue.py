"""Persistent local image-to-3D queue. v0.5.0a / Unclecow."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import shutil
import subprocess
import threading
import time
import tempfile
import uuid
from urllib.parse import quote
import warnings
import zipfile

from PIL import Image, ImageOps
from factory_glb import inspect_glb, inspect_texture_glb, read_glb
from factory_postprocess import checked_model, checked_reference_set, validate_request
from factory_runtime import powershell_command
from factory_setup import FactorySetup
from factory_pipeline import PipelineController
from factory_deploy import WindowsOwnedJob, stop_owned_process
from factory_capabilities import creation_allowed, require_creation
from factory_identity import service_fingerprint

IMAGE_TYPES = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
TERMINAL = {"generated-unreviewed", "failed", "cancelled", "interrupted"}
VIEW_NAMES = {"left", "back", "right"}


# Capture once during import: changing source files cannot make an old process
# claim it has loaded the updated implementation.
SERVICE_BUILD = service_fingerprint()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_child(base: Path, value: str) -> Path:
    path = (base / value).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("文件路径超出工厂目录")
    return path


def log_progress(log: str) -> tuple[str, int | None]:
    """Only report percentages supplied by a real stage; never estimate time."""
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", log)
    for line in reversed(clean.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("event") == "progress":
            labels = {"preparing": "预处理视图", "loading": "加载模型", "sampling": "几何采样", "exporting": "导出模型"}
            current, total = event.get("current"), event.get("total")
            progress = round(current / total * 100) if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total > 0 else None
            label = labels.get(event.get("stage"), str(event.get("stage", "运行中")))
            return label, min(100, max(0, progress)) if progress is not None else None
    stages = list(re.finditer(r"\[(\d+)/(\d+)\]\s*([^\r\n]+)", clean))
    if stages:
        stage = stages[-1]
        tail = clean[stage.end():]
        fraction = list(re.finditer(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", tail))
        percent = None
        if fraction and int(fraction[-1][2]):
            percent = min(100, round(int(fraction[-1][1]) / int(fraction[-1][2]) * 100))
        return f"阶段 {stage[1]}/{stage[2]} · {stage[3].strip()[:120]}", percent
    for marker, label in reversed([
        ("Loading", "加载模型"), ("Diffusion Sampling", "几何采样"),
        ("Volume Decoding", "网格解码"), ("采样", "几何采样"),
        ("FACTORY_PHASE: export", "导出模型"),
    ]):
        position = clean.rfind(marker)
        if position >= 0:
            tail = clean[position:]
            fractions = list(re.finditer(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", tail))
            percent = None
            if fractions and int(fractions[-1][2]):
                percent = min(100, round(int(fractions[-1][1]) / int(fractions[-1][2]) * 100))
            return label, percent
    if "FACTORY_PHASE: image-generation" in clean:
        fractions = list(re.finditer(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", clean))
        if fractions and int(fractions[-1][2]):
            return "图片采样", min(100, round(int(fractions[-1][1]) / int(fractions[-1][2]) * 100))
        return "本地生图 · 加载或采样", None
    return "运行中 · 等待引擎进度", None


class Factory:
    def __init__(self, root: Path, start_worker: bool = True):
        self.root = Path(root).resolve()
        self._version = json.loads((self.root / "VERSION.json").read_text(encoding="utf-8-sig"))["version"]
        self.service_build = SERVICE_BUILD
        self.storage = self.root / "logs/visual-factory"
        self.storage.mkdir(parents=True, exist_ok=True)
        self.state_file = self.storage / "state.json"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.history_cache = []
        self.history_read_at = 0.0
        self.model_cache = {}
        self.data = {"images": {}, "batches": []}
        if self.state_file.exists():
            try:
                self.data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if not isinstance(self.data.get("images"), dict) or not isinstance(self.data.get("batches"), list):
                    raise ValueError("Invalid persisted queue")
            except (ValueError, OSError) as error:
                # Preserve evidence, fail visibly instead of silently losing the queue.
                raise RuntimeError(f"本地队列无法读取，原文件已保留：{error}") from error
        self.setup = FactorySetup(self.root, start_worker=False)
        self.pipeline = PipelineController(self)
        for job in self._jobs():
            if job["status"] == "running":
                run_root = self.run_root(job)
                receipt = self._receipt(run_root)
                expected_kind = job['kind'] if job.get('kind') in {'image','video'} else 'model'
                if receipt.get("status") == "generated-unreviewed" and any(asset["kind"] == expected_kind for asset in self.assets(run_root)):
                    job.update(status="generated-unreviewed", phase="生成完成", finishedAt=receipt.get("finishedAt", now()))
                else:
                    job.update(status="interrupted", phase="运行被中断", error="上次运行未正常结束，请确认后重试", finishedAt=now())
        variant_engines = {item["id"] for item in self.engines() if "weightVariants" in item}
        for job in self._jobs():
            if job.get("kind") == "image" and job["status"] != "deleted" and job["engine"] in variant_engines:
                job["request"].setdefault("weightVariant", "Q4_K")
        self._save()
        if start_worker:
            self.setup.start_worker()
            self.worker = threading.Thread(target=self._work, name="factory-gpu-queue", daemon=True)
            self.worker.start()

    def _save(self):
        write_json(self.state_file, self.data)

    def _jobs(self):
        return [job for batch in self.data["batches"] for job in batch["jobs"]]

    def _assert_no_installation(self):
        if self.stopping.is_set():
            raise ValueError('应用正在关闭，任务未提交；请重新打开后继续。')
        with self.setup.lock:
            if any(job.get('status') in {'queued','running'} for job in self.setup.jobs):
                raise ValueError('模型正在安装，请等待安装完成或取消后再开始生成。')

    def install_model(self, payload, retry_id=None):
        with self.lock:
            if self.stopping.is_set():
                raise ValueError('应用正在关闭，安装未提交；请重新打开后继续。')
            if any(job.get('status') in {'queued','running'} for job in self._jobs()) or any(p['status'] not in {'completed','failed','cancelled','interrupted'} for p in self.pipeline.records):
                raise ValueError('请等待当前生成任务结束或取消后再安装模型，避免修改正在使用的运行环境。')
            return self.setup.retry(retry_id) if retry_id else self.setup.install(payload)

    def create_pipeline(self,payload):
        return self.pipeline.create(payload)

    def cancel_pipeline(self,identifier):
        return self.pipeline.cancel(identifier)

    def retry_pipeline(self,identifier):
        return self.pipeline.retry(identifier)

    def upload_model(self, data, filename):
        if not isinstance(data, bytes) or not 20 <= len(data) <= 512 * 1024 * 1024:
            raise ValueError('请选择非空 GLB 模型，最大支持 512 MB。')
        if not isinstance(filename,str) or Path(filename).suffix.lower() != '.glb':
            raise ValueError('目前支持导入 GLB 格式；请从建模软件导出自包含的 GLB 文件。')
        with tempfile.TemporaryDirectory(prefix='model-import-',dir=self.storage) as temporary:
            staged=Path(temporary)/'model.glb'
            staged.write_bytes(data)
            document,_binary=read_glb(staged)
            for item in document.get('buffers',[])+document.get('images',[]):
                if 'uri' in item and not str(item['uri']).startswith('data:'):
                    raise ValueError('模型包含外部贴图或数据文件，请重新导出为贴图内嵌的 GLB。')
            try:
                summary=inspect_glb(staged)
                accessors=document.get('accessors',[])
                vertex_count=0
                for mesh in document.get('meshes',[]):
                    for primitive in mesh.get('primitives',[]):
                        index=primitive.get('attributes',{}).get('POSITION')
                        if type(index) is not int or not 0 <= index < len(accessors):
                            raise ValueError('网格缺少有效的位置数据。')
                        accessor=accessors[index]
                        count=accessor.get('count')
                        if type(count) is not int or count <= 0:
                            raise ValueError('网格顶点数量无效。')
                        if accessor.get('type')!='VEC3' or accessor.get('componentType') not in {5120,5121,5122,5123,5126} or 'sparse' in accessor:
                            raise ValueError('网格位置数据格式不支持，请重新导出普通 GLB。')
                        view_index=accessor.get('bufferView')
                        views=document.get('bufferViews',[])
                        if type(view_index) is not int or not 0 <= view_index < len(views):
                            raise ValueError('网格位置数据缺少有效缓冲区。')
                        view=views[view_index]
                        if view.get('buffer',0)!=0:
                            raise ValueError('网格位置数据必须内嵌在 GLB 中。')
                        width={5120:1,5121:1,5122:2,5123:2,5126:4}[accessor['componentType']]
                        offset=view.get('byteOffset',0)+accessor.get('byteOffset',0)
                        stride=view.get('byteStride',3*width)
                        view_length=view.get('byteLength')
                        if type(offset) is not int or type(stride) is not int or type(view_length) is not int or offset < view.get('byteOffset',0) or stride < 3*width:
                            raise ValueError('网格位置数据偏移或步长无效。')
                        end=offset+(count-1)*stride+3*width
                        buffers=document.get('buffers',[])
                        if not buffers or end > len(_binary) or end > view.get('byteOffset',0)+view_length or end > buffers[0].get('byteLength',0):
                            raise ValueError('网格位置数据超出缓冲区，请重新导出完整 GLB。')
                        if accessor['componentType']==5126:
                            unpack=struct.Struct('<3f').unpack_from
                            if any(not all(math.isfinite(value) for value in unpack(_binary,offset+row*stride)) for row in range(count)):
                                raise ValueError('网格位置包含无效数值，请在建模软件中修复。')
                        vertex_count+=count
            except (KeyError,IndexError,TypeError) as error:
                raise ValueError('GLB 网格结构无效，请在建模软件中检查并重新导出。') from error
            if vertex_count <= 0:
                raise ValueError('模型没有可显示的网格顶点。')
            run_id=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:12]
            folder=self.root/'outputs/imported-model'/run_id
            folder.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(staged.parent),str(folder))
            checksum=hashlib.sha256(data).hexdigest()
            name=Path(filename).stem[:100]
            write_json(folder/'receipt.json',{'factoryVersion':self.version(),'runId':run_id,'engine':'imported-model',
                       'engineVersion':self.version(),'objectName':name,'status':'generated-unreviewed',
                       'inputMode':'import','origin':'local-import','startedAt':now(),'finishedAt':now(),
                       'inputSha256':checksum,'gpuInferenceExecuted':False,'outputs':[{'path':str(folder/'model.glb'),'sha256':checksum,'bytes':len(data)}]})
        self.history_read_at=0
        return {'name':name,'url':'/files/'+(folder/'model.glb').relative_to(self.root).as_posix(),
                'sha256':checksum,'bytes':len(data),'modelInfo':summary,'origin':'local-import'}

    def engines(self):
        items = []
        for manifest in sorted((self.root / "engines").glob("*/engine.json")):
            item = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if not re.fullmatch(r"[a-z0-9_-]+", item.get("id", "")) or manifest.parent.name != item["id"]:
                raise ValueError(f"无效工具注册：{manifest.parent.name}")
            entry = safe_child(manifest.parent, item.get("entrypoint") or "run.ps1")
            capabilities = item.get("capabilities") or {}
            public = {key: item.get(key) for key in ("id", "title", "version", "notes")} | {
                "status": item.get("status") if entry.is_file() else "blocked",
                "multiView": bool(capabilities.get("multiView")),
                "kind": item.get("kind", "model"),
                "views": capabilities.get("views", []),
                "appearance": item.get("appearance") or ("texture" if item["id"] in {"trellis", "pixal3d"} else "vertex" if item["id"] == "triposr" else "geometry"),
                'capabilities': copy.deepcopy(capabilities), 'defaults':copy.deepcopy(item.get('defaults') or {}),
                'mediaAdapter':item.get('mediaAdapter') is True, 'runtime':copy.deepcopy(item.get('runtime') or {}),
            }
            if "variants" in item:
                public["weightVariants"] = self._weight_variants(item, manifest.parent)
                public["defaultWeightVariant"] = "Q4_K_M"
            readiness = self.setup.readiness(item["id"])
            if readiness is not None:
                public.update(readiness)
            public['creationEnabled'] = creation_allowed(public)
            items.append(public)
        return items

    def creation_engines(self):
        """Available creation choices; engines() also retains historical registrations."""
        return [engine for engine in self.engines() if creation_allowed(engine)]

    def _weight_variants(self, manifest, engine_folder):
        variants = manifest["variants"]
        if not isinstance(variants, list):
            raise ValueError("模型档位注册需为列表")
        public, seen = [], set()
        for variant in variants:
            if not isinstance(variant, dict) or not isinstance(variant.get("id"), str) or not variant["id"] or variant["id"] in seen:
                raise ValueError("模型档位注册无效或重复")
            seen.add(variant["id"])
            status = variant.get("status", "unavailable")
            verified = variant.get("verified") is True
            if variant.get("path"):
                try:
                    weight = safe_child(self.root, variant["path"])
                    allowed = (engine_folder / "runtime/models").resolve()
                    file_valid = weight.is_relative_to(allowed) and weight.is_file() and weight.stat().st_size == variant.get("bytes")
                except (OSError, TypeError, ValueError):
                    file_valid = False
                verified = verified and file_valid
            if status in {"installed", "ready"} and not verified:
                status = "blocked"
            public.append({"id": variant["id"], "label": variant.get("label") or variant["id"],
                           "bytes": variant.get("bytes"), "status": status, "verified": verified,
                           "gpuVerified": variant.get("gpuVerified") is True, "legacy": variant.get("legacy") is True})
        return public

    @staticmethod
    def _request_weight_variant(engine, payload):
        if "weightVariants" not in engine:
            if "weightVariant" in payload:
                raise ValueError("此生图工具没有可选权重档位")
            return None
        legacy=next((item for item in engine['weightVariants'] if item['id']=='Q4_K' and item['status'] in {'installed','ready'} and item.get('verified')),None)
        selected = payload.get("weightVariant") if "weightVariant" in payload else 'Q4_K' if legacy else engine.get('defaultWeightVariant','Q4_K_M')
        if not isinstance(selected, str):
            raise ValueError("请选择准确的权重档位")
        variant = next((item for item in engine["weightVariants"] if item["id"] == selected), None)
        if not variant or variant["status"] not in {"installed", "ready"} or not variant["verified"]:
            raise ValueError(f"权重档位尚未安装或校验完成：{selected}")
        return selected

    def upload_image(self, data: bytes, filename: str, *, persist=True):
        if not data or len(data) > 24 * 1024 * 1024:
            raise ValueError("每张图片需在 24 MB 以内")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    kind = source.format
                    if kind not in IMAGE_TYPES or source.width * source.height > 40_000_000:
                        raise ValueError("请导入 PNG、JPEG 或 WebP 图片（不超过 4000 万像素）")
                    source.verify()
                with Image.open(io.BytesIO(data)) as source:
                    image = ImageOps.exif_transpose(source).convert("RGBA")
                    alpha_low, alpha_high = image.getchannel("A").getextrema()
                    if alpha_high == 0:
                        raise ValueError("图片完全透明，无法生成模型")
                    # Some image generators emit 250..255 alpha without a real
                    # cutout. Normalize only the imported copy so 3D engines
                    # can still remove its background; preserve genuine alpha.
                    if alpha_low >= 250:
                        image.putalpha(255)
                    width, height = image.size
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("图片无法解码或尺寸过大，请换一张图片") from error
        image_id = uuid.uuid4().hex
        relative = f"inputs/uploads/{image_id}.png"
        path = safe_child(self.root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
        clean_name = str(filename).replace("\\", "/").split("/")[-1][:160] or "图片"
        record = {"id": image_id, "name": clean_name, "path": relative, "url": "/files/" + quote(relative),
                  "width": width, "height": height, "bytes": path.stat().st_size,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        with self.lock:
            self.data["images"][image_id] = record
            if persist:self._save()
        return self._public_image(record)

    @staticmethod
    def _public_image(record):
        return {key: value for key, value in record.items() if key != "path"}

    def samples(self):
        images = []
        for path in sorted((self.root / "inputs").glob("*")):
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                key = "sample-" + hashlib.sha256(path.name.encode()).hexdigest()[:16]
                with self.lock:
                    if key not in self.data["images"]:
                        relative = path.relative_to(self.root).as_posix()
                        self.data["images"][key] = {"id": key, "name": path.name, "path": relative,
                                                      "url": "/files/" + quote(relative), "bytes": path.stat().st_size}
                        self._save()
                    images.append(self._public_image(self.data["images"][key]))
        return {"images": images}

    def _image(self, image_id):
        record = self.data["images"].get(image_id)
        if not record or not safe_child(self.root, record["path"]).is_file():
            raise ValueError("导入图片不存在，请重新导入")
        return record

    def _new_job(self, batch_id, obj, engine, retry_of=None):
        main_id = obj["mainImageId"]
        view_images = {"front": main_id}
        if engine["multiView"]:
            view_images.update({image["view"]: image["imageId"] for image in obj["auxiliaryImages"]})
        return {"id": uuid.uuid4().hex, "batchId": batch_id, "engine": engine["id"], "engineId": engine["id"],
                "appearance": engine.get('appearance'),
                "objectId": obj.get("id") or obj.get("objectId"), "objectName": obj["name"], "mainImageId": main_id, "auxiliaryImages": copy.deepcopy(obj["auxiliaryImages"]),
                "viewImages": view_images, "inputMode": "multi-view" if engine["multiView"] else "single",
                "status": "queued", "phase": "等待生产", "progress": None, "createdAt": now(),
                "runId": datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:12],
                "retryOf": retry_of, "cancelRequested": False, "logTail": ""}

    def create_batch(self, payload, *, persist=True):
        self._assert_no_installation()
        if not isinstance(payload, dict):
            raise ValueError("无效生产请求")
        objects, selected = payload.get("objects"), payload.get("engineIds")
        if not isinstance(objects, list) or not 1 <= len(objects) <= 20:
            raise ValueError("请添加 1 至 20 个物体")
        if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
            raise ValueError("请至少勾选一个工具，工具不能重复")
        registered = {item["id"]: item for item in self.engines()}
        engines = []
        for key in selected:
            engine = registered.get(key)
            require_creation(engine)
            if not engine or engine["status"] not in {"ready", "installed"} or engine["kind"] != "model":
                raise ValueError(f"工具当前不可运行：{key}")
            engines.append(engine)
        with self.lock:
            validated = []
            for index, obj in enumerate(objects):
                if not isinstance(obj, dict):
                    raise ValueError("无效物体输入")
                self._image(obj.get("mainImageId"))
                auxiliary = obj.get("auxiliaryImages", [])
                if not isinstance(auxiliary, list) or len(auxiliary) > 3:
                    raise ValueError("每个物体最多添加左面、背面、右面三张辅助图")
                directions, seen_images = set(), {obj["mainImageId"]}
                for view in auxiliary:
                    if not isinstance(view, dict) or view.get("view") not in VIEW_NAMES:
                        raise ValueError("辅助图方向需为左面、背面或右面")
                    if view["view"] in directions or view.get("imageId") in seen_images:
                        raise ValueError("辅助图方向和图片不能重复")
                    self._image(view.get("imageId"))
                    directions.add(view["view"])
                    seen_images.add(view["imageId"])
                for engine in engines:
                    if engine["multiView"] and not directions.issubset(set(engine["views"])):
                        raise ValueError(f"{engine['title']} 不支持所选辅助图方向")
                validated.append({"id": uuid.uuid4().hex, "name": str(obj.get("name") or f"物体 {index + 1}").strip()[:100],
                                  "mainImageId": obj["mainImageId"], "auxiliaryImages": copy.deepcopy(auxiliary)})
            batch_id = uuid.uuid4().hex
            batch = {"id": batch_id, "name": str(payload.get("name") or "图片生产").strip()[:100],
                     "createdAt": now(), "factoryVersion": self.version(), "objects": validated,
                     "engineIds": selected[:], "jobs": [self._new_job(batch_id, obj, engine) for obj in validated for engine in engines]}
            self._assert_no_installation()
            self.data["batches"].append(batch)
            if persist:self._save()
            result = copy.deepcopy(batch)
        if persist:self.wake.set()
        return result

    def create_image_job(self, payload, *, persist=True):
        if isinstance(payload,dict):
            selected=next((item for item in self.engines() if item['id']==payload.get('engineId')),None)
            require_creation(selected, payload)
            if selected and selected.get('kind') != 'image':
                raise ValueError('生图入口只接受图片模型')
            if selected and selected.get('mediaAdapter'):
                request={**payload,'mode':'txt2img'}
                if 'weightVariants' not in selected and request.get('weightVariant') in ('',None):request.pop('weightVariant',None)
                if request.get('seed') is None:request['seed']=secrets.randbelow(2**31)
                return self.create_media_job(request,persist=persist)
        self._assert_no_installation()
        if not isinstance(payload, dict):
            raise ValueError("无效生图请求")
        engine = next((item for item in self.engines() if item["id"] == payload.get("engineId")), None)
        if not engine or engine["kind"] != "image" or engine["status"] not in {"ready", "installed"}:
            raise ValueError("生图模型当前不可运行，请等待安装完成")
        weight_variant = self._request_weight_variant(engine, payload)
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 3000 or "\0" in prompt:
            raise ValueError("请输入 1 至 3000 字的图片描述")
        width, height, steps, seed = (payload.get(key, default) for key, default in [("width", 512), ("height", 512), ("steps", 20), ("seed", None)])
        if type(width) is not int or type(height) is not int or width not in {512, 768, 1024} or height not in {512, 768, 1024}:
            raise ValueError("图片宽高需为 512、768 或 1024")
        if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 100:
            raise ValueError("步数需为 1 至 100 的整数")
        if seed is None:
            seed = secrets.randbelow(2**31)
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**31:
            raise ValueError("随机种子需为 0 至 2147483647 的整数")
        negative = payload.get("negativePrompt", "")
        if not isinstance(negative, str) or len(negative) > 2000 or "\0" in negative:
            raise ValueError("反向描述过长")
        with self.lock:
            batch_id = uuid.uuid4().hex
            obj = {"id": uuid.uuid4().hex, "name": prompt.strip()[:60], "mainImageId": None, "auxiliaryImages": []}
            job = self._new_job(batch_id, obj, engine)
            job.update(kind="image", inputMode="text", viewImages={}, request={"prompt": prompt.strip(), "width": width,
                       "height": height, "steps": steps, "seed": seed, "negativePrompt": negative})
            if weight_variant is not None:
                job["request"]["weightVariant"] = weight_variant
            batch = {"id": batch_id, "name": "本地生图", "kind": "image", "createdAt": now(),
                     "factoryVersion": self.version(), "objects": [], "engineIds": [engine["id"]], "jobs": [job]}
            self._assert_no_installation()
            self.data["batches"].append(batch)
            if persist:self._save()
            result = copy.deepcopy(job)
        if persist:self.wake.set()
        return result

    def create_media_job(self,payload,*,persist=True):
        from factory_media import create_job
        return create_job(self,payload,persist)

    def upload_media_input(self,data,name):
        from factory_media import upload_input
        return upload_input(self,data,name)

    def import_output_image(self, url, *, persist=True):
        if not isinstance(url, str) or not url.startswith("/files/outputs/"):
            raise ValueError("请选择本地已生成的图片")
        from urllib.parse import unquote
        relative = unquote(url[len("/files/outputs/"):])
        path = safe_child(self.root / "outputs", relative)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not path.is_file():
            raise ValueError("该产物不是可导入的图片")
        if path.stat().st_size > 24 * 1024 * 1024:
            raise ValueError("生成图片超过 24 MB")
        return self.upload_image(path.read_bytes(), path.name,persist=persist)

    def create_model_job(self, payload):
        self._assert_no_installation()
        engine, source, request = validate_request(self, payload)
        with self.lock:
            batch_id = uuid.uuid4().hex
            obj = {"id": uuid.uuid4().hex, "name": request["objectName"], "mainImageId": None, "auxiliaryImages": []}
            job = self._new_job(batch_id, obj, engine)
            job.update(kind=engine["kind"], inputMode="model", viewImages={}, request=request, sourceModel=source)
            batch = {"id": batch_id, "name": {"rig": "绑定骨骼", "animation": "制作动作", "texture": "模型上色"}[engine["kind"]],
                     "kind": engine["kind"], "createdAt": now(), "factoryVersion": self.version(),
                     "objects": [obj], "engineIds": [engine["id"]], "jobs": [job]}
            self._assert_no_installation()
            self.data["batches"].append(batch)
            self._save()
            result = copy.deepcopy(job)
        self.wake.set()
        return result

    def model_reference_images(self, model_url):
        """Follow actual receipts to the original views; never invent missing images."""
        seen = set()
        current = model_url
        for _ in range(12):
            if current in seen:
                raise ValueError("模型来源链存在循环")
            seen.add(current)
            source = checked_model(self.root, current)
            folder = (self.root / source["path"]).parent
            while folder.is_relative_to(self.root / "outputs") and not (folder / "receipt.json").is_file():
                folder = folder.parent
            if not folder.is_relative_to(self.root / "outputs"):
                return []
            receipt = self._receipt(folder)
            records = self._receipt_inputs(folder, receipt)
            if records:
                raw = receipt.get("inputs", [])
                if isinstance(raw, dict):
                    raw = [{"view": view, **item} for view, item in raw.items() if isinstance(item, dict)]
                hashes = {item.get("view"): item.get("sha256") for item in raw if isinstance(item, dict)} if isinstance(raw, list) else {}
                return [{**record, **({"sha256": hashes[record["view"]]} if hashes.get(record["view"]) else {})} for record in records]
            current = (receipt.get("sourceModel") or {}).get("url") or (receipt.get("request") or {}).get("modelUrl")
            if not current:
                return []
        raise ValueError("模型来源链过长，请手动选择参考图")

    def version(self):
        return self._version

    def run_root(self, job):
        return safe_child(self.root / "outputs", f"{job['engine']}/{job['runId']}")

    def _find_job(self, job_id):
        for job in self._jobs():
            if job["id"] == job_id:
                return job
        raise ValueError("任务不存在")

    def cancel_job(self, job_id):
        with self.lock:
            job = self._find_job(job_id)
            if job["status"] == "queued":
                job.update(status="cancelled", phase="已取消", finishedAt=now())
            elif job["status"] == "running":
                job["cancelRequested"] = True
                job["phase"] = "正在停止引擎"
            self._save()
            return copy.deepcopy(job)

    def cancel_batch(self, batch_id):
        with self.lock:
            batch = self.get_batch(batch_id)
            for job in batch["jobs"]:
                self.cancel_job(job["id"])
            return copy.deepcopy(batch)

    def get_batch(self, batch_id):
        for batch in self.data["batches"]:
            if batch["id"] == batch_id:
                return batch
        raise ValueError("批次不存在")

    def retry_job(self, job_id):
        with self.lock:
            self._assert_no_installation()
            original = self._find_job(job_id)
            require_creation(original, original.get('request'))
            if original["status"] not in {"failed", "cancelled", "interrupted"}:
                raise ValueError("只有失败、取消或中断的任务可以重试")
            if any(job.get("retryOf") == job_id and job["status"] in {"queued", "running"} for job in self._jobs()):
                raise ValueError("此任务已在重试队列中")
            if original.get('mediaJob'):
                from factory_media import create_job, INPUT_FIELDS
                permitted=INPUT_FIELDS|{'mode','prompt','negativePrompt','width','height','steps','frames','fps','seed','strength','weightVariant','transparentBackground','cfgScale','controlStrength','ipAdapterStrength'}
                payload={k:copy.deepcopy(v) for k,v in original['request'].items() if k in permitted}
                payload['engineId']=original['engine']
                return create_job(self,payload,retry_of=original['id'])
            engines = {engine["id"]: engine for engine in self.engines()}
            engine = engines.get(original["engine"])
            require_creation(engine)
            if not engine or engine["status"] not in {"ready", "installed"}:
                raise ValueError("工具尚未就绪")
            if original.get("kind") not in {"image", "rig", "animation", "texture"}:
                self._image(original["mainImageId"])
                for image in original["auxiliaryImages"]:
                    self._image(image["imageId"])
            obj = {"id": original.get("objectId"), "name": original["objectName"], "mainImageId": original["mainImageId"], "auxiliaryImages": original["auxiliaryImages"]}
            request = copy.deepcopy(original.get("request", {}))
            if original.get("kind") == "image":
                weight_variant = self._request_weight_variant(engine, request)
                if weight_variant is not None:
                    request["weightVariant"] = weight_variant
            job = self._new_job(original["batchId"], obj, engine, original["id"])
            if original.get("kind") == "image":
                job.update(kind="image", inputMode="text", viewImages={}, request=request)
            elif original.get("kind") in {"rig", "animation", "texture"}:
                source = checked_model(self.root, request.get("modelUrl"), request.get("sourceSha256"), original["kind"] == "animation")
                if original["kind"] == "texture":
                    checked_reference_set(self.root, request.get("referenceImages"), require_sha=True)
                job.update(kind=original["kind"], inputMode="model", viewImages={}, request=request, sourceModel=source)
            self.get_batch(original["batchId"])["jobs"].append(job)
            self._save()
            result = copy.deepcopy(job)
        self.wake.set()
        return result

    def _checked_delete_root(self, engine, run_id):
        if not isinstance(engine, str) or not re.fullmatch(r"[a-z0-9_-]+", engine):
            raise ValueError("删除工具标识无效")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("删除运行标识无效")
        if engine not in {item["id"] for item in self.engines()}:
            raise ValueError("删除工具未注册")
        outputs = self.root / "outputs"
        engine_root = outputs / engine
        folder = engine_root / run_id
        # A junction or symlink must not redirect a destructive operation to
        # another run, another engine, or shared source images.
        if any(path.resolve() != path for path in (outputs, engine_root, folder)):
            raise ValueError("删除运行路径被重定向")
        if not folder.resolve().is_relative_to(outputs.resolve()) or folder.parent != engine_root:
            raise ValueError("删除路径超出工具运行目录")
        if folder.exists() and not folder.is_dir():
            raise ValueError("运行路径不是目录")
        receipt = self._receipt(folder)
        if receipt.get("engine", engine) != engine or receipt.get("runId", run_id) != run_id:
            raise ValueError("运行收据身份与删除目标不一致")
        return folder

    def _delete_run(self, engine, run_id, selected_id, jobs):
        if any(job["status"] not in TERMINAL | {"deleted"} for job in jobs):
            raise ValueError("等待或运行中的任务不可删除")
        folder = self._checked_delete_root(engine, run_id)
        original = [(job, copy.deepcopy(job)) for job in jobs]
        if folder.exists():
            shutil.rmtree(folder)
        if folder.exists():
            raise RuntimeError("运行目录删除未完成，状态已保留")
        deleted_at = now()
        for job in jobs:
            kept = {key: job[key] for key in ("id", "batchId", "engine", "engineId", "runId", "kind", "objectId", "objectName", "mainImageId", "inputMode", "createdAt", "startedAt", "retryOf") if key in job}
            kept.update(status="deleted", phase="已删除", finishedAt=job.get("finishedAt") or deleted_at, deletedAt=deleted_at)
            job.clear()
            job.update(kept)
        try:
            self._save()
        except Exception:
            for job, record in original:
                job.clear()
                job.update(record)
            raise
        self.history_read_at = 0
        self.history_cache = []
        return {"id": selected_id, "runId": run_id, "engine": engine, "status": "deleted", "deleted": True,
                "deletedAt": deleted_at, "jobIds": [job["id"] for job in jobs]}

    def delete_job(self, job_id):
        with self.lock:
            job = self._find_job(job_id)
            if job["status"] not in TERMINAL | {"deleted"}:
                raise ValueError("等待或运行中的任务不可删除")
            jobs = [item for item in self._jobs() if item["engine"] == job["engine"] and item["runId"] == job["runId"]]
            return self._delete_run(job["engine"], job["runId"], job_id, jobs)

    def delete_history(self, history_id):
        with self.lock:
            if not isinstance(history_id, str) or not re.fullmatch(r"[a-z0-9_-]+~[A-Za-z0-9][A-Za-z0-9_-]{0,127}", history_id):
                raise ValueError("历史记录标识无效")
            engine, run_id = history_id.split("~", 1)
            folder = self._checked_delete_root(engine, run_id)
            receipt = self._receipt(folder)
            if not folder.is_dir() or not receipt:
                raise ValueError("历史记录不存在")
            if receipt.get("status") not in TERMINAL:
                raise ValueError("等待或运行中的记录不可删除")
            jobs = [job for job in self._jobs() if job["engine"] == engine and job["runId"] == run_id]
            return self._delete_run(engine, run_id, history_id, jobs)

    @staticmethod
    def _receipt(run_root):
        try:
            receipt = json.loads((run_root / "receipt.json").read_text(encoding="utf-8-sig"))
            return receipt if isinstance(receipt, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _tail(path):
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 24000))
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _work(self):
        while not self.stopping.is_set():
            self.pipeline.advance()
            with self.lock:
                job = next((job for job in self._jobs() if job["status"] == "queued"), None)
                if job:
                    job.update(status="running", phase="启动引擎", startedAt=now())
                    self._save()
            if job:
                try:
                    self._execute_job(job)
                except Exception as error:
                    with self.lock:
                        interrupted = self.stopping.is_set()
                        job.update(status="interrupted" if interrupted else "failed",
                                   phase="应用关闭，任务中断" if interrupted else "生产失败",
                                   error=str(error), finishedAt=now())
                        self._save()
                self.pipeline.advance()
            else:
                self.wake.wait(1)
                self.wake.clear()

    def _stop_process(self, process):
        stop_owned_process(process)

    @staticmethod
    def _validate_model_color(job, output):
        if job.get('appearance') not in {'texture', 'painted'}:
            return None
        models = sorted(output.glob('*.glb'))
        if not models:
            raise ValueError('已选择带贴图建模，但没有返回内嵌颜色贴图的 GLB')
        results = []
        for path in models:
            if not path.resolve().is_relative_to(output.resolve()):
                raise ValueError('带贴图模型输出超出本次目录')
            results.append({'name':path.name, **inspect_texture_glb(path)})
        return {'appearance':'texture', 'visualQuality':'unreviewed', 'outputs':results}

    def _execute_job(self, job):
        output = self.run_root(job)
        launcher_log = self.storage / f"{job['id']}.log"
        views_file = self.storage / f"{job['id']}-views.json"
        with self.lock:
            sources = {view: str(safe_child(self.root, self._image(key)["path"])) for view, key in job["viewImages"].items()}
        image_job = job.get("kind") == "image"
        model_job = job.get("kind") in {"texture", "rig", "animation"}
        media_job=job.get('mediaJob') is True
        write_json(views_file, job["request"] if image_job or model_job or media_job else sources)
        host = powershell_command(self.root)
        if media_job:
            command=[host,'-NoLogo','-NoProfile','-File',str(self.root/'scripts/media-job.ps1'),
                     '-Engine',job['engine'],'-RequestJson',str(views_file),'-RunId',job['runId']]
        elif image_job:
            command = [host, "-NoLogo", "-NoProfile", "-File", str(self.root / "scripts/image-job.ps1"),
                       "-Engine", job["engine"], "-RequestJson", str(views_file), "-RunId", job["runId"]]
        elif model_job:
            command = [host, "-NoLogo", "-NoProfile", "-File", str(self.root / "scripts/model-job.ps1"),
                       "-Engine", job["engine"], "-RequestJson", str(views_file), "-RunId", job["runId"]]
        else:
            command = [host, "-NoLogo", "-NoProfile", "-File", str(self.root / "scripts/factory.ps1"),
                       "-Action", "run", "-Engine", job["engine"], "-InputImage", sources["front"], "-RunId", job["runId"]]
        if job["inputMode"] == "multi-view":
            command += ["-InputSet", str(views_file)]
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with launcher_log.open("wb") as stream:
            process = subprocess.Popen(command, cwd=self.root, stdout=stream, stderr=subprocess.STDOUT, **flags)
            self.process = process
            owned_job = None
            try:
                if os.name == 'nt':
                    owned_job = WindowsOwnedJob(process)
                    process._factory_owned_job = owned_job
                while process.poll() is None:
                    with self.lock:
                        cancel = job["cancelRequested"]
                    if cancel or self.stopping.is_set():
                        self._stop_process(process)
                        break
                    log = self._tail(output / "run.log") or self._tail(launcher_log)
                    with self.lock:
                        job["logTail"] = log
                        job["phase"], job["progress"] = log_progress(log)
                    time.sleep(0.5)
                receipt = self._receipt(output)
                if process.returncode == 0 and receipt.get('status') == 'generated-unreviewed':
                    try:
                        color = self._validate_model_color(job, output)
                        if color:
                            receipt['colorValidation'] = color
                            write_json(output / 'receipt.json', receipt)
                    except (ValueError, OSError, KeyError, TypeError) as validation_error:
                        receipt.update(status='failed', error='彩色产物检查未通过：' + str(validation_error))
                        write_json(output / 'receipt.json', receipt)
                log = self._tail(output / "run.log") or self._tail(launcher_log)
                with self.lock:
                    if job["cancelRequested"]:
                        status, phase, error = "cancelled", "已取消", None
                    elif self.stopping.is_set():
                        status, phase, error = "interrupted", "服务停止，任务中断", "请确认后重试"
                    elif process.returncode == 0 and receipt.get("status") == "generated-unreviewed" and any(asset["kind"] == (job['kind'] if job.get('kind') in {'image','video'} else 'model') for asset in self.assets(output)):
                        status, phase, error = "generated-unreviewed", "生成完成 · 可预览", None
                    else:
                        status, phase, error = "failed", "生产失败", receipt.get("error") or log.strip()[-1400:] or f"引擎退出码 {process.returncode}"
                    job.update(status=status, phase=phase, error=error, logTail=log, finishedAt=now(),
                               progress=100 if status == "generated-unreviewed" else None)
                    if receipt.get("status") == "running" and status in {"cancelled", "interrupted"}:
                        receipt.update(status=status, finishedAt=job["finishedAt"], error=error)
                        write_json(output / "receipt.json", receipt)
                    self._save()
                    self.history_read_at = 0
            finally:
                try:
                    if process.poll() is None:
                        self._stop_process(process)
                finally:
                    if owned_job:
                        owned_job.close()
                    self.process = None

    def assets(self, folder):
        assets = []
        if not folder.is_dir():
            return assets
        receipt = self._receipt(folder)
        image_engine = receipt.get("inputMode") == "text" or receipt.get('kind')=='image'
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()) or not path.stat().st_size:
                continue
            if path.name.startswith(("input", "prepared")) or path.suffix == ".part":
                continue
            suffix = path.suffix.lower()
            if receipt.get("pipelineStage") in {"texture", "rig", "animation"} and any(part in {"work", "diagnostics", "references"} for part in path.relative_to(folder).parts[:-1]) and suffix in {".glb", ".obj", ".ply", ".png", ".jpg", ".jpeg", ".webp"}:
                continue
            kind = "video" if suffix in {'.mp4','.webm','.mov'} else 'audio' if suffix in {'.wav','.mp3','.flac','.ogg','.m4a'} else "model" if suffix in {".glb", ".obj", ".ply"} else ("image" if image_engine else "texture") if suffix in {".png", ".jpg", ".jpeg", ".webp"} else "data"
            relative = path.relative_to(self.root).as_posix()
            assets.append({"name": path.relative_to(folder).as_posix(), "url": "/files/" + quote(relative),
                           "bytes": path.stat().st_size, "kind": kind,
                           "previewUrl": "/files/" + quote(relative) if suffix == ".glb" or kind in {'image','video','audio'} else None})
            if suffix == ".glb":
                signature = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
                if signature not in self.model_cache:
                    try:
                        self.model_cache[signature] = inspect_glb(path)
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        self.model_cache[signature] = {"error": str(error)}
                    if len(self.model_cache) > 256:
                        self.model_cache.pop(next(iter(self.model_cache)))
                assets[-1]["modelInfo"] = self.model_cache[signature]
        return assets

    def history(self):
        if time.monotonic() - self.history_read_at < 2:
            return copy.deepcopy(self.history_cache)
        items = []
        engines=self.engines()+[{'id':'imported-model','title':'本机导入','kind':'model'}]
        for engine in engines:
            for path in (self.root / "outputs" / engine["id"]).glob("*/receipt.json"):
                receipt = self._receipt(path.parent)
                if not receipt:
                    continue
                folder = path.parent
                inputs = self._receipt_inputs(folder, receipt)
                input_url = next((item["url"] for item in inputs if item["view"] == "front"), None)
                kind = engine['kind'] if engine['kind'] in {'image','video'} else 'model'
                item = {"id": f"{engine['id']}~{folder.name}", "runId": folder.name, "engine": engine["id"], "engineId": engine["id"], "kind": kind,
                              "status": receipt.get("status"), "createdAt": receipt.get("startedAt"),
                              "factoryVersion": receipt.get("factoryVersion"), "inputUrl": input_url,
                              "assets": self.assets(folder) if receipt.get("status") == "generated-unreviewed" else [],
                              "error": receipt.get("error"), "inputMode": receipt.get("inputMode", "text" if kind == "image" else "single"),
                              "inputs": inputs, "objectName": receipt.get("objectName"), "finishedAt": receipt.get("finishedAt")}
                if receipt.get('origin')=='local-import':item['origin']='local-import'
                if receipt.get("pipelineStage") in {"texture", "rig", "animation"}:
                    item.update(pipelineStage=receipt["pipelineStage"], sourceModel=receipt.get("sourceModel"))
                if isinstance(receipt.get("request"), dict):
                    item["request"] = copy.deepcopy(receipt["request"])
                    if "weightVariants" in engine and kind == "image":
                        item["request"].setdefault("weightVariant", "Q4_K")
                    if "weightVariant" in item["request"]:
                        item["weightVariant"] = item["request"]["weightVariant"]
                items.append(item)
        self.history_cache = sorted(items, key=lambda item: item.get("createdAt") or "", reverse=True)
        self.history_read_at = time.monotonic()
        return copy.deepcopy(self.history_cache)

    def _input_record(self, view, candidates, name=None):
        for candidate in candidates:
            if not isinstance(candidate, (str, Path)) or not str(candidate):
                continue
            try:
                path = safe_child(self.root, str(candidate))
                if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
            except (OSError, TypeError, ValueError):
                continue
            return {"view": view, "url": "/files/" + quote(path.relative_to(self.root).as_posix()), "name": name or path.name}
        return None

    def _receipt_inputs(self, folder, receipt):
        slots = {}
        raw = receipt.get("inputs", [])
        if isinstance(raw, dict):
            raw = [({"view": view, **value} if isinstance(value, dict) else {"view": view, "path": value}) for view, value in raw.items()]
        if isinstance(raw, list):
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                view = item.get("view", "front" if index == 0 else None)
                if view in {"front", "left", "back", "right"}:
                    slots[view] = item
        legacy = receipt.get("input")
        if "front" not in slots and legacy:
            slots["front"] = legacy if isinstance(legacy, dict) else {"path": legacy}
        inputs = []
        for view in ("front", "left", "back", "right"):
            item = slots.get(view, {})
            prefix = "input" if view == "front" else f"input_{view}"
            copies = sorted(folder.glob(prefix + ".*"))
            candidates = [item.get("path"), *copies, item.get("source")]
            if view == "front":
                candidates.append(receipt.get("source"))
            record = self._input_record(view, candidates, item.get("name"))
            if record:
                inputs.append(record)
        return inputs

    def _task_inputs(self, jobs):
        slots = {}
        for job in jobs:
            if job.get('mediaJob'):
                for index,key in enumerate(job.get('request',{}).get('inputImageIds',[])):
                    image=self.data['images'].get(key,{})
                    record=self._input_record('front' if index==0 else 'reference',[image.get('path')],image.get('name'))
                    if record:slots.setdefault('front' if index==0 else 'reference-'+str(index),record)
                continue
            if job.get("kind") == "image":
                continue
            image_ids = {"front": job.get("mainImageId")}
            image_ids.update({item["view"]: item["imageId"] for item in job.get("auxiliaryImages", [])})
            for view, image_id in image_ids.items():
                image = self.data["images"].get(image_id, {})
                record = self._input_record(view, [image.get("path")], image.get("name"))
                if record and view not in slots:
                    slots[view] = record
            for record in self._receipt_inputs(self.run_root(job), self._receipt(self.run_root(job))):
                slots.setdefault(record["view"], record)
        return [slots[view] for view in ("front", "left", "back", "right") if view in slots]+[value for key,value in slots.items() if key.startswith('reference-')]

    def result_tasks(self, batches, history):
        tasks = []
        represented = {(job["engine"], job["runId"]) for job in self._jobs()}
        for batch in batches:
            groups = {}
            for job in batch["jobs"]:
                if job["status"] == "deleted":
                    continue
                object_id = job.get("objectId") or job.get("mainImageId") or "image"
                kind = job['kind'] if job.get('kind') in {'image','video'} else 'model'
                groups.setdefault((kind, object_id), []).append(job)
            for (kind, object_id), jobs in groups.items():
                first = jobs[0]
                task_id = f"queue~{batch['id']}~{object_id}"
                inputs = self._task_inputs(jobs)
                task = {"id": task_id, "name": first.get("objectName") or batch["name"], "batchId": batch["id"],
                        "batchName": batch["name"], "createdAt": first.get("createdAt") or batch.get("createdAt"),
                        "source": "queue", "kind": kind, "inputMode": "text" if kind == "image" else "multi-view" if len(inputs) > 1 or any(job["inputMode"] == "multi-view" for job in jobs) else "single",
                        "inputs": inputs, "jobs": jobs,
                        "downloadUrl": "/api/result-tasks/" + quote(task_id) + "/download" if any(job["status"] == "generated-unreviewed" for job in jobs) else None}
                if first.get("kind") in {"texture", "rig", "animation"}:
                    task.update(inputMode="model", pipelineStage=first["kind"], sourceModel=first.get("sourceModel"))
                if kind == "image":
                    task["prompt"] = first.get("request", {}).get("prompt")
                    if "weightVariant" in first.get("request", {}):
                        task["weightVariant"] = first["request"]["weightVariant"]
                tasks.append(task)
        for item in history:
            if (item["engine"], item["runId"]) in represented or item["status"] == "deleted":
                continue
            task_id = "history~" + item["id"]
            job = {**item, "historyId": item["id"]}
            task = {"id": task_id, "name": item.get("objectName") or f"{item['engine']} · {item['runId']}",
                    "batchId": None, "batchName": "历史产物", "createdAt": item.get("createdAt"), "source": "history",
                    "kind": item["kind"], "inputMode": item["inputMode"], "inputs": item["inputs"], "jobs": [job],
                    "downloadUrl": "/api/result-tasks/" + quote(task_id) + "/download" if item["status"] == "generated-unreviewed" else None}
            if item.get("pipelineStage") in {"texture", "rig", "animation"}:
                task.update(pipelineStage=item["pipelineStage"], sourceModel=item.get("sourceModel"))
            if item["kind"] == "image":
                task["prompt"] = item.get("request", {}).get("prompt")
                if "weightVariant" in item:
                    task["weightVariant"] = item["weightVariant"]
            tasks.append(task)
        return sorted(tasks, key=lambda task: task.get("createdAt") or "", reverse=True)

    def state(self):
        with self.lock:
            batches = copy.deepcopy(self.data["batches"])
            for batch in batches:
                batch["jobs"] = [job for job in batch["jobs"] if job["status"] != "deleted"]
            batches = [batch for batch in batches if batch["jobs"]]
            for batch in batches:
                for job in batch["jobs"]:
                    start, finish = job.get("startedAt"), job.get("finishedAt")
                    job["elapsedSeconds"] = 0
                    if start:
                        job["elapsedSeconds"] = max(0, round(((datetime.fromisoformat(finish) if finish else datetime.now(timezone.utc)) - datetime.fromisoformat(start)).total_seconds()))
                    job["assets"] = self.assets(self.run_root(job)) if job["status"] == "generated-unreviewed" else []
                    job["inputUrl"] = self._public_image(self.data["images"].get(job["mainImageId"], {})).get("url")
                    job['creationEnabled'] = creation_allowed(job) and creation_allowed(job.get('request'))
            engines = self.engines()
            registered = {engine['id']: engine for engine in engines}
            jobs = [job for batch in batches for job in batch["jobs"]]
            for job in jobs:
                job['creationEnabled'] = job['creationEnabled'] and creation_allowed(registered.get(job.get('engine')))
            history = self.history()
            for item in history:
                item['creationEnabled'] = creation_allowed(item) and creation_allowed(item.get('request')) and creation_allowed(registered.get(item.get('engine')))
            hardware = self.setup.hardware()
            from factory_media import public_engines
            media_engines=public_engines(self)
            gpu = hardware.get('gpus', [])
            gpu_label = f"{gpu[0]['name']} · {gpu[0].get('memoryMiB', 0) / 1024:g} GB" if gpu else '未检测到 NVIDIA 显卡'
            tasks = self.result_tasks(batches, history)
            for task in tasks:
                task['creationEnabled'] = creation_allowed(task) and all(job.get('creationEnabled', True) and creation_allowed(job) and creation_allowed(job.get('request')) for job in task.get('jobs', []))
            return {"version": self.version(), "historicalEngines": engines, "engines": [engine for engine in engines if engine["kind"] == "model" and creation_allowed(engine)],
                    "imageEngines": [engine for engine in engines if engine["kind"] == "image" and creation_allowed(engine) and ('txt2img' in engine.get('capabilities',{}).get('modes',['txt2img']))],
                    'mediaEngines':media_engines,'mediaJobs':[job for job in jobs if job.get('mediaJob')],
                    'mediaInputs':[{**record,'url':'/files/'+record['path']} for record in self.data.get('mediaInputs',{}).values()],
                    "rigEngines": [],
                    "animationEngines": [],
                    "textureEngines": [engine for engine in engines if engine["kind"] == "texture" and creation_allowed(engine)],
                    "batches": [batch for batch in batches if batch.get("kind") != "image"],
                    "imageJobs": [job for job in jobs if job.get("kind") == "image"], "jobs": jobs,
                    "images": {key: self._public_image(record) for key, record in self.data["images"].items()},
                    "history": history, "resultTasks": tasks,
                    "pipelines": self.pipeline.public(jobs),
                    "machine": {"gpu": gpu_label, "queue": "串行"}}

    def archive(self, *, batch_id=None, history_id=None, task_id=None):
        with self.lock:
            if task_id:
                task = next((item for item in self.state()["resultTasks"] if item["id"] == task_id), None)
                if not task:
                    raise ValueError("结果任务不存在")
                folders = [(f"{job['engine']}/{job['runId']}", self.run_root(job)) for job in task["jobs"] if job["status"] == "generated-unreviewed"]
            elif batch_id:
                jobs = copy.deepcopy(self.get_batch(batch_id)["jobs"])
                folders = [(f"{job['engine']}/{job['runId']}", self.run_root(job)) for job in jobs if job["status"] == "generated-unreviewed"]
            elif history_id and re.fullmatch(r"[a-z0-9_-]+~[a-zA-Z0-9_-]+", history_id):
                engine, run_id = history_id.split("~", 1)
                if engine not in {item["id"] for item in self.engines()}:
                    raise ValueError("记录不存在")
                folder = safe_child(self.root / "outputs", f"{engine}/{run_id}")
                if self._receipt(folder).get("status") != "generated-unreviewed":
                    raise ValueError("此记录没有完成的产物")
                folders = [(f"{engine}/{run_id}", folder)]
            else:
                raise ValueError("下载记录无效")
        if not folders:
            raise ValueError("此批次尚无已完成产物")
        target = self.storage / f"download-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            for prefix, folder in folders:
                for path in sorted(folder.rglob("*")):
                    if path.is_file() and path.resolve().is_relative_to(folder.resolve()) and path.suffix != ".part":
                        archive.write(path, prefix + "/" + path.relative_to(folder).as_posix())
        return target

    def close(self, pause_pending=False):
        self.stopping.set()
        self.wake.set()
        setup_error = None
        try:
            self.setup.close()
        except Exception as error:
            setup_error = error
        if self.worker:
            self.worker.join(timeout=40)
            if self.worker.is_alive():
                raise RuntimeError("引擎仍在停止，请保留服务日志")
        if setup_error:
            raise setup_error
        if pause_pending:
            with self.lock:
                self.pipeline.pause_all()
                for job in self._jobs():
                    if job.get('status') in {'queued', 'running'}:
                        job.update(status='interrupted', phase='应用已关闭，任务暂停',
                                   error='重新打开后，点击重试继续。', finishedAt=now())
                self._save()
            with self.setup.lock:
                for job in self.setup.jobs:
                    if job.get('status') in {'queued', 'running'}:
                        job.update(status='interrupted', stage='应用已关闭，安装暂停；点击重试继续',
                                   updatedAt=now())
                self.setup._save()
