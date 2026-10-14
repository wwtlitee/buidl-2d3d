"""Persistent one-click prompt -> image -> selected models. v0.6.0 / Unclecow."""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
import re
import uuid
from urllib.parse import unquote
from factory_capabilities import require_creation

SUCCESS='generated-unreviewed'
TERMINAL={'completed','failed','cancelled','interrupted'}


class PipelineController:
    def __init__(self,factory):
        self.factory=factory
        self.records=factory.data.setdefault('pipelines',[])
        if not isinstance(self.records,list):raise ValueError('一键流程记录格式错误，原记录已保留。')

    def _record(self,identifier):
        record=next((p for p in self.records if p['id']==identifier),None)
        if record is None:raise ValueError('一键流程不存在。')
        return record

    def _spec(self,payload):
        if not isinstance(payload,dict):raise ValueError('一键流程需要 JSON 对象。')
        models=payload.get('modelEngineIds')
        if not isinstance(models,list) or not 1<=len(models)<=14 or any(not isinstance(x,str) for x in models) or len(set(models))!=len(models):
            raise ValueError('请选择至少一个不重复的建模模型。')
        spec={key:payload[key] for key in ['prompt','imageEngineId','weightVariant','seed','negativePrompt'] if key in payload}
        image=next((e for e in self.factory.engines() if e['id']==payload.get('imageEngineId')),None)
        require_creation(image, payload)
        if not image or image.get('kind') != 'image':
            raise ValueError('一键流程的生图阶段只接受图片模型')
        engines = {engine['id']:engine for engine in self.factory.engines()}
        for identifier in models:
            require_creation(engines.get(identifier))
        defaults=(image or {}).get('defaults') or {}
        spec.update(modelEngineIds=models[:],width=payload.get('width',defaults.get('width',512)),
            height=payload.get('height',defaults.get('height',512)),steps=payload.get('steps',defaults.get('steps',20)))
        if isinstance(spec.get('prompt'),str):spec['prompt']=spec['prompt'].strip()
        return spec

    def create(self,payload,*,retry_of=None):
        from factory_queue import now
        spec=self._spec(payload)
        token=payload.get('clientRequestId')
        if token is not None and (not isinstance(token,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',token)):
            raise ValueError('请求标识格式错误。')
        with self.factory.lock:
            if token:
                existing=next((p for p in self.records if p.get('clientRequestId')==token),None)
                if existing:
                    if existing['inputSpec']!=spec:raise ValueError('同一请求标识不能用于不同提示词或模型。')
                    return self.public_record(existing)
            self.factory._assert_no_installation()
            engines={item['id']:item for item in self.factory.engines()}
            for name in spec['modelEngineIds']:
                model=engines.get(name)
                if not model or model['kind']!='model' or model['status'] not in {'ready','installed'}:
                    raise ValueError('所选建模模型尚不可用：'+name)
                if model['multiView']:raise ValueError('一键流程只生成一张主图，请选择单图建模模型；多视角请在图片建模页补图。')
            image_payload={key:value for key,value in spec.items() if key!='modelEngineIds'}
            image_payload['engineId']=image_payload.pop('imageEngineId',None)
            original_batches=len(self.factory.data['batches'])
            job=self.factory.create_image_job(image_payload,persist=False)
            record={'id':uuid.uuid4().hex,'name':spec['prompt'][:60],'prompt':spec['prompt'],
                    'inputSpec':copy.deepcopy(spec),'clientRequestId':token,'status':'queued','phase':'image',
                    'phaseLabel':'等待生成图片','imageJobId':job['id'],'imageEngineId':job['engine'],
                    'weightVariant':job['request'].get('weightVariant'),'engineIds':spec['modelEngineIds'][:],
                    'imageRequest':copy.deepcopy(job['request']),'modelBatchId':None,'mainImageId':None,
                    'createdAt':now(),'factoryVersion':self.factory.version(),'error':None}
            if retry_of:record['retryOf']=retry_of
            self.records.append(record)
            try:self.factory._save()
            except Exception:
                self.records.remove(record)
                del self.factory.data['batches'][original_batches:]
                raise
            self.factory.wake.set()
            return self.public_record(record)

    def advance(self):
        from factory_queue import now
        with self.factory.lock:
            if self.factory.stopping.is_set():return
            jobs={job['id']:job for job in self.factory._jobs()}
            changed=False
            for record in self.records:
                if record['status'] in TERMINAL:continue
                image=jobs.get(record['imageJobId'])
                if not image:
                    record.update(status='failed',error='生图任务记录缺失。',finishedAt=now());changed=True;continue
                if not record.get('modelBatchId'):
                    if image['status'] in {'failed','cancelled','interrupted','deleted'}:
                        status='failed' if image['status'] in {'failed','deleted'} else image['status']
                        record.update(status=status,phase='image',phaseLabel='生图未完成，建模没有启动',error=image.get('error'),finishedAt=now());changed=True
                    elif image['status']==SUCCESS:
                        original_batches=len(self.factory.data['batches'])
                        original_images=set(self.factory.data['images'])
                        try:
                            assets=self.factory.assets(self.factory.run_root(image))
                            asset=next((a for a in assets if a['kind']=='image'),None)
                            if not asset:raise ValueError('生图没有可使用的图片，建模未启动。')
                            source=(self.factory.root/unquote(asset['url'][len('/files/'):])).resolve()
                            checksum=hashlib.sha256(source.read_bytes()).hexdigest()
                            imported=self.factory.import_output_image(asset['url'],persist=False)
                            batch=self.factory.create_batch({'name':record['name'],'objects':[{'name':record['name'],'mainImageId':imported['id'],'auxiliaryImages':[]}],
                                                             'engineIds':record['engineIds'][:]},persist=False)
                            record.update(status='running',phase='model',phaseLabel='图片已生成，等待生成模型',
                                          modelBatchId=batch['id'],mainImageId=imported['id'],imageUrl=asset['url'],imageSha256=checksum)
                            self.factory._save()
                            self.factory.wake.set()
                            changed=False
                            continue
                        except Exception as error:
                            del self.factory.data['batches'][original_batches:]
                            for key in set(self.factory.data['images'])-original_images:self.factory.data['images'].pop(key)
                            record.update(modelBatchId=None,mainImageId=None)
                            record.update(status='failed',phase='model',phaseLabel='建模准备失败，没有更换模型',error=str(error),finishedAt=now())
                        changed=True
                    elif image['status']=='running':
                        if record['status']!='running':record.update(status='running',phaseLabel='正在生成图片');changed=True
                else:
                    batch=next((b for b in self.factory.data['batches'] if b['id']==record['modelBatchId']),None)
                    model_jobs=batch['jobs'] if batch else []
                    statuses=[j['status'] for j in model_jobs]
                    if not model_jobs:
                        record.update(status='failed',error='建模任务记录缺失。',finishedAt=now());changed=True
                    elif all(s==SUCCESS for s in statuses):
                        record.update(status='completed',phase='completed',phaseLabel='图片和模型都已生成',finishedAt=now());changed=True
                    elif all(s in {SUCCESS,'failed','cancelled','interrupted','deleted'} for s in statuses):
                        status='interrupted' if 'interrupted' in statuses else 'cancelled' if all(s=='cancelled' for s in statuses) else 'failed'
                        errors=[j.get('error') for j in model_jobs if j.get('error')]
                        record.update(status=status,phase='model',phaseLabel='部分模型未完成，请查看结果后重试',error='；'.join(errors) or '建模未全部完成。',finishedAt=now());changed=True
            if changed:self.factory._save()

    def cancel(self,identifier):
        from factory_queue import now
        with self.factory.lock:
            record=self._record(identifier)
            if record['status'] in TERMINAL:return self.public_record(record)
            if record.get('modelBatchId'):self.factory.cancel_batch(record['modelBatchId'])
            else:self.factory.cancel_job(record['imageJobId'])
            record.update(status='cancelled',phaseLabel='流程已取消，已有图片和模型保留',finishedAt=now())
            self.factory._save()
            return self.public_record(record)

    def retry(self,identifier):
        with self.factory.lock:
            original=self._record(identifier)
            if original['status'] not in {'failed','cancelled','interrupted'}:raise ValueError('只有失败、取消或中断的流程可以重试。')
            active=next((p for p in reversed(self.records) if p.get('retryOf')==identifier and p['status'] not in TERMINAL),None)
            if active:return self.public_record(active)
            payload=copy.deepcopy(original['inputSpec'])
            payload.update(original['imageRequest'])
            payload['imageEngineId']=original['imageEngineId']
            payload['modelEngineIds']=original['engineIds'][:]
            payload.pop('clientRequestId',None)
            return self.create(payload,retry_of=identifier)

    def pause_all(self):
        from factory_queue import now
        for record in self.records:
            if record['status'] not in TERMINAL:
                batch=next((b for b in self.factory.data['batches'] if b['id']==record.get('modelBatchId')),None)
                if batch and batch['jobs'] and all(j['status']==SUCCESS for j in batch['jobs']):
                    record.update(status='completed',phase='completed',phaseLabel='图片和模型都已生成',finishedAt=now())
                else:record.update(status='interrupted',phaseLabel='应用已关闭，流程暂停',error='重新打开后，手动重试继续。',finishedAt=now())

    def public_record(self,record,jobs=None):
        public={key:copy.deepcopy(value) for key,value in record.items() if key not in {'inputSpec','imageRequest'}}
        jobs=jobs if jobs is not None else self.factory._jobs()
        image=next((j for j in jobs if j['id']==record['imageJobId']),None)
        if image:
            public['imageJob']=copy.deepcopy(image)
            if record['phase']=='image':public['progress']=image.get('progress')
            if record['status'] not in TERMINAL and record['phase']=='image' and image['status']=='running':
                public.update(status='running',phaseLabel=image.get('phase') or '正在生成图片')
        batch=next((b for b in self.factory.data['batches'] if b['id']==record.get('modelBatchId')),None)
        identifiers={j['id'] for j in batch['jobs']} if batch else set()
        public['modelJobs']=[copy.deepcopy(j) for j in jobs if j['id'] in identifiers]
        active=next((j for j in public['modelJobs'] if j['status']=='running'),None)
        if record['status'] not in TERMINAL and active:
            public.update(status='running',phaseLabel=active.get('phase') or '正在生成模型',progress=active.get('progress'))
        if batch:public['modelBatchId']=batch['id']
        return public

    def public(self,jobs):
        return [self.public_record(p,jobs) for p in reversed(self.records)]
