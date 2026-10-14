"""Seal portable deployment recipes from the reviewed local baseline. v0.5.0.

Maintenance only: includes adapter/source/license files, never environments,
user inputs, credentials, logs or model weights. Generated bundles are hashed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'config/deployment-bundles'
EXTENSIONS={'.py','.ps1','.json','.yaml','.yml','.toml','.txt','.md','.pyd','.patch','.diff','.fbx'}
SKIP={'.git','.venv','__pycache__','weights','ckpt','cache','logs','log','outputs','output','checks','tests',
      'tmp','toolchain','sdk','cuda','build','builds','dist','node_modules','vendor-src','pdoc','.model-runtime','assets','examples','figures','images'}
PROFILES=[]
MODELS=[]


def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8-sig'))
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def weight(repo,revision,filename,target,size=None,sha=None):
    path=ROOT/target
    if not path.is_file():raise FileNotFoundError(target)
    if size is not None and path.stat().st_size!=size:raise ValueError('Size mismatch: '+target)
    size=path.stat().st_size
    if not sha or len(sha)!=64:sha=digest(path)
    filename=quote(filename,safe='/')
    return {'path':target,'size':size,'sha256':sha,
            'url':f'https://huggingface.co/{repo}/resolve/{revision}/{filename}',
            'mirrors':[f'https://hf-mirror.com/{repo}/resolve/{revision}/{filename}']}


def source_files(folder):
    base=ROOT/folder
    for current,dirs,files in os.walk(base):
        dirs[:]=[name for name in dirs if name not in SKIP and not name.startswith('.venv') and not (Path(current)==base and name=='models')]
        for name in files:
            path=Path(current)/name
            if Path(current)==base and (name.startswith(('diagnose_','compare_','check_','smoke_','test_','download','install_')) or name in {'readiness.json','installation.json','environment-check.json','provenance.json','source-manifest.json','provider-build-info.json'}):continue
            if folder=='texture-lab/hunyuan-paint' and path.parent==base/'config' and name not in {'model-source.json','requirements-lock.txt','source-patches.diff'}:continue
            if path.suffix.lower() in EXTENSIONS or name.lower().startswith(('license','notice','copying')):
                if path.stat().st_size<20*1024**2:
                    yield path


def source_bundle(profile_id,folders,extras=None,requirements=None):
    OUTPUT.mkdir(parents=True,exist_ok=True)
    archive=OUTPUT/f'{profile_id}-0.5.0.zip'
    written=set()
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for folder in folders:
            for path in source_files(folder):
                relative=path.relative_to(ROOT).as_posix()
                if relative in written or path.name=='engine.json':continue
                data=path.read_bytes()
                # Normalise local cache settings only in bundled launchers.
                if path.suffix=='.ps1':
                    text=data.decode('utf-8-sig')
                    text=re.sub(r"'D:\\2D3DFactory\\cache\\(huggingface|torch|uv)'",
                                lambda m:"(Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'cache/"+m[1]+"')",text)
                    data=text.encode('utf-8')
                if relative=='engines/triposr/src/tsr/models/tokenizers/image.py':
                    text=data.decode('utf-8')
                    text='from pathlib import Path\n'+text
                    text,count=re.subn(r'hf_hub_download\(\s*repo_id=self\.cfg\.pretrained_model_name_or_path,\s*filename=[\"\x27]config\.json[\"\x27],?\s*\)',
                                       'str(Path(__file__).resolve().parents[4] / "models/dino/config.json")',text)
                    if count!=1:raise ValueError('TripoSR Dino loading patch must match exactly one upstream call')
                    data=text.encode('utf-8')
                bundle.writestr(relative,data);written.add(relative)
        for target,source in (extras or {}).items():
            if isinstance(source,bytes):data=source
            else:data=(ROOT/source).read_bytes()
            if target not in written:bundle.writestr(target,data);written.add(target)
        if requirements:
            target=f'config/deployment-requirements/{profile_id}.txt'
            text=(ROOT/requirements).read_text(encoding='utf-8-sig')
            # Environment managers themselves are not model dependencies.
            text='\n'.join(line for line in text.splitlines() if not line.startswith(('pip==','setuptools==','wheel==')))+'\n'
            bundle.writestr(target,text.encode('utf-8'));written.add(target)
    return {'path':archive.relative_to(ROOT).as_posix(),'size':archive.stat().st_size,'sha256':digest(archive)},written


def register(profile_id,title,kind,folders,files,requirements=None,environment=None,dependencies=None,extras=None,probe=None,license_url=None):
    item,names=source_bundle(profile_id,folders,extras,requirements)
    profile={'id':profile_id,'bundles':[item],'python':'3.11.14'}
    if environment:
        profile.update(environment=environment,requirements=f'config/deployment-requirements/{profile_id}.txt')
    if profile_id=='mia-rig':
        profile.update(torchIndex='https://download.pytorch.org/whl/cu121',wheelIndexes=['https://data.pyg.org/whl/torch-2.1.0+cu121.html'])
    elif profile_id in {'unirig','partcrafter'}:
        profile['wheelIndexes']=['https://data.pyg.org/whl/torch-2.5.0+cu124.html']
    PROFILES.append(profile)
    manifest=read(f'engines/{profile_id}/engine.json')
    manifest={k:v for k,v in manifest.items() if k not in {'status','notes','factoryVersion'} and 'verification' not in k.lower() and 'verified' not in k.lower()}
    required=[{'path':item['path'],'size':item['size']} for item in files]
    required += [{'path':f'engines/{profile_id}/run.ps1'}]
    if environment:required += [{'path':environment+'/Scripts/python.exe'}]
    # Every program/ABI/config file in the sealed source bundle is required.
    # Keep required lightweight; model weights have full pinned download SHA.
    for name in sorted(names):
        if name.endswith(('.py','.pyd')) and ('/src/' in name or '/vendor/' in name):required.append({'path':name})
    model={'id':profile_id,'engineId':profile_id,'title':title,'kind':kind,'description':manifest.get('description') or manifest.get('title',title),
           'recommended':False,'minimumVramMiB':0 if profile_id=='basic-motion' else 12288,
           'minimumRamGiB':16,'extractReserveBytes':12*1024**3 if environment else 0,
           'sourceUrl':manifest.get('source') or license_url,'files':files,'required':required,
           'manifest':manifest,'deploymentProfile':profile_id,'dependencies':dependencies or [],
           'license':{'label':'官方模型与依赖许可','url':license_url or manifest.get('source'),
                      'requiresAcceptance':True,'notice':'安装会下载固定版本模型与独立 CUDA / Python 依赖。模型许可与依赖许可分别适用；先阅读官方说明。依赖下载另需约 3–6 GB，磁盘预留包含解压和环境空间。'},
           'remedy':'点击安装建立独立环境并校验组件。安装完成后首次实际生成仍需验收。'}
    if probe:
        probe['args']=[('@root:'+str((Path(probe['path']).parent / arg).as_posix())) if arg.endswith('.py') and not arg.startswith('-') else arg for arg in probe.get('args',[])]
        # Script arguments originate from the provider directory, not .venv/Scripts.
        for n,arg in enumerate(probe['args']):
            if arg.startswith('@root:'):
                provider=Path(probe['path']).parents[2]
                filename=Path(arg[6:]).name
                if profile_id=='hunyuan3d-mv':provider=Path('engines/hunyuan3d-mv')
                probe['args'][n]='@root:'+(provider/filename).as_posix()
        probe['timeout']=180 if profile_id=='hunyuan-paint' else 60
        model['probes']=[probe]
    MODELS.append(model)


def main():
    public=read('config/public-model-download-results.json')
    for engine,title,repo in [('hunyuan3d','单张图片 · Hunyuan3D mini','tencent/Hunyuan3D-2mini'),
                               ('triposr','快速几何 · TripoSR','stabilityai/TripoSR')]:
        files=[]
        for receipt in public:
            if receipt['repo']!=repo:continue
            path=Path(receipt['path']).relative_to(ROOT).as_posix()
            filename=path.split('/models/',1)[1]
            files.append(weight(repo,receipt['revision'],filename,path,receipt['size'],receipt.get('hash')))
        extras={f'engines/{engine}/run.ps1':ROOT/f'engines/{engine}/run.ps1'}
        if engine=='triposr':
            metadata=read('engines/triposr/dino-source-metadata.json')
            cache=ROOT/'engines/triposr/cache/huggingface/hub/models--facebook--dino-vitb16/snapshots'/metadata['sha']/'config.json'
            if not cache.is_file():
                cache=next((ROOT/'engines/triposr/cache').rglob('config.json'))
            files.append(weight('facebook/dino-vitb16',metadata['sha'],'config.json','engines/triposr/models/dino/config.json') if (ROOT/'engines/triposr/models/dino/config.json').exists() else
                         {'path':'engines/triposr/models/dino/config.json','size':cache.stat().st_size,'sha256':digest(cache),
                          'url':f"https://huggingface.co/facebook/dino-vitb16/resolve/{metadata['sha']}/config.json"})
        register(engine,title,'model',[f'engines/{engine}'],files,f'engines/{engine}/requirements.lock.txt',f'engines/{engine}/.venv',
                 ['image-preparation'],extras,{'path':f'engines/{engine}/.venv/Scripts/python.exe','args':['run.py','--check'],'requiredFlags':['true']},f'https://huggingface.co/{repo}')
        MODELS[-1]['minimumVramMiB']=6144
        MODELS[-1]['description']='生成几何模型。6 GB 显存来自官方单图配置建议；本工具的既有实测使用 12 GB 显卡。'
        if engine=='triposr':
            for component in MODELS[-1]['required']:
                if component['path']=='engines/triposr/models/dino/config.json':
                    component['alternatives']=[cache.relative_to(ROOT).as_posix()]
    source=read('config/multiview-model-source.json')
    files=[weight(source['repo'],source['revision'],item['file'],'engines/hunyuan3d-mv/models/'+item['file'],item['size'],item['sha256']) for item in source['receipts']]
    register('hunyuan3d-mv','多角度图片 · Hunyuan3D','model',['engines/hunyuan3d-mv'],files,dependencies=['hunyuan3d'],
             probe={'path':'engines/hunyuan3d/.venv/Scripts/python.exe','args':['../hunyuan3d-mv/run.py','--check'],'requiredFlags':['true']},license_url='https://huggingface.co/tencent/Hunyuan3D-2mv')
    for engine,title in [('triposg','细节几何 · TripoSG'),('partcrafter','分部件模型 · PartCrafter')]:
        files=[]
        receipts=read(f'engines/{engine}/logs/download-manifest.json')
        for item in receipts:
            repo=item['repo'];directory='RMBG-1.4' if repo=='briaai/RMBG-1.4' else 'TripoSG' if engine=='triposg' else 'PartCrafter'
            files.append(weight(repo,item['revision'],item['file'],f'engines/{engine}/models/{directory}/'+item['file'],item['bytes'],item['sha256']))
        register(engine,title,'model',[f'engines/{engine}'],files,f'engines/{engine}/requirements.lock.txt',f'engines/{engine}/.venv',
                 probe={'path':f'engines/{engine}/.venv/Scripts/python.exe','args':['run.py','--check'],'requiredFlags':['pass']},
                 license_url='https://huggingface.co/VAST-AI/TripoSG' if engine=='triposg' else 'https://huggingface.co/wgsxm/PartCrafter')
        MODELS[-1]['minimumVramMiB']=8192
        MODELS[-1]['description']='从图片重建几何。8 GB 显存来自官方要求；本工具的既有实测使用 12 GB 显卡。'
    source=read('animation-lab/mia/weights-manifest.json')
    files=[weight(source['repository'],source['revision'],i['path'],'animation-lab/mia/source/'+i['path'],i['size'],i['sha256']) for i in source['files']]
    register('mia-rig','人物绑骨 · MIA','rig',['animation-lab/mia','engines/mia-rig'],files,'animation-lab/mia/requirements-lock.txt','animation-lab/mia/.venv',
             probe={'path':'animation-lab/mia/.venv/Scripts/python.exe','args':['mia_cli.py','--check'],'requiredFlags':[]},license_url='https://huggingface.co/jasongzy/Make-It-Animatable')
    register('mia-motion','人物动作 · MIA','animation',['engines/mia-motion'],[],dependencies=['mia-rig'],license_url='https://huggingface.co/jasongzy/Make-It-Animatable')
    source=read('animation-lab/unirig/weights.manifest.json')
    files=[weight(source['repository'],source['revision'],i['file'],'animation-lab/unirig/models/'+i['file'],i['bytes'],i['sha256']) for i in source['files']]
    files.append(weight('facebook/opt-350m',source['opt_config_revision'],'config.json','animation-lab/unirig/models/opt-350m/config.json'))
    register('unirig','通用绑骨 · UniRig','rig',['animation-lab/unirig','engines/unirig'],files,'animation-lab/unirig/requirements.lock.txt','animation-lab/unirig/.venv',
             probe={'path':'animation-lab/unirig/.venv/Scripts/python.exe','args':['-c','import torch,bpy,spconv; assert torch.cuda.is_available(); print("CUDA/imports checked")'],'requiredFlags':['checked']},license_url='https://huggingface.co/VAST-AI/UniRig')
    source=read('animation-lab/riganything/provenance.json')
    ckpt=source['checkpoint']
    target='animation-lab/riganything/ckpt/'+ckpt['rfilename']
    if not (ROOT/target).is_file():target='animation-lab/riganything/ckpt/riganything.pt'
    files=[weight(source['model_repository'],source['model_revision'],ckpt['rfilename'],target,ckpt['size'],ckpt['lfs']['sha256'])]
    register('riganything','自由骨架 · RigAnything','rig',['animation-lab/riganything','engines/riganything'],files,'animation-lab/riganything/requirements-runtime.lock.txt','animation-lab/riganything/.venv',
             probe={'path':'animation-lab/riganything/.venv/Scripts/python.exe','args':['runner.py','--check'],'requiredFlags':[]},license_url='https://huggingface.co/Isabellaliu/RigAnything')
    source=read('texture-lab/hunyuan-paint/config/model-source.json')
    files=[weight(source['repo'],source['revision'],i['path'],'texture-lab/hunyuan-paint/models/'+i['path'],i['bytes'],i['sha256']) for i in source['files']]
    register('hunyuan-paint','多图上色 · Hunyuan Paint','texture',['texture-lab/hunyuan-paint','engines/hunyuan-paint'],files,
             'texture-lab/hunyuan-paint/config/requirements-lock.txt','texture-lab/hunyuan-paint/.venv',
             probe={'path':'texture-lab/hunyuan-paint/.venv/Scripts/python.exe','args':['paint_cli.py','--check'],'requiredFlags':[]},license_url='https://huggingface.co/tencent/Hunyuan3D-2')
    register('basic-motion','关节动作 · 内置','animation',['engines/basic-motion'],[],license_url='https://github.com/KhronosGroup/glTF')
    MODELS[-1]['minimumRamGiB']=4
    for name,data in [('python-profiles.json',{'version':'0.5.0','author':'Unclecow','profiles':PROFILES}),('python-models.json',{'version':'0.5.0','author':'Unclecow','models':MODELS})]:
        (ROOT/'config'/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'profiles':len(PROFILES),'models':len(MODELS),'bundlesMiB':round(sum(p['bundles'][0]['size'] for p in PROFILES)/1024**2,2)}))


if __name__=='__main__':main()
