"""Local, resumable model deployment and first-run detection. v0.5.0a / Unclecow."""
from __future__ import annotations

import copy
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse
import urllib.error
import urllib.request
import uuid
import zipfile

try:
    from factory_capabilities import creation_allowed, require_creation
except ModuleNotFoundError as error:
    if error.name != 'factory_capabilities':
        raise
    # Some maintenance callers load this module by absolute path.
    policy_spec = importlib.util.spec_from_file_location('factory_capabilities', Path(__file__).with_name('factory_capabilities.py'))
    policy_module = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy_module)
    creation_allowed, require_creation = policy_module.creation_allowed, policy_module.require_creation


def deploy_profile(root, profile_id, on_progress=None, cancel=None, accepted_installation=None):
    from factory_deploy import deploy_profile as deploy
    return deploy(root, profile_id, on_progress=on_progress, cancel=cancel, accepted_installation=accepted_installation)

ACTIVE = {"queued", "running"}


def run_probe(command, cwd, env, timeout, cancel):
    """Keep first-run probes inside the application's cancellable process tree."""
    from factory_deploy import WindowsOwnedJob, stop_owned_process
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=os.name != 'nt',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        owned_job = None
        started = time.monotonic()
        try:
            if os.name == 'nt':
                owned_job = WindowsOwnedJob(process)
                process._factory_owned_job = owned_job
            while process.poll() is None:
                if cancel():
                    raise InstallCancelled()
                if time.monotonic() - started > timeout:
                    raise RuntimeError('运行组件检测超时，请查看安装记录后重试。')
                time.sleep(.1)
            output.seek(0)
            return subprocess.CompletedProcess(command, process.returncode,
                output.read().decode('utf-8', errors='replace'), '')
        finally:
            try:
                if process.poll() is None:
                    stop_owned_process(process)
            finally:
                if owned_job:
                    owned_job.close()


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("安装路径必须是项目内的相对路径")
    def canonical(path):
        text = str(path.resolve())
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return Path(text)
    candidate = canonical(root / relative)
    canonical_root = canonical(root)
    if candidate == canonical_root or not candidate.is_relative_to(canonical_root):
        raise ValueError("安装路径超出项目目录")
    return candidate


def verify_file(path, item):
    if not path.is_file() or path.stat().st_size != item["size"]:
        raise ValueError(f"文件大小不符，已保留原文件：{path.name}")
    digest = hashlib.sha256()
    git_digest=hashlib.sha1(b'blob '+str(item['size']).encode('ascii')+b'\0') if item.get('gitBlobSha1') else None
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
            if git_digest:git_digest.update(block)
    if (item.get('sha256') and digest.hexdigest()!=item['sha256']) or (git_digest and git_digest.hexdigest()!=item['gitBlobSha1']) or not (item.get('sha256') or git_digest):
        raise ValueError(f"SHA256 校验失败，原文件已保留：{path.name}")
    return digest.hexdigest()


def extract_archive(archive, destination, flatten_dlls=False, strip_prefix=None):
    """Only extract verified archives; reject traversal and symbolic links."""
    destination.mkdir(parents=True, exist_ok=True)
    result = []
    with zipfile.ZipFile(archive) as bundle:
        entries = []
        for entry in bundle.infolist():
            normalized = entry.filename.replace("\\", "/")
            if '..' in normalized.split('/') or normalized.startswith('/'):
                raise ValueError('运行包包含越界路径')
            if strip_prefix:
                if not isinstance(strip_prefix,str) or not strip_prefix.endswith('/') or '..' in strip_prefix.split('/'):
                    raise ValueError('源码目录前缀无效')
                if not normalized.startswith(strip_prefix):
                    raise ValueError('源码目录与固定版本清单不一致')
                normalized=normalized[len(strip_prefix):]
                if not normalized:continue
            target = safe_path(destination, normalized)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("运行包不允许包含符号链接")
            if flatten_dlls:
                if not normalized.lower().endswith((".dll", "license.txt", "license")):
                    continue
                if normalized.lower().endswith(".dll"):
                    target = destination / Path(normalized).name
                else:
                    target = safe_path(destination, "licenses/" + archive.name + "/" + normalized)
            entries.append((entry, target))
        for entry, target in entries:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".extract.part")
            with bundle.open(entry) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            digest = hashlib.sha256()
            with temporary.open("rb") as stream:
                while block := stream.read(4 * 1024 * 1024):
                    digest.update(block)
            expected = digest.hexdigest()
            if target.exists():
                verify_file(target, {"size": entry.file_size, "sha256": expected})
                temporary.unlink()
            else:
                temporary.replace(target)
            result.append({"path": str(target), "size": entry.file_size, "sha256": expected})
    return result


