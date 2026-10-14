"""Deploy reviewed isolated Python model profiles. v0.5.0 / Unclecow."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import stat
import uuid
import zipfile
import signal
import ctypes
from ctypes import wintypes

SHIPPED_ROOT = Path(__file__).resolve().parents[1]


def deploy_bundle(archive, root, emit, cancel):
    from factory_setup import safe_path, verify_file
    with zipfile.ZipFile(archive) as bundle:
        entries=[]
        for entry in bundle.infolist():
            relative=entry.filename.replace('\\','/')
            target=safe_path(root,relative)
            if relative.split('/')[0] not in {'engines','animation-lab','texture-lab','config','video-lab','image-lab','model-lab','provider-lab'}:
                raise ValueError('部署源码路径不属于程序与配置目录')
            if entry.file_size>20*1024**2 or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('部署源码包包含过大文件或符号链接')
            entries.append((entry,target))
        for entry,target in entries:
            if cancel():raise RuntimeError('安装已取消，已备份的原文件保留')
            if entry.is_dir():continue
            data=bundle.read(entry)
            if target.is_file() and target.read_bytes()==data:continue
            if target.exists():
                backup=target.with_name(target.name+'.before-0.5.0-'+uuid.uuid4().hex[:8])
                shutil.copy2(target,backup)
                emit('原程序文件已备份：'+str(backup.relative_to(root)))
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.deploy-'+uuid.uuid4().hex+'.part')
            temporary.write_bytes(data)
            temporary.replace(target)


class WindowsOwnedJob:
    """Own the installer tree so any exception also stops its child compilers."""
    def __init__(self, process):
        class BasicLimits(ctypes.Structure):
            _fields_=[('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),('flags',wintypes.DWORD),
                      ('minimumWorkingSet',ctypes.c_size_t),('maximumWorkingSet',ctypes.c_size_t),
                      ('activeProcessLimit',wintypes.DWORD),('affinity',ctypes.c_size_t),
                      ('priorityClass',wintypes.DWORD),('schedulingClass',wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('readOperations','writeOperations','otherOperations','readBytes','writeBytes','otherBytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_=[('basic',BasicLimits),('io',Counters),('processMemoryLimit',ctypes.c_size_t),
                      ('jobMemoryLimit',ctypes.c_size_t),('peakProcessMemory',ctypes.c_size_t),('peakJobMemory',ctypes.c_size_t)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype=wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype=wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype=wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
        self.kernel.TerminateJobObject.restype=wintypes.BOOL
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=ExtendedLimits()
        limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not self.kernel.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            error=ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle,1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None


def stop_owned_process(process):
    if process.poll() is not None:
        return
    if os.name=='nt':
        owned_job=getattr(process,'_factory_owned_job',None)
        if owned_job:owned_job.terminate()
        else:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,
                           timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    else:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        if os.name=='nt':process.kill()
        else:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        process.wait(timeout=5)


def run_command(command, root, env, emit, cancel, timeout=3600):
    folder = root/'logs/setup'
    folder.mkdir(parents=True,exist_ok=True)
    logfile=folder/f'deployment-{os.getpid()}-{time.time_ns()}.log'
    started=time.monotonic()
    with logfile.open('wb') as stream:
        process=subprocess.Popen(command,cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=os.name!='nt',
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        owned_job=None
        if os.name=='nt':
            try:
                owned_job=WindowsOwnedJob(process)
                process._factory_owned_job=owned_job
            except Exception:
                stop_owned_process(process)
                raise
        position=0
        pending=b''
        try:
            while True:
                if cancel() or time.monotonic()-started>timeout:
                    stop_owned_process(process)
                    raise RuntimeError('安装已取消，已有下载缓存保留' if cancel() else '部署步骤超时，请查看日志后重试')
                with logfile.open('rb') as reader:
                    reader.seek(position);data=reader.read();position=reader.tell()
                if data:
                    pending+=data
                    lines=pending.split(b'\n');pending=lines.pop()
                    for line in lines[-12:]: emit(line.decode('utf-8',errors='replace')[-800:])
                if process.poll() is not None: break
                time.sleep(.2)
            if pending: emit(pending.decode('utf-8',errors='replace')[-800:])
            if process.returncode:
                raise RuntimeError(f'运行环境部署失败（退出码 {process.returncode}）。日志：{logfile.relative_to(root)}')
        finally:
            try:
                if process.poll() is None:
                    stop_owned_process(process)
            finally:
                if owned_job:owned_job.close()


def deploy_profile(root, profile_id, on_progress=None, cancel=None, profiles=None, accepted_installation=None):
    """Only commands from the shipped recipe can run; API supplies only an ID."""
    from factory_setup import safe_path, verify_file
    root=Path(root).resolve()
    emit=on_progress or (lambda _message:None)
    cancelled=cancel or (lambda:False)
    if profiles is None:
        source=root/'config/python-profiles.json'
        if not source.is_file():source=SHIPPED_ROOT/'config/python-profiles.json'
        profiles=json.loads(source.read_text(encoding='utf-8'))
        profiles['profiles']=list(profiles.get('profiles',[]))
        for filename in ('image3d-profiles.json','video-profiles.json','spar-profiles.json','multiview-profiles.json'):
            extra=root/'config'/filename
            if not extra.is_file():extra=SHIPPED_ROOT/'config'/filename
            if extra.is_file():
                profiles['profiles'].extend(json.loads(extra.read_text(encoding='utf-8')).get('profiles',[]))
    if not isinstance(profile_id,str) or not re.fullmatch('[a-z0-9_-]+',profile_id):
        raise ValueError('部署配方标识无效')
    profile=next((item for item in profiles.get('profiles',[]) if item['id']==profile_id),None)
    if profile is None:raise ValueError('部署配方不存在')
    from factory_capabilities import require_profile_creation
    require_profile_creation(root, profile, accepted_installation)
    if cancelled():raise RuntimeError('安装已取消')
    for item in profile.get('bundles',[]):
        archive=safe_path(root,item['path'])
        if not archive.is_file(): archive=safe_path(SHIPPED_ROOT,item['path'])
        verify_file(archive,item)
        emit('部署程序与配置：'+archive.name)
        deploy_bundle(archive,root,emit,cancelled)
    environment=profile.get('environment')
    if not environment:return
    uv=root/'runtime/tools/uv/uv.exe'
    executable=str(uv) if uv.is_file() else shutil.which('uv')
    if not executable:raise RuntimeError('缺少安装组件，请重新打开「造点啥.exe」，应用会自动准备')
    venv=safe_path(root,environment)
    python=venv/'Scripts/python.exe'
    env=dict(os.environ,UV_CACHE_DIR=str(root/'cache/uv'),UV_PYTHON_INSTALL_DIR=str(root/'runtime/python'),
             UV_PYTHON_BIN_DIR=str(root/'runtime/tools/python-bin'),UV_NO_MODIFY_PATH='1',
             PYTHONUTF8='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    if not python.is_file():
        emit('建立独立 Python 环境')
        run_command([executable,'venv','--python',profile.get('python','3.11.14'),'--managed-python',str(venv)],root,env,emit,cancelled)
    requirements=safe_path(root,profile['requirements'])
    command=[executable,'pip','install','--python',str(python),'--requirements',str(requirements),
             '--extra-index-url',profile.get('torchIndex','https://download.pytorch.org/whl/cu124'),'--index-strategy','unsafe-best-match']
    for url in profile.get('wheelIndexes',[]):command+=['--find-links',url]
    emit('安装固定版本依赖（首次会下载 CUDA / PyTorch，耗时取决于网络）')
    run_command(command,root,env,emit,cancelled)
    for entry in profile.get('postCommands',[]):
        command=[str(python)]+[str(root) if arg=='@root' else str(safe_path(root,arg[6:])) if arg.startswith('@root:') else arg for arg in entry]
        run_command(command,root,env,emit,cancelled)
    for entry in profile.get('verifyCommands',[]):
        command=[str(python)]+[str(root) if arg=='@root' else str(safe_path(root,arg[6:])) if arg.startswith('@root:') else arg for arg in entry]
        emit('校验模型运行入口与组件导入')
        run_command(command,root,env,emit,cancelled,timeout=180)
    emit('独立运行环境准备完成；继续执行安装校验')
