"""Own a media invocation and validate outputs before its success receipt. v0.7.0."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from PIL import Image
from factory_capabilities import require_accepted_execution

def timestamp():return datetime.now(timezone.utc).isoformat()

def save(path,value):
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)

def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def inspect_outputs(folder,manifest,request,root=None):
    folder=Path(folder).resolve()
    values=[]
    video=manifest.get('kind')=='video'
    for path in sorted(folder.iterdir()):
        suffix=path.suffix.lower()
        if not path.is_file() or not path.resolve().is_relative_to(folder) or path.name.startswith(('input','prepared')):continue
        if suffix not in ({'.mp4','.webm','.mov'} if video else {'.png','.jpg','.jpeg','.webp'}):continue
        if path.stat().st_size==0:raise ValueError('生成文件为空')
        if video:
            if root is None:raise RuntimeError('视频输出必须经独立运行环境全帧解码')
            from factory_setup import safe_path
            runtime=manifest.get('runtime') or {}
            relative=runtime.get('python') or ((runtime.get('environment')+'/Scripts/python.exe') if runtime.get('environment') else '')
            if not relative:raise RuntimeError('视频运行环境缺少校验入口')
            python=safe_path(root,relative)
            if not python.is_file():raise RuntimeError('视频校验环境未部署完成，请在模型与环境重新安装')
            result=subprocess.run([str(python),str(Path(root)/'scripts/probe_media.py'),str(path),str(request['frames']+1)],
                capture_output=True,text=True,encoding='utf-8',timeout=180,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise ValueError('生成的视频无法完整解码：'+result.stderr[-700:])
            try:metadata=json.loads(result.stdout)
            except ValueError as error:raise ValueError('视频校验返回了无效结果') from error
            if metadata['frames']!=request['frames']:raise ValueError(f"生成帧数不符：请求{request['frames']}，实际{metadata['frames']}")
            if abs(metadata['fps']-request['fps'])>.1:raise ValueError('输出帧率与实际请求不一致')
        else:
            try:
                with Image.open(path) as image:
                    image.load()
                    metadata={'width':image.width,'height':image.height,'mode':image.mode}
                    if image.mode=='RGBA' and image.getchannel('A').getextrema()[1]==0:raise ValueError('生成图片完全透明')
            except (OSError,Image.DecompressionBombError) as error:raise ValueError('生成图片无法解码') from error
        if metadata['width']!=request['width'] or metadata['height']!=request['height']:
            raise ValueError(f"生成尺寸不符：请求{request['width']}×{request['height']}，实际{metadata['width']}×{metadata['height']}")
        values.append({'path':str(path),'bytes':path.stat().st_size,'sha256':file_hash(path),**metadata})
    if not values:raise ValueError('模型没有返回可用的'+('视频' if video else '图片'))
    count=(manifest.get('capabilities') or {}).get('outputCount')
    if request.get('mode')=='multiview' and count and len(values)!=count:raise ValueError('多视图结果数量不完整')
    return values

def run(root,engine,request_file,run_id):
    from factory_runtime import powershell_command
    from factory_setup import safe_path
    root=Path(root).resolve()
    if not re.fullmatch(r'[a-z0-9_-]+',engine) or not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{6,32}',run_id):raise ValueError('运行标识无效')
    folder=root/'engines'/engine
    manifest=json.loads((folder/'engine.json').read_text(encoding='utf-8-sig'))
    if manifest['id']!=engine or manifest.get('status') not in {'ready','installed'}:raise ValueError('模型未部署')
    entry=safe_path(folder,manifest.get('entrypoint','run.ps1'))
    request_path=Path(request_file).resolve()
    if not request_path.is_relative_to(root/'logs/visual-factory'):raise ValueError('请求文件不属于本应用队列')
    request=json.loads(request_path.read_text(encoding='utf-8'))
    require_accepted_execution(root, manifest, run_id, request_path, request)
    if request.get('engineId')!=engine:raise ValueError('媒体模型与请求不一致')
    # Verify provenance again at execution time, after any wait in the GPU queue.
    references=request.get('imageReferences',[])
    for item in references:
        path=Path(item['path']).resolve()
        if not path.is_relative_to(root/'inputs') or not path.is_file() or file_hash(path)!=item['sha256']:raise ValueError('生成前参考图已改变，请重新导入')
    for key in ('maskPath','controlImagePath','ipAdapterImagePath','referenceVideoPath','referenceAudioPath'):
        if request.get(key):
            path=Path(request[key]).resolve()
            if not path.is_relative_to(root/'inputs') or not path.is_file() or file_hash(path)!=request[key+'Sha256']:raise ValueError('生成前参考素材已改变，请重新导入')
    output=safe_path(root,'outputs/'+engine+'/'+run_id)
    if output.exists():raise ValueError('输出已经存在，请使用新的任务ID')
    output.mkdir(parents=True)
    copied=output/'request.json';save(copied,request)
    receipt={'factoryVersion':json.loads((root/'VERSION.json').read_text(encoding='utf-8'))['version'],
        'engine':engine,'engineVersion':manifest.get('version'),'runId':run_id,'kind':manifest['kind'],
        'inputMode':request['mode'],'status':'running','startedAt':timestamp(),'request':request,
        'inputs':[{'view':'front' if index==0 else 'reference','source':item['path'],'sha256':item['sha256']} for index,item in enumerate(references)],
        'gpuInferenceExecuted':False}
    receipt_file=output/'receipt.json';save(receipt_file,receipt)
    environment={**os.environ,'PYTHONUTF8':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1'}
    try:
        with (output/'run.log').open('wb') as log:
            command=[powershell_command(root),'-NoLogo','-NoProfile','-File',str(entry),'-RequestJson',str(copied),'-OutputDirectory',str(output)]
            completed=subprocess.run(command,cwd=root,env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if completed.returncode:raise RuntimeError(f'模型运行失败，退出码{completed.returncode}；请查看任务日志')
        receipt['outputs']=inspect_outputs(output,manifest,request,root)
        metadata_path=output/'output-adapter.json'
        if metadata_path.is_file():
            metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
            receipt['provider']=metadata
            receipt['gpuInferenceExecuted']=metadata.get('gpuInferenceExecuted') is True
        receipt['status']='generated-unreviewed'
        return receipt
    except Exception as error:
        receipt.update(status='failed',error=str(error));raise
    finally:
        receipt['finishedAt']=timestamp();save(receipt_file,receipt)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--engine',required=True)
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    try:run(args.root,args.engine,args.request,args.run_id)
    except Exception as error:print(str(error),file=sys.stderr);raise SystemExit(1)
