"""Validated image editing/video jobs on the existing serial queue. v0.7.0 / Unclecow."""
from __future__ import annotations
import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
from factory_capabilities import creation_allowed, require_creation, RETIRED_MODES

MODES={'txt2img','img2img','edit','inpaint','control','multiview','txt2video','img2video','reference-video','model','texture'}
INPUT_FIELDS={'inputImageIds','maskImageId','controlImageId','ipAdapterImageId','referenceVideoId','referenceAudioId','sourceModelUrl'}
PRIVATE_FIELDS={'imageReferences','maskPath','controlImagePath','ipAdapterImagePath','referenceVideoPath','referenceAudioPath','sourceModel','outputDir','runtime','command','engine','kind'}


def public_engines(factory):
    installed={x['id']:x for x in factory.creation_engines()}
    result={}
    for model in factory.setup.catalog.get('models',[]):
        if not creation_allowed(model):continue
        if model.get('kind') not in {'image','video'} or model.get('supportOnly') or model.get('weightVariant'):continue
        engine_id=model.get('engineId',model['id'])
        entry=factory.setup._public_model(model,factory.setup.hardware())
        item={key:copy.deepcopy(entry.get(key)) for key in ('title','kind','status','capabilities','defaults','resourceProfile','validationStage','releaseStatus','description','sourceUrl','license','appearance')}
        item.update(id=engine_id,setupModelId=model['id'])
        if engine_id in installed:item.update(copy.deepcopy(installed[engine_id]))
        capabilities=item.get('capabilities') or {}
        if not capabilities.get('modes'):
            capabilities={**capabilities,'modes':['txt2img'] if item['kind']=='image' else []}
        item['capabilities']={**capabilities, 'modes':[mode for mode in capabilities.get('modes',[]) if mode not in RETIRED_MODES]}
        item['creationEnabled']=True
        result[engine_id]=item
    for engine_id,item in installed.items():
        if item['kind'] not in {'image','video'}:continue
        result[engine_id]={**result.get(engine_id,{}),**copy.deepcopy(item),'setupModelId':result.get(engine_id,{}).get('setupModelId',engine_id)}
        result[engine_id]['capabilities']=item.get('capabilities') or {'modes':['txt2img'] if item['kind']=='image' else []}
        result[engine_id]['capabilities']={**result[engine_id]['capabilities'], 'modes':[mode for mode in result[engine_id]['capabilities'].get('modes',[]) if mode not in RETIRED_MODES]}
        result[engine_id]['creationEnabled']=True
    return list(result.values())


def _integer(value,default,name,minimum,maximum):
    value=default if value is None else value
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError(f'{name}需为{minimum}至{maximum}之间的整数')
    return value


def _reference(factory,key):
    from factory_queue import safe_child
    if not isinstance(key,str):raise ValueError('参考图ID无效')
    image=factory._image(key)
    path=safe_child(factory.root,image['path'])
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if image.get('sha256')!=digest:raise ValueError('参考图片内容已经改变，请重新导入')
    return {'id':key,'path':str(path),'sha256':digest}


