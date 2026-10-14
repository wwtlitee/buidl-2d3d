"""Fixed single-reference MV-Adapter joint six-view recipe. v0.7.0 / Unclecow."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def save(path,value):
    target=ROOT/path;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    lock=json.loads((ROOT/'scripts/providers/multiview-source-lock.json').read_text(encoding='utf-8'))
    engine='mvadapter-i2mv-sdxl'
    runtime={'type':'diffusers','family':engine,'python':f'engines/{engine}/.venv/Scripts/python.exe',
             'environment':f'engines/{engine}/.venv','sourceDirectory':f'engines/{engine}/src',
             'modelDirectory':f'engines/{engine}/models','revision':lock['sourceRevision'],
             'adapterRevision':lock['modelRevision'],'jointMultiView':True,'outputCount':6,
             'azimuthsDeg':[0,45,90,180,270,315],'memoryPolicy':'model-cpu-offload/vae-slicing-tiling/cond-encoder-fp16-cuda'}
    caps={'modes':['multiview'],'maxReferenceImages':1,'maxInputImages':1,'inputTypes':['image'],
          'multiView':True,'jointMultiView':True,'outputCount':6,'requiresReference':True,'requiresPrompt':True,
          'requiresSquare':True,'allowedSizes':[[512,512]],'sizeMultiple':64,'maxWidth':512,'maxHeight':512,
          'maxPixels':512*512,'maxSteps':50,'supportsTransparency':False,'supportsNegativePrompt':True}
    defaults={'width':512,'height':512,'steps':30,'cfgScale':3.0,'seed':20261009}
    required=[]
    for f in lock['files']:
        entry={'path':f['path']}
        if not f['path'].endswith(('mesh_utils/__init__.py','mesh_utils/camera.py','mvadapter/utils/__init__.py')):entry['size']=f['size']
        required.append(entry)
    required += [{'path':f'engines/{engine}/.venv/Scripts/python.exe'},{'path':f'engines/{engine}/run.ps1'}]
    model={'id':engine,'engineId':engine,'title':'单图联合六视角 · MV-Adapter SDXL 512实验档','kind':'image','version':'0.7.0',
           'mediaAdapter':True,'recommended':False,'installable':True,'gpuVerified':False,'validationStage':'researched',
           'description':'一张对象参考图加描述，单次联合去噪生成6个固定视角；CPU卸载和VAE分块实验档，12GB实际内存与耗时尚待验证。',
           'sourceUrl':'https://github.com/huanngzh/MV-Adapter','runtime':runtime,'capabilities':caps,'defaults':defaults,
           'appearance':{'category':'multiview','badge':'Joint6/512'},'files':lock['files'],'required':required,
           'minimumVramMiB':12288,'minimumRamGiB':32,'extractReserveBytes':6*1024**3,
           'dependencies':['image-preparation'],'deploymentProfile':engine,
           'adapters':[{'source':'scripts/providers/multiview-run.ps1','target':f'engines/{engine}/run.ps1'}],
           'manifest':{'version':'0.7.0','entrypoint':'run.ps1','mediaAdapter':True,'runtime':runtime,
                       'capabilities':caps,'defaults':defaults,'source':'https://github.com/huanngzh/MV-Adapter'},
           'resourceProfile':{'targetGpu':'RTX3060 12GB','officialOriginalVramGiB':14,'defaultResolution':'512x512',
                              'precision':'FP16','cpuOffload':True,'vaeSlicing':True,'vaeTiling':True,'measured':False,
                              'notice':'官方i2mv原配置约14GB；本512CPU卸载档仅供试跑，未承诺12GB能够直接运行原精度/原分辨率。'},
           'license':{'label':'MV-Adapter 与 SDXL 底模及VAE许可','url':'https://huggingface.co/huanngzh/mv-adapter',
                      'requiresAcceptance':True,'notice':'MV-Adapter代码与权重、SDXL底模、VAE各适用其固定来源许可，保留源码LICENSE。多视角一致性和12GB性能需首次真实生成验收。'},
           'remedy':'在应用安装独立环境和固定组件；先用512默认档生成6视角短测。OOM或缺组件时明确失败，不自动换模型。',
           'probes':[{'path':runtime['python'],'args':['@root:scripts/providers/multiview-run.py','--check'],
                      'requiredFlags':['"gpuInferenceExecuted": false'],'timeout':180}]}
    profile={'id':engine,'version':'0.7.0','python':'3.11.14','environment':runtime['environment'],
             'requirements':'config/deployment-requirements/multiview.txt','torchIndex':'https://download.pytorch.org/whl/cu124',
             'postCommands':[['@root:scripts/providers/multiview-setup.py']],
             'verifyCommands':[['@root:scripts/providers/multiview-run.py','--check']]}
    save('config/multiview-models.json',{'version':'0.7.0','author':'Unclecow','models':[model]})
    save('config/multiview-profiles.json',{'version':'0.7.0','author':'Unclecow','profiles':[profile]})
    print('Wrote 1 joint six-view provider; input is one image + prompt; other catalogs untouched.')


if __name__=='__main__':main()