def detect_hardware(root):
    gpus, error = [], None
    smi = shutil.which("nvidia-smi")
    if not smi and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation/NVSMI/nvidia-smi.exe"
        smi = str(candidate) if candidate.is_file() else None
    if smi:
        try:
            result = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=5,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in result.stdout.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) == 3:
                    gpus.append({"name": parts[0], "memoryMiB": int(parts[1]), "driver": parts[2]})
            if result.returncode:
                error = "NVIDIA 驱动没有正常响应"
        except (OSError, ValueError, subprocess.SubprocessError):
            error = "无法读取 NVIDIA 显卡，请检查驱动后重新检测"
    ram = None
    usable_ram = None
    free_ram = None
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(field, ctypes.c_ulonglong) for field in
                         ("totalPhys", "availPhys", "totalPage", "availPage", "totalVirtual", "availVirtual", "availExtended")]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            ram = round(value.totalPhys / (1024 ** 3), 1)
            usable_ram=ram
            free_ram=round(value.availPhys/(1024**3),1)
        installed_memory=ctypes.c_ulonglong()
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(installed_memory)):
            ram=round(installed_memory.value/(1024**2),1)
    system, architecture = platform.system(), platform.machine()
    build_tools={'msvc':False}
    if os.name=='nt':
        vswhere=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
        if vswhere.is_file():
            try:
                result=subprocess.run([str(vswhere),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],
                    capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                build_tools['msvc']=result.returncode==0 and bool(result.stdout.strip())
            except (OSError,subprocess.SubprocessError):pass
    free = shutil.disk_usage(root).free
    supported = system == "Windows" and architecture.lower() in {"amd64", "x86_64"}
    python_ready = (root / "runtime/core/.venv/Scripts/python.exe").is_file() or Path(sys.executable).is_file()
    private_shell = root / "runtime/tools/pwsh/pwsh.exe"
    shell_ready = private_shell.is_file() or bool(shutil.which("pwsh"))
    requirements = [
        {"id": "platform", "label": "操作系统", "status": "ok" if supported else "missing",
         "detail": f"{system} · {architecture}", "remedy": "当前自动部署支持 Windows 10 / 11 的 x64 电脑。"},
        {"id": "python", "label": "基础运行环境", "status": "ok" if python_ready else "missing",
         "detail": "基础运行环境已就绪" if python_ready else "基础运行环境尚未准备",
         "remedy": "重新打开造点啥.exe，应用会自动准备所需组件，无需手动安装 Python。"},
        {"id": "powershell", "label": "模型启动组件", "status": "ok" if shell_ready else "missing",
         "detail": "模型启动组件已就绪" if shell_ready else "模型启动组件尚未准备",
         "remedy": "重新打开造点啥.exe，应用会自动准备所需组件。"},
        {"id": "gpu", "label": "NVIDIA 显卡与驱动", "status": "ok" if gpus else "missing",
         "detail": gpus[0]["name"] if gpus else error or "未检测到支持 CUDA 的 NVIDIA 显卡",
         "remedy": "安装 NVIDIA 官方显卡驱动后重新检测；AMD / Intel 显卡目前不能运行这些本地模型。"},
        {"id": "ram", "label": "系统内存", "status": "ok" if ram is not None and ram >= 16 else "warning",
         "detail": f"{ram:g} GB" if ram is not None else "尚未读取到系统内存",
         "remedy": "模型会用系统内存存放部分权重；安装前请查看该模型的最低内存需求。"},
        {"id": "disk", "label": "磁盘空间", "status": "ok" if free >= 15 * 1024**3 else "warning",
         "detail": f"应用所在磁盘可用 {free / 1024**3:.1f} GB",
         "remedy": "首次安装会下载大文件并准备运行环境，请为所选模型留出足够空间。"},
    ]
    return {"platform": system, "architecture": architecture, "gpus": gpus,
            "gpuAvailable": bool(gpus), "ramGiB": ram, 'usableRamGiB':usable_ram,'freeRamGiB':free_ram,"freeDiskBytes": free,
            "root": str(root), "requirements": requirements,'buildTools':build_tools}


class InstallCancelled(Exception):
    pass


class FactorySetup:
    def __init__(self, root, catalog=None, hardware=None, start_worker=True):
        self.root = Path(root).resolve()
        catalog_path = self.root / "config/setup-catalog.json"
        local_catalog=catalog_path.is_file()
        if not catalog_path.is_file():
            catalog_path = Path(__file__).resolve().parents[1] / "config/setup-catalog.json"
        self.catalog = catalog if catalog is not None else json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"models": []}
        if catalog is None:
            for name in ('python-models.json','image3d-models.json','video-models.json','spar-models.json','multiview-models.json'):
                python_catalog_path = self.root / 'config' / name
                if not python_catalog_path.exists():
                    if local_catalog:continue
                    python_catalog_path = Path(__file__).resolve().parents[1] / 'config' / name
                if not python_catalog_path.exists():continue
                extensions = json.loads(python_catalog_path.read_text(encoding="utf-8"))
                by_id = {model["id"]: model for model in self.catalog.get("models", [])}
                by_id.update({model["id"]: model for model in extensions.get("models", [])})
                self.catalog["models"] = list(by_id.values())
        credential_spec=importlib.util.spec_from_file_location('factory_credentials',Path(__file__).with_name('factory_credentials.py'))
        credential_module=importlib.util.module_from_spec(credential_spec)
        credential_spec.loader.exec_module(credential_module)
        CredentialStore,ScopedAuthRedirect=credential_module.CredentialStore,credential_module.ScopedAuthRedirect
        self.credentials=CredentialStore(self.root)
        self.download_opener=urllib.request.build_opener(ScopedAuthRedirect())
        version_path=self.root/'VERSION.json'
        self.product_version=json.loads(version_path.read_text(encoding='utf-8-sig')).get('version',self.catalog.get('version','0.7.0')) if version_path.is_file() else self.catalog.get('version','0.7.0')
        self.hardware_override = hardware
        self._hardware = None
        self._hardware_at = 0
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.cancel_events = {}
        self.storage = self.root / "logs/setup"
        self.state_file = self.storage / "state.json"
        self.jobs = []
        self.worker = None
        ids = set()
        for model in self.catalog.get("models", []):
            if not re.fullmatch(r"[a-z0-9_-]+", model.get("id", "")) or model["id"] in ids:
                raise ValueError("模型安装清单 ID 无效或重复")
            ids.add(model["id"])
            for item in model.get("files", []):
                safe_path(self.root, item["path"])
                if type(item.get("size")) is not int or item["size"] <= 0 or not (re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")) or re.fullmatch(r'[a-f0-9]{40}',item.get('gitBlobSha1',''))):
                    raise ValueError("模型安装清单缺少固定大小或 SHA256")
                for url in [item.get("url", "")] + item.get("mirrors", []):
                    parsed = urlparse(url)
                    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
                        raise ValueError("安装下载仅允许 HTTPS 或本机测试地址")
            for item in model.get("required", []):
                safe_path(self.root, item["path"])
                for alternative in item.get("alternatives", []):
                    safe_path(self.root, alternative)
        for model in self.catalog.get("models", []):
            self._plans(model)
        if self.state_file.exists():
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(state.get("jobs"), list):
                raise ValueError("安装任务记录无法读取，原文件已保留")
            self.jobs = state["jobs"]
            for job in self.jobs:
                if job.get("status") in ACTIVE:
                    job.update(status="interrupted", stage="安装已中断，点击重试继续", updatedAt=timestamp())
            self._save()
        if start_worker:
            self.start_worker()

    def start_worker(self):
        if self.stopping.is_set():
            raise RuntimeError('安装队列已经关闭，请重新打开应用。')
        if self.worker is None:
            self.worker = threading.Thread(target=self._work, daemon=True, name="model-deployment")
            self.worker.start()

    def _save(self):
        write_json(self.state_file, {"version": self.product_version, "jobs": self.jobs})

    def hardware(self, refresh=False):
        if self.hardware_override is not None:
            return copy.deepcopy(self.hardware_override)
        if refresh or self._hardware is None or time.monotonic() - self._hardware_at > 30:
            self._hardware = detect_hardware(self.root)
            self._hardware_at = time.monotonic()
        return dict(self._hardware, freeDiskBytes=shutil.disk_usage(self.root).free)

    def _model(self, model_id):
        if not isinstance(model_id, str):
            raise ValueError("请选择安装列表中的模型")
        model = next((item for item in self.catalog.get("models", []) if item["id"] == model_id), None)
        if model is None:
            raise ValueError(f"模型不存在：{model_id}，请重新检测")
        return model

    def _plans(self, model):
        plans, seen, active = [], set(), set()
        def visit(entry):
            if entry["id"] in active:
                raise ValueError("模型安装清单包含循环依赖")
            if entry["id"] in seen:
                return
            active.add(entry["id"])
            for dependency in entry.get("dependencies", []):
                visit(self._model(dependency))
            active.remove(entry["id"])
            seen.add(entry["id"])
            plans.append(entry)
        visit(model)
        return plans

    def _files(self, model):
        files = {}
        for plan in self._plans(model):
            for item in plan.get("files", []):
                if item["path"] in files and any(files[item["path"]].get(key) != item.get(key) for key in ("size", "sha256",'gitBlobSha1')):
                    raise ValueError("共享模型文件的版本或哈希冲突")
                files[item["path"]] = item
        return list(files.values())

    def _component_present(self, item):
        for relative in [item["path"]] + item.get("alternatives", []):
            path = safe_path(self.root, relative)
            try:
                valid = path.is_dir() if item.get("directory") else path.is_file()
                if valid and item.get("size") is not None:
                    valid = path.stat().st_size == item["size"]
            except OSError:
                valid = False
            if valid:
                return True
        return False

    def _local_status(self, model):
        engine_id = model.get("engineId", model["id"])
        latest_deployment = next((job for job in reversed(self.jobs) if engine_id in job.get("deployingEngines", [])), None)
        if latest_deployment and engine_id not in latest_deployment.get("validatedEngines", []) and latest_deployment["status"] != "completed":
            message = "此模型正在部署，完成检测前暂不可用" if latest_deployment["status"] in ACTIVE else "上次部署未通过检测，请点击重试完成安装"
            return "blocked", [message]
        required = model.get("required", [])
        if not required:
            return "blocked", [model.get("remedy") or "此模型暂没有可重复部署的安装配方"]
        missing = []
        for item in required:
            if not self._component_present(item):
                missing.append(item["path"])
        for dependency in model.get("dependencies", []):
            dependency_status, _ = self._local_status(self._model(dependency))
            if dependency_status not in {"ready", "installed"}:
                missing.append(f"依赖工具 {dependency} 未完成安装")
        if missing:
            count = len(required) - len(missing)
            return "incomplete" if count else "missing", missing
        if model.get("kind") == "utility":
            return "installed", []
        manifest_path = self.root / "engines" / engine_id / "engine.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            return "incomplete", ["模型入口尚未部署，请点击安装完成最后一步"]
        if manifest.get("status") == "ready":
            return "ready", []
        receipt = self.storage / f"{model['id']}-receipt.json"
        if receipt.is_file():
            return "installed", []
        if manifest.get("status") == "installed":
            return "installed", []
        return "incomplete", ["环境检测尚未通过，请点击安装重新核验"]

    def readiness(self, engine_id):
        model = next((item for item in self.catalog.get("models", []) if item.get("engineId", item["id"]) == engine_id), None)
        if model is None:
            return None
        status, missing = self._local_status(model)
        return {"status": "blocked" if status == "incomplete" else status,
                "notes": "组件完整，本机实际生成已验收。" if status == "ready" else "组件已安装，首次实际生成待验收。" if status == "installed" else
                         "尚未完成模型部署：" + "、".join(missing[:3]) + "。请打开「模型与环境」完成安装。",
                "missing": missing}

    def _public_model(self, model, hardware):
        status, missing = self._local_status(model)
        files = self._files(model)
        total = sum(item["size"] for item in files)
        installed = 0
        partial_bytes = 0
        for item in files:
            target = safe_path(self.root, item["path"])
            try:
                if target.is_file() and target.stat().st_size == item["size"]:
                    installed += item["size"]
                elif target.with_name(target.name + ".part").is_file():
                    partial_bytes += min(item["size"], target.with_name(target.name + ".part").stat().st_size)
            except OSError:
                # A verified .part becomes its final name atomically while the UI polls.
                continue
        remaining = max(0, total - installed - partial_bytes)
        blockers = []
        has_recipe = bool(files) or bool(model.get("deploymentProfile"))
        if not has_recipe and status not in {"ready", "installed"}:
            blockers.append(model.get("remedy") or "此模型需要完成专用运行环境安装")
        if model.get('installable') is False or model.get('availability') in {'unreleased','cloud-only','unsupported'} or model.get('releaseStatus') in {'unreleased','cloud-only','unavailable'} or model.get('installStatus') in {'blocked','unavailable'}:
            blockers.append(model.get('remedy') or '此候选目前没有可在本机部署的完整版本。')
        if any(plan.get('gated') or plan.get('authRequired') for plan in self._plans(model)) and not self.credentials.public()['huggingface']['configured']:
            blockers.append("此模型需要 Hugging Face 账号授权；请先完成官方模型页面的申请。")
        for plan in self._plans(model):
            for requirement in plan.get('prerequisites',[]):
                if requirement.get('probe')=='msvc' and not hardware.get('buildTools',{}).get('msvc'):
                    blockers.append(requirement.get('remedy') or '此模型需要Visual Studio C++ Build Tools。')
                if requirement.get('path') and not safe_path(self.root,requirement['path']).exists():
                    blockers.append(requirement.get('remedy') or '缺少安装组件：'+requirement['path'])
        plans = self._plans(model)
        minimum_vram = max((plan.get("minimumVramMiB", 0) for plan in plans), default=0)
        minimum_ram = max((plan.get("minimumRamGiB", 0) for plan in plans), default=0)
        if minimum_vram > 0:
            if not hardware.get("gpuAvailable"):
                blockers.append("未检测到 NVIDIA CUDA 显卡与驱动，请安装驱动后重新检测。")
            elif max((gpu.get("memoryMiB", 0) for gpu in hardware.get("gpus", [])), default=0) < minimum_vram:
                blockers.append(f"此配置至少需要 {minimum_vram / 1024:g} GB NVIDIA 显存。")
        if hardware.get("platform") != "Windows" or hardware.get("architecture", "").lower() not in {"amd64", "x86_64"}:
            blockers.append("当前安装配方支持 Windows x64。")
        ram = hardware.get("ramGiB")
        if ram is not None and ram < minimum_ram:
            blockers.append(f"此配置需要至少 {minimum_ram} GB 系统内存。")
        reserve = sum(plan.get("extractReserveBytes", 0) for plan in plans) + (64 * 1024 * 1024 if total else 0)
        if hardware.get("freeDiskBytes", 0) < remaining + reserve:
            blockers.append(f"剩余磁盘空间不足，安装需要约 {(remaining + reserve) / 1024**3:.1f} GB 可用空间。")
        public = {key: copy.deepcopy(model.get(key)) for key in
                  ("id", "engineId", "weightVariant", "supportOnly", "title", "kind", "description", "recommended", "license", "remedy", "sourceUrl",
                   'capabilities','defaults','resourceProfile','validationStage','releaseStatus','installStatus','authRequired','runtime','appearance')}
        dependency_plans = self._plans(model)[:-1]
        if not public.get('appearance') and model.get('kind') in {'model', 'texture'}:
            public['appearance'] = (model.get('manifest') or {}).get('appearance')
            if not public['appearance'] and not model.get('availability'):
                public['appearance'] = 'texture' if model['id'] in {'trellis', 'pixal3d', 'sf3d'} else 'vertex' if model['id'] == 'triposr' else 'geometry'
        if dependency_plans and public.get("license"):
            dependency_names = "、".join(plan["title"] for plan in dependency_plans)
            public["license"]["notice"] = (public["license"].get("notice") or "") + f" 本次同时安装依赖 {dependency_names}，请阅读相应来源的模型与依赖许可。"
        public.update(status=status, canInstall=has_recipe and not blockers,
                      downloadBytes=total, remainingBytes=remaining, installedBytes=installed,
                      blockers=blockers, missing=missing, minimumVramMiB=minimum_vram,
                      minimumRamGiB=minimum_ram)
        public["environmentReserveBytes"] = sum(plan.get("extractReserveBytes", 0) for plan in self._plans(model))
        public["dependencyLicenses"] = [{"modelId": plan["id"], "title": plan["title"], **plan.get("license", {})} for plan in dependency_plans]
        if status=='installed':public['validationStage']='installed-unverified'
        elif status=='ready':public['validationStage']='local-verified'
        return public

    def snapshot(self, refresh=False):
        with self.lock:
            hardware = self.hardware(refresh)
            return {"version": self.product_version, "hardware": hardware,
                    "models": [self._public_model(model, hardware) for model in self.catalog.get("models", []) if all(creation_allowed(plan) for plan in self._plans(model))],
                    "jobs": copy.deepcopy(self.jobs), 'credentials':self.credentials.public()}

    def configure_credentials(self,payload):
        return self.credentials.configure(payload)

    def install(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("安装请求必须是 JSON 对象")
        model = self._model(payload.get("modelId"))
        require_creation(*self._plans(model))
        with self.lock:
            duplicate = next((job for job in self.jobs if job["modelId"] == model["id"] and job["status"] in ACTIVE), None)
            if duplicate:
                return copy.deepcopy(duplicate)
            if any(plan.get("license", {}).get("requiresAcceptance") for plan in self._plans(model)) and payload.get("acceptLicense") is not True:
                raise ValueError("请阅读模型许可并勾选同意后安装")
            public = self._public_model(model, self.hardware(refresh=True))
            if not public["canInstall"]:
                raise ValueError("；".join(public["blockers"]) or "此模型不能自动安装")
            job = {"id": uuid.uuid4().hex, "modelId": model["id"], "status": "queued", "progress": 0,
                   "stage": "等待安装", "downloadedBytes": 0, "totalBytes": public["downloadBytes"],
                   "log": [], "error": None, "createdAt": timestamp(), "updatedAt": timestamp(),
                   "licenseAccepted": payload.get("acceptLicense") is True, "gpuInferenceExecuted": False}
            self.jobs.append(job)
            self.cancel_events[job["id"]] = threading.Event()
            self._save()
            self.wake.set()
            return copy.deepcopy(job)

    def _job(self, job_id):
        job = next((entry for entry in self.jobs if entry["id"] == job_id), None)
        if job is None:
            raise ValueError("安装任务不存在")
        return job

    def cancel(self, job_id):
        with self.lock:
            job = self._job(job_id)
            if job["status"] == "queued":
                self.cancel_events.setdefault(job_id, threading.Event()).set()
                job.update(status="cancelled", stage="已取消，下载文件保留", updatedAt=timestamp())
                self._save()
            elif job["status"] == "running":
                self.cancel_events[job_id].set()
                job.update(stage="正在取消，下载文件保留", updatedAt=timestamp())
            return copy.deepcopy(job)

    def retry(self, job_id):
        with self.lock:
            old = copy.deepcopy(self._job(job_id))
            if old["status"] not in {"failed", "cancelled", "interrupted"}:
                raise ValueError("只有失败、取消或中断的安装才能重试")
        return self.install({"modelId": old["modelId"], "acceptLicense": old.get("licenseAccepted") is True})

    def _update(self, job, **fields):
        with self.lock:
            if fields.get("status") == "running" and job["status"] != "queued":
                return
            job.update(fields, updatedAt=timestamp())
            if "message" in fields:
                job["log"] = (job["log"] + [job.pop("message")])[-80:]
            self._save()

    def _check_cancel(self, job):
        if self.stopping.is_set() or self.cancel_events[job["id"]].is_set():
            raise InstallCancelled()

    def _download(self, item, job, completed):
        target = safe_path(self.root, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._update(job, stage=f"核验已有文件：{target.name}")
            verify_file(target, item)
            return
        partial = target.with_name(target.name + ".part")
        if partial.exists() and partial.stat().st_size > item["size"]:
            raise ValueError(f"未完成下载超过预期大小，已保留：{partial.name}")
        if partial.exists() and partial.stat().st_size == item["size"]:
            try:
                verify_file(partial, item)
            except ValueError:
                evidence = partial.with_name(partial.name + f".corrupt-{uuid.uuid4().hex[:8]}")
                partial.replace(evidence)
                self._update(job, message=f"错误下载已另存 {evidence.name}；重新下载固定来源")
            else:
                self._check_cancel(job)
                partial.replace(target)
                return
        errors = []
        for url in [item["url"]] + item.get("mirrors", []):
            self._check_cancel(job)
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "BuildSomething/0.7.0", "Accept-Encoding": "identity", **self.credentials.headers(url)}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            try:
                with (self.download_opener.open(request, timeout=15) if headers.get('Authorization') else urllib.request.urlopen(request, timeout=15)) as response:
                    if offset and response.status != 206:
                        offset = 0
                    elif offset:
                        content_range = response.headers.get("Content-Range", "")
                        if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith(f"/{item['size']}"):
                            raise ValueError("下载服务器返回错误的断点位置")
                    self._update(job, stage=f"下载：{target.name}", message=f"下载 {target.name} · {urlparse(url).hostname}")
                    with partial.open("ab" if offset else "wb") as output:
                        written = offset
                        last_update = 0
                        while True:
                            self._check_cancel(job)
                            block = response.read(64 * 1024)
                            if not block:
                                break
                            if written + len(block) > item["size"]:
                                raise ValueError("下载内容超过清单大小")
                            output.write(block)
                            output.flush()
                            written += len(block)
                            if time.monotonic() - last_update > 0.2:
                                self._update(job, downloadedBytes=completed + written,
                                             progress=round(90 * (completed + written) / max(1, job["totalBytes"]), 1))
                                last_update = time.monotonic()
                    self._check_cancel(job)
                    self._update(job, stage=f"SHA256 校验：{target.name}")
                    verify_file(partial, item)
                    self._check_cancel(job)
                    partial.replace(target)
                    return
            except InstallCancelled:
                raise
            except (OSError, ValueError, urllib.error.URLError) as error:
                detail='授权未通过：请先在官方模型页面接受许可，再更新可读取该模型的 Hugging Face 令牌。' if isinstance(error,urllib.error.HTTPError) and error.code in {401,403} else self.credentials.redact(error)
                errors.append(f"{urlparse(url).hostname}: {detail}")
                self._update(job, message=f"下载未完成：{errors[-1]}")
        raise RuntimeError("下载失败，可点击重试继续。" + "；".join(errors))

    def _deploy(self, model, job):
        engine_id = model.get("engineId", model["id"])
        self._update(job, deployingEngines=list(dict.fromkeys(job.get("deployingEngines", []) + [engine_id])),
                     validatedEngines=[item for item in job.get("validatedEngines", []) if item != engine_id])
        extracted = []
        for item in model.get("files", []):
            self._check_cancel(job)
            if item.get("extractTo"):
                self._update(job, stage="部署原生运行组件", progress=92)
                extracted.extend(extract_archive(safe_path(self.root, item["path"]),
                                                safe_path(self.root, item["extractTo"]), item.get("flattenDlls", False), item.get('stripPrefix')))
        for adapter in model.get("adapters", []):
            source = safe_path(self.root, adapter["source"])
            target = safe_path(self.root, adapter["target"])
            contents = source.read_bytes()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != contents:
                backup = target.with_name(target.name + f".before-0.5.0-{uuid.uuid4().hex[:8]}")
                shutil.copy2(target, backup)
            target.write_bytes(contents)
        profile_checks = None
        if model.get("deploymentProfile"):
            self._update(job, stage=f"安装 {model['title']} 的独立运行环境", progress=94,
                         acceptedDeploymentProfiles=list(dict.fromkeys(job.get('acceptedDeploymentProfiles', []) + [model['deploymentProfile']])))
            profile_checks = deploy_profile(self.root, model["deploymentProfile"],
                            on_progress=lambda message: self._update(job, message=str(message)),
                            cancel=lambda: self.stopping.is_set() or self.cancel_events[job["id"]].is_set(),
                            accepted_installation=job['id'])
        manifest_path = self.root / "engines" / engine_id / "engine.json"
        existing = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {}
        manifest = dict(model.get("manifest", {}), **existing)
        for name in ('runtime','capabilities','defaults','mediaAdapter','appearance'):
            if name in model:manifest[name]=copy.deepcopy(model[name])
        title = existing.get("title", model["title"]) if model.get("weightVariant") else model["title"]
        manifest.update(id=engine_id, title=title, kind=model.get("kind", "model"),
                        status="ready" if existing.get("status") == "ready" else "installed",
                        factoryVersion=self.product_version, entrypoint="run.ps1")
        if model.get("weightVariant"):
            definition = model["variantDefinition"]
            if definition.get("id") != model["weightVariant"]:
                raise ValueError("量化版本安装定义与选中版本不匹配")
            variant_entries = {entry["id"]: entry for entry in manifest.get("variants", [])}
            variant_entries[definition["id"]] = dict(definition, status="installed", verified=True, gpuVerified=False)
            manifest["variants"] = list(variant_entries.values())
        checks = []
        for probe in model.get("probes", []):
            self._check_cancel(job)
            executable = safe_path(self.root, probe["path"])
            environment = os.environ.copy()
            environment["PATH"] = str(executable.parent) + os.pathsep + str(self.root / "runtime/cuda12") + os.pathsep + environment.get("PATH", "")
            arguments = [str(self.root) if arg=='@root' else str(safe_path(self.root, arg[6:])) if arg.startswith("@root:") else arg for arg in probe.get("args", ["--help"])]
            working_directory = safe_path(self.root, probe["cwd"]) if probe.get("cwd") else executable.parent
            result = run_probe([str(executable)] + arguments, cwd=working_directory,
                               env=environment, timeout=probe.get("timeout", 45),
                               cancel=lambda: self.stopping.is_set() or self.cancel_events[job['id']].is_set())
            if result.returncode:
                raise RuntimeError(f"运行组件检测失败（{result.returncode}）：{(result.stdout + result.stderr)[-700:]}")
            text = result.stdout + result.stderr
            if any(flag not in text for flag in probe.get("requiredFlags", [])):
                raise RuntimeError("运行组件缺少此工具所需参数")
            checks.append({"path": probe["path"], "exitCode": result.returncode,
                           "outputSha256": hashlib.sha256(text.encode()).hexdigest()})
        self._check_cancel(job)
        missing = []
        for item in model.get("required", []):
            if not self._component_present(item):
                missing.append(item["path"])
        if missing:
            raise RuntimeError("安装后仍缺少组件：" + "、".join(missing[:5]))
        write_json(self.storage / f"{model['id']}-receipt.json", {"version": self.product_version, "modelId": model["id"],
                   "jobId": job["id"], "files": model.get("files", []), "extracted": extracted, "checks": checks,
                   'validatedContent':[{'path':item['path'],'sha256':item.get('sha256') or verify_file(safe_path(self.root,item['path']),item),
                                        'sourceHashType':'sha256' if item.get('sha256') else 'git-blob-sha1'} for item in model.get('files',[])],
                   "profileChecks": profile_checks,
                   "installedAt": timestamp(), "gpuInferenceExecuted": False, "licenseAccepted": job["licenseAccepted"]})
        write_json(manifest_path, manifest)
        if model.get("weightVariant") and model.get("variantSourceConfig"):
            variant_path = safe_path(self.root, model["variantSourceConfig"])
            saved_variants = json.loads(variant_path.read_text(encoding="utf-8")) if variant_path.is_file() else {"engineId": engine_id}
            entries = {entry["id"]: entry for entry in saved_variants.get("variants", [])}
            entries[model["weightVariant"]] = variant_entries[model["weightVariant"]]
            saved_variants["variants"] = list(entries.values())
            write_json(variant_path, saved_variants)
        self._update(job, validatedEngines=list(dict.fromkeys(job.get("validatedEngines", []) + [engine_id])))

    def _work(self):
        while not self.stopping.is_set():
            with self.lock:
                job = next((entry for entry in self.jobs if entry["status"] == "queued"), None)
            if job is None:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            try:
                self._update(job, status="running", stage="检测安装条件", message="开始安装；下载可取消并继续")
                self._check_cancel(job)
                model = self._model(job["modelId"])
                completed = 0
                for item in self._files(model):
                    self._check_cancel(job)
                    self._download(item, job, completed)
                    completed += item["size"]
                    self._update(job, downloadedBytes=completed, progress=round(90 * completed / max(1, job["totalBytes"]), 1))
                self._update(job, stage="部署并检测模型入口", progress=93)
                for plan in self._plans(model):
                    self._check_cancel(job)
                    self._deploy(plan, job)
                self._update(job, status="completed", stage="安装完成，可以开始创作；首次实际生成待验收", progress=100,
                             message="全部文件校验完成，运行入口检测通过")
            except InstallCancelled:
                self._update(job, status="interrupted" if self.stopping.is_set() else "cancelled", stage="安装已暂停，已下载内容保留")
            except Exception as error:
                if self.stopping.is_set() or self.cancel_events[job["id"]].is_set():
                    self._update(job, status="interrupted" if self.stopping.is_set() else "cancelled", stage="安装已暂停，已下载内容保留")
                else:
                    self._update(job, status="failed", stage="安装未完成，请查看原因并重试", error=str(error), message=str(error))

    def close(self):
        self.stopping.set()
        self.wake.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=18)
            if self.worker.is_alive():
                raise RuntimeError('安装仍在停止，请保留安装日志后重试关闭。')
