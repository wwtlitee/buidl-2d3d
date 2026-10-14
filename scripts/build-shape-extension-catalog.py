"""Maintain reviewed shape recipes from fixed metadata and sealed code. v0.7.0."""
from pathlib import Path
import hashlib
import json
import os
from urllib.parse import quote
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TEMP=Path(os.environ['TEMP'])/'factory-shape-sources-07'
META=json.loads((Path(os.environ['TEMP'])/'image3d-hf-metadata.json').read_text(encoding='utf-8'))
extra=json.loads((TEMP/'hunyuan2-metadata.json').read_text(encoding='utf-8'))
META['tencent/Hunyuan3D-2']={'revision':extra['sha'],'files':extra['siblings']}

def save(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def pin(repo,name,target):
    data=META[repo];f=next(x for x in data['files'] if x['rfilename']==name)
    result={'path':target,'size':f['size'],'url':f"https://huggingface.co/{repo}/resolve/{data['revision']}/{quote(name,safe='/')}",
        'mirrors':[f"https://hf-mirror.com/{repo}/resolve/{data['revision']}/{quote(name,safe='/')}"]}
    if f.get('lfs'):result['sha256']=f['lfs']['sha256']
    else:result['gitBlobSha1']=f['blobId']
    return result

def runtime_bundle():
    target=ROOT/'config/deployment-bundles/hunyuan-shape-0.7.0.zip'
    with zipfile.ZipFile(ROOT/'config/deployment-bundles/hunyuan3d-0.5.0.zip') as old,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as new:
        for name in old.namelist():
            if name.startswith('engines/hunyuan3d/src/'):
                new.writestr(name.replace('engines/hunyuan3d/src/','model-lab/hunyuan-shape/src/',1),old.read(name))
            elif name=='config/deployment-requirements/hunyuan3d.txt':
                new.writestr('config/deployment-requirements/hunyuan-shape.txt',old.read(name))
                (ROOT/'config/deployment-requirements/hunyuan-shape.txt').write_bytes(old.read(name))
    return {'path':target.relative_to(ROOT).as_posix(),'size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}

def main():
    bundle=runtime_bundle()
    models=[{'id':'hunyuan-shape-runtime-07','title':'Hunyuan几何共用环境 · 独立目录','kind':'utility','supportOnly':True,
        'deploymentProfile':'hunyuan-shape-runtime-07','files':[],
        'required':[{'path':'model-lab/hunyuan-shape/.venv/Scripts/python.exe'},{'path':'model-lab/hunyuan-shape/src/hy3dgen/shapegen/pipelines.py'}],
        'sourceUrl':'https://github.com/Tencent-Hunyuan/Hunyuan3D-2/tree/f8db63096c8282cb27354314d896feba5ba6ff8a',
        'license':{'label':'Tencent Hunyuan3D Community License','url':'https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/f8db63096c8282cb27354314d896feba5ba6ff8a/LICENSE','requiresAcceptance':True,
                   'notice':'固定源代码及依赖各有许可；独立环境保留原有Hunyuan组件。'},'validationStage':'researched'}]
    profiles=[{'id':'hunyuan-shape-runtime-07','python':'3.11.14','environment':'model-lab/hunyuan-shape/.venv',
        'requirements':'config/deployment-requirements/hunyuan-shape.txt','bundles':[bundle],
        'verifyCommands':[['-c','import torch,trimesh,diffusers; assert torch.cuda.is_available(); print("Shape runtime imports checked; no inference")']]}]
    for key,repo,sub,vae_repo,vae_sub,joint in [
        ('hunyuan3d-mini-turbo','tencent/Hunyuan3D-2mini','hunyuan3d-dit-v2-mini-turbo','tencent/Hunyuan3D-2mini','hunyuan3d-vae-v2-mini-turbo',False),
        ('hunyuan3d-mv-turbo','tencent/Hunyuan3D-2mv','hunyuan3d-dit-v2-mv-turbo','tencent/Hunyuan3D-2','hunyuan3d-vae-v2-0-turbo',True)]:
        base='model-lab/'+key+'/models'
        files=[pin(repo,sub+'/'+name,base+'/'+sub+'/'+name) for name in ['config.yaml','model.fp16.safetensors']]
        files += [pin(vae_repo,vae_sub+'/'+name,base+'/'+vae_sub+'/'+name) for name in ['config.yaml','model.fp16.safetensors']]
        models.append({'id':key,'engineId':key,'title':'Hunyuan3D 多视角 Turbo · 白模' if joint else 'Hunyuan3D Mini Turbo · 单图白模',
            'kind':'model','version':'0.7.0','appearance':'geometry','installable':True,'recommended':False,
            'description':'多角度联合生成一个白模，另行上色；FlashVDM加速档。' if joint else '一张图生成白模，另行上色；FlashVDM加速档。',
            'sourceUrl':'https://huggingface.co/'+repo,'dependencies':['hunyuan-shape-runtime-07','image-preparation'],
            'minimumVramMiB':6144,'minimumRamGiB':16,'extractReserveBytes':1024**3,
            'files':files,'required':[{'path':f['path'],'size':f['size']} for f in files]+[{'path':'engines/'+key+'/run.ps1'}],
            'adapters':[{'source':'scripts/providers/3d-run.ps1','target':'engines/'+key+'/run.ps1'}],
            'runtime':{'family':'hunyuan2','sourceDirectory':'model-lab/hunyuan-shape/src','python':'model-lab/hunyuan-shape/.venv/Scripts/python.exe',
                'modelDirectory':base,'subfolder':sub,'jointMultiview':joint,'flashVDM':True,'useSafetensors':True,
                'turboVae':{'directory':base,'subfolder':vae_sub},'revision':'f8db63096c8282cb27354314d896feba5ba6ff8a'},
            'capabilities':{'multiView':joint,'views':['left','back','right'] if joint else [],'maxReferenceImages':4 if joint else 1},
            'defaults':{'steps':5,'resolution':256,'cfgScale':1.01},
            'license':{'label':'Tencent Hunyuan3D Community License','url':'https://huggingface.co/'+repo+'/blob/'+META[repo]['revision']+'/LICENSE',
                'requiresAcceptance':True,'notice':'遵守对应腾讯模型及代码许可，白模不包含PBR贴图。'},
            'resourceProfile':{'tier':'experimental','label':'12 GB可试 · 加速档待实测','minVramGiB':6,'minRamGiB':16,'note':'少步采样不代表相同画质，实际速度需本机验证。'},'validationStage':'researched',
            'probes':[{'path':'model-lab/hunyuan-shape/.venv/Scripts/python.exe','args':['@root:scripts/providers/3d-run.py','--root','@root','--engine',key,'--check'],'timeout':90}]})
    # Geometry-only 2.1 source/weights; paint's much larger VRAM route is separate.
    key='hunyuan3d-21-shape';base='model-lab/hunyuan-shape21/models'
    source={'path':'cache/setup/hunyuan21-source-82920d64.zip','size':155825200,'sha256':'1c560ba93e2471305276f36170a7e3c3857f39b14313790533dbc06ab01ed2a3',
        'url':'https://codeload.github.com/Tencent-Hunyuan/Hunyuan3D-2.1/zip/82920d643c0dc2f7bfd7255f45f62d386edfe60c',
        'extractTo':'model-lab/hunyuan-shape21/src','stripPrefix':'Hunyuan3D-2.1-82920d643c0dc2f7bfd7255f45f62d386edfe60c/'}
    files=[source]+[pin('tencent/Hunyuan3D-2.1',sub+'/'+name,base+'/'+sub+'/'+name) for sub in ['hunyuan3d-dit-v2-1','hunyuan3d-vae-v2-1'] for name in ['config.yaml','model.fp16.ckpt']]
    profiles.append({'id':'hunyuan-shape21-07','python':'3.11.14','environment':'model-lab/hunyuan-shape21/.venv',
        'requirements':'config/deployment-requirements/hunyuan-shape.txt','bundles':[bundle],
        'verifyCommands':[['@root:scripts/providers/3d-run.py','--root','@root','--engine',key,'--check']]})
    models.append({'id':key,'engineId':key,'title':'Hunyuan3D 2.1 · 仅几何白模','kind':'model','version':'0.7.0','appearance':'geometry','installable':True,
        'description':'一张图生成白模；此项仅安装几何，未包含21GB显存的Paint2.1。','sourceUrl':'https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1',
        'dependencies':['image-preparation'],'deploymentProfile':'hunyuan-shape21-07','minimumVramMiB':10240,'minimumRamGiB':16,'extractReserveBytes':3*1024**3,
        'files':files,'required':[{'path':f['path'],'size':f['size']} for f in files if not f.get('extractTo')]+[{'path':'engines/'+key+'/run.ps1'},{'path':'model-lab/hunyuan-shape21/.venv/Scripts/python.exe'}],
        'adapters':[{'source':'scripts/providers/3d-run.ps1','target':'engines/'+key+'/run.ps1'}],
        'runtime':{'family':'hunyuan21','sourceDirectory':'model-lab/hunyuan-shape21/src','python':'model-lab/hunyuan-shape21/.venv/Scripts/python.exe',
            'modelDirectory':base,'subfolder':'hunyuan3d-dit-v2-1','jointMultiview':False,'useSafetensors':False,'revision':'82920d643c0dc2f7bfd7255f45f62d386edfe60c'},
        'capabilities':{'multiView':False,'views':[],'maxReferenceImages':1},'defaults':{'steps':30,'resolution':256,'cfgScale':5.5},
        'license':{'label':'Tencent Hunyuan3D 2.1 Community License','url':'https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/blob/82920d643c0dc2f7bfd7255f45f62d386edfe60c/LICENSE',
            'notice':'模型和源码许可需分别遵守；完整彩色PBR需要更高配置，不包含在此几何配方。','requiresAcceptance':True},
        'resourceProfile':{'tier':'experimental','label':'12 GB可试 · 仅白模','minVramGiB':10,'minRamGiB':16,'note':'官方10GB为几何模块，完整模型+贴图约29GB。'},'validationStage':'researched'})
    information=[
        ('hunyuan-omni-info','Hunyuan3D Omni · 形状约束','https://github.com/Tencent-Hunyuan/Hunyuan3D-Omni','图+点云/骨架等生成白模；专用输入与Windows适配尚未接入。','available'),
        ('mv-adapter-info','MV-Adapter · 网格条件上色（待接入）','https://github.com/huanngzh/MV-Adapter','单图联合六视角已单独接入；此项记录尚未接入的网格条件上色管线，不能安装或作为上色效果承诺。','available'),
        ('hi3dgen-info','Hi3DGen · 法线增强几何','https://github.com/bytedance/Hi3DGen','已发布；spconv/xformers专用环境及12GB路径尚未适配。','available'),
        ('triposf-info','TripoSF · mesh/点云重建','https://github.com/VAST-AI-Research/TripoSF','这是已有mesh/点云的重建，不是单张图直接生模型；输入管线尚未接入。','available'),
        ('sam3d-objects-info','SAM 3D Objects · 高配置','https://github.com/facebookresearch/sam-3d-objects','官方Linux与至少32GB显存，当前Windows12GB不提供安装配方。','available'),
        ('direct3d-info','Direct3D-S2 · 高配置','https://github.com/DreamTechAI/Direct3D-S2','推荐1024档需约24GB；作者不推荐10GB的512档。本批未接入。','available'),
        ('step1x3d-info','Step1X-3D · 高配置','https://github.com/stepfun-ai/Step1X-3D','全流程约27–29GB显存，当前12GB不提供全流程配方。','available'),
        ('hunyuan-buffalo-info','Hunyuan3D Buffalo · 跟踪','https://github.com/Tencent-Hunyuan/Hunyuan3D-Buffalo1.0','项目/论文已发布，尚未找到完整本地权重与推理代码。','unreleased'),
        ('hunyuan3d-cloud-info','Hunyuan3D 3.0/3.1 · 云API','https://intl.cloud.tencent.com/zh/document/api/1284/75540','只有云API路线，不能在本机下载这些模型权重。','cloud-only'),
        ('tripo-cloud-info','Tripo H3.1/P1/P2 · 云API','https://github.com/VAST-AI-Research/tripo-js-sdk','开源SDK不等于本地生成模型权重，本条仅资料入口。','cloud-only'),
        ('rodin-cloud-info','Rodin · 云API','https://docs.hyper3d.ai/en','未找到相应官方本地权重；本条仅资料入口。','cloud-only')]
    for key,title,url,note,status in information:
        models.append({'id':key,'title':title,'kind':'model','installable':False,'releaseStatus':status,'installStatus':'blocked',
            'description':note,'remedy':note,'sourceUrl':url,'files':[],'required':[],'validationStage':'researched'})
    path=ROOT/'config/image3d-models.json';data=json.loads(path.read_text(encoding='utf-8'));by={x['id']:x for x in data['models']};by.update({x['id']:x for x in models});data['models']=list(by.values());save(path,data)
    path=ROOT/'config/image3d-profiles.json';data=json.loads(path.read_text(encoding='utf-8'));by={x['id']:x for x in data['profiles']};by.update({x['id']:x for x in profiles});data['profiles']=list(by.values());save(path,data)
    print('Added shape recipes',len(models),'profiles',len(profiles))

if __name__=='__main__':main()