def validate_request(factory,payload):
    if not isinstance(payload,dict):raise ValueError('媒体请求需为JSON对象')
    if PRIVATE_FIELDS.intersection(payload):raise ValueError('参考路径和运行参数由应用管理，请选择已导入的素材')
    engine=next((x for x in factory.engines() if x['id']==payload.get('engineId')),None)
    require_creation(engine,payload)
    if not engine or engine['status'] not in {'installed','ready'}:raise ValueError('工具尚未部署，请先在模型与环境完成安装')
    capability=engine.get('capabilities') or {}
    modes=capability.get('modes') or (['txt2img'] if engine['kind']=='image' else [])
    mode=payload.get('mode',modes[0] if modes else '')
    require_creation({'mode':mode})
    if not isinstance(mode,str) or mode not in MODES or mode not in modes:raise ValueError('所选模型不支持这个输入模式')
    prompt=payload.get('prompt','')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000 or '\0' in prompt:raise ValueError('请填写1至6000字的描述')
    negative=payload.get('negativePrompt','')
    if not isinstance(negative,str) or len(negative)>6000 or '\0' in negative:raise ValueError('反向描述无效')
    if negative and capability.get('supportsNegativePrompt') is False:raise ValueError('此模型不支持反向描述，请清空该字段')
    defaults=engine.get('defaults') or {}
    video=engine['kind']=='video'
    width=_integer(payload.get('width'),defaults.get('width',512),'宽度',128 if video else 256,2048)
    height=_integer(payload.get('height'),defaults.get('height',512),'高度',128 if video else 256,2048)
    multiple=capability.get('sizeMultiple',32)
    if type(multiple) is not int or multiple<=0 or width%multiple or height%multiple:raise ValueError(f'尺寸需为{multiple}的倍数')
    if width>capability.get('maxWidth',2048) or height>capability.get('maxHeight',2048):raise ValueError('宽高超过此模型运行档的限制')
    if capability.get('requiresSquare') and width!=height:raise ValueError('此模型的联合视图需要正方形尺寸')
    if capability.get('allowedSizes') and [width,height] not in capability['allowedSizes']:raise ValueError('此实验运行档只支持安装卡中的推荐尺寸')
    if width*height>capability.get('maxPixels',2048*2048 if not video else 1920*1080):raise ValueError('尺寸超过此运行档的限制')
    request={'engineId':engine['id'],'mode':mode,'prompt':prompt.strip(),'negativePrompt':negative,'width':width,'height':height,
        'steps':_integer(payload.get('steps'),defaults.get('steps',20),'采样步数',1,capability.get('maxSteps',100)),
        'seed':_integer(payload.get('seed'),defaults.get('seed',20261009),'随机种子',0,2147483647)}
    if capability.get('allowedSteps') and request['steps'] not in capability['allowedSteps']:raise ValueError('此模型的蒸馏运行档使用固定步数，请使用推荐参数')
    if 'weightVariant' in payload or 'weightVariants' in engine:
        request['weightVariant']=factory._request_weight_variant(engine,payload)
    strength=payload.get('strength',defaults.get('strength',.7))
    if type(strength) not in {int,float} or not math.isfinite(strength) or not 0<=strength<=1:raise ValueError('重绘强度需为0至1')
    request['strength']=strength
    for key,maximum,fallback in (('cfgScale',20,6),('controlStrength',2,.8),('ipAdapterStrength',2,.8)):
        value=payload.get(key,defaults.get(key,fallback))
        if type(value) not in {int,float} or not math.isfinite(value) or not 0<=value<=maximum:raise ValueError(key+'参数无效')
        request[key]=value
    transparent=payload.get('transparentBackground',False)
    if type(transparent) is not bool or transparent and not capability.get('supportsTransparency'):raise ValueError('此模型不支持指定的透明输出模式')
    request['transparentBackground']=transparent
    ids=payload.get('inputImageIds',[])
    if not isinstance(ids,list) or len(ids)!=len(set(ids)) or len(ids)>capability.get('maxInputImages',capability.get('maxReferenceImages',2)):
        raise ValueError('参考图片数量无效或超过模型限制')
    if mode in {'img2img','edit','inpaint','img2video'} and not ids:raise ValueError('这个模式需要先导入参考图片')
    if capability.get('requiresReference') and not ids:raise ValueError('此联合多视角模型需要先导入主图')
    if mode in {'txt2img','txt2video'} and ids:raise ValueError('文字模式不接收参考图片，请切换图像模式')
    request['inputImageIds']=ids[:]
    request['imageReferences']=[_reference(factory,key) for key in ids]
    for field,path_name in (('maskImageId','maskPath'),('controlImageId','controlImagePath'),('ipAdapterImageId','ipAdapterImagePath')):
        if payload.get(field):
            reference=_reference(factory,payload[field]);request[field]=reference['id'];request[path_name]=reference['path']
            request[path_name+'Sha256']=reference['sha256']
    if mode=='inpaint' and not request.get('maskPath'):raise ValueError('局部重绘需要导入蒙版')
    if mode=='control' and not (request.get('controlImagePath') or request.get('ipAdapterImagePath')):raise ValueError('受控生成需要控制图片或外观参考图')
    for field,path_name,kind in (('referenceVideoId','referenceVideoPath','video'),('referenceAudioId','referenceAudioPath','audio')):
        if payload.get(field):
            if kind not in capability.get('inputTypes',[]):raise ValueError('此模型不支持该种参考素材')
            record=factory.data.get('mediaInputs',{}).get(payload[field])
            if not record or record['kind']!=kind:raise ValueError('参考素材不存在，请重新导入')
            from factory_queue import safe_child
            path=safe_child(factory.root,record['path'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('参考素材已改变，请重新导入')
            request[field]=record['id'];request[path_name]=str(path);request[path_name+'Sha256']=record['sha256']
    if mode=='reference-video' and not (ids or request.get('referenceVideoPath')):raise ValueError('参考视频模式需要图片或视频参考')
    if payload.get('sourceModelUrl'):
        from factory_postprocess import checked_model
        source=checked_model(factory.root,payload['sourceModelUrl'])
        request['sourceModelUrl']=payload['sourceModelUrl'];request['sourceModel']={**source,'path':str(factory.root/source['path'])}
    if video:
        frames=_integer(payload.get('frames'),defaults.get('frames',49),'帧数',1,1000)
        rule=capability.get('frameRule') or {}
        if frames<rule.get('min',1) or frames>rule.get('max',1000) or frames%rule.get('multiple',1)!=rule.get('offset',0)%rule.get('multiple',1) or rule.get('allowed') and frames not in rule['allowed']:
            raise ValueError('帧数不符合此模型的合法帧网格，请使用推荐参数')
        fps=payload.get('fps',defaults.get('fps',24))
        if type(fps) not in {int,float} or not math.isfinite(fps) or not 1<=fps<=60:raise ValueError('帧率需为1至60')
        if capability.get('fps') and fps not in capability['fps']:raise ValueError('此模型不支持选定帧率')
        request.update(frames=frames,fps=fps)
    return engine,request


def create_job(factory,payload,persist=True,retry_of=None):
    from factory_queue import now
    with factory.lock:
        factory._assert_no_installation()
        engine,request=validate_request(factory,payload)
        key=payload.get('clientRequestId')
        if key is not None and (not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}',key)):raise ValueError('提交ID无效')
        signature=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if key and not retry_of:
            previous=next((j for j in factory._jobs() if j.get('clientRequestId')==key),None)
            if previous:
                if previous.get('requestSignature')!=signature:raise ValueError('同一提交ID不能对应不同输入')
                return copy.deepcopy(previous)
        batch_id=uuid.uuid4().hex
        obj={'id':uuid.uuid4().hex,'name':request['prompt'][:80],'mainImageId':None,'auxiliaryImages':[]}
        job=factory._new_job(batch_id,obj,engine,retry_of)
        job.update(kind=engine['kind'],inputMode=request['mode'],viewImages={},request=request,mediaJob=True,
                   clientRequestId=key,requestSignature=signature)
        batch={'id':batch_id,'name':'视频生成' if engine['kind']=='video' else '图像创作','kind':engine['kind'],
            'createdAt':now(),'factoryVersion':factory.version(),'objects':[],'engineIds':[engine['id']],'jobs':[job]}
        factory.data['batches'].append(batch)
        try:
            if persist:factory._save()
        except Exception:
            factory.data['batches'].remove(batch);raise
        if persist:factory.wake.set()
        return copy.deepcopy(job)


def upload_input(factory,data,name):
    from factory_queue import safe_child,write_json
    if not isinstance(data,bytes) or not data or len(data)>256*1024*1024:raise ValueError('参考媒体需为1至256MB')
    suffix=Path(str(name)).suffix.lower()
    if suffix not in {'.mp4','.mov','.webm','.mp3','.wav','.flac','.m4a','.ogg'}:raise ValueError('参考媒体格式不受支持')
    kind='video' if suffix in {'.mp4','.mov','.webm'} else 'audio'
    valid=(suffix in {'.mp4','.mov','.m4a'} and data[4:8]==b'ftyp') or (suffix=='.webm' and data[:4]==b'\x1aE\xdf\xa3') or (suffix=='.wav' and data[:4]==b'RIFF' and data[8:12]==b'WAVE') or (suffix=='.flac' and data[:4]==b'fLaC') or (suffix=='.ogg' and data[:4]==b'OggS') or (suffix=='.mp3' and (data[:3]==b'ID3' or len(data)>1 and data[0]==255 and data[1]&224==224))
    if not valid:raise ValueError('媒体文件头与所选格式不符')
    checksum=hashlib.sha256(data).hexdigest();key=checksum[:32]
    path=safe_child(factory.root,f'inputs/media/{checksum}{suffix}')
    with factory.lock:
        existing=factory.data.setdefault('mediaInputs',{}).get(key)
        if existing:return {'media':copy.deepcopy(existing)|{'url':'/files/'+existing['path']}}
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_bytes(data)
        record={'id':key,'kind':kind,'name':Path(str(name)).name[:120],'path':path.relative_to(factory.root).as_posix(),'sha256':checksum,'bytes':len(data)}
        factory.data['mediaInputs'][key]=record
        try:factory._save()
        except Exception:factory.data['mediaInputs'].pop(key,None);raise
        return {'media':copy.deepcopy(record)|{'url':'/files/'+record['path']}}
