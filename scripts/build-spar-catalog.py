"""Write the separate fixed SPAR3D catalog; no downloads. v0.7.0 / Unclecow."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.7.0'


def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8'))


def write(path,value):
    target=ROOT/path;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    lock=read('scripts/providers/spar-source-lock.json')
    toolkit=read('texture-lab/hunyuan-paint/config/cuda-source.json')
    sdk=read('texture-lab/hunyuan-paint/config/sdk-source.json')
    tools=[]
    for item in toolkit['files']:
        if item['name'] not in {'cuda_nvcc','cuda_cudart','cuda_cccl'}:continue
        basename=Path(item['relative_path']).name
        tools.append({'path':'cache/setup/spar-toolchain/'+basename,'url':'https://developer.download.nvidia.com/compute/cuda/redist/'+item['relative_path'],
                      'size':int(item['size']),'sha256':item['sha256'],'extractTo':'runtime/toolchains/spar3d/cuda-12.4',
                      'stripPrefix':basename.removesuffix('.zip')+'/'})
    for item in sdk['files']:
        tools.append({'path':'cache/setup/spar-toolchain/'+item['package']+'.'+item['version']+'.nupkg',
                      'url':item['url'],'size':item['bytes'],'sha256':item['sha256'],
                      'extractTo':'runtime/toolchains/spar3d/windows-sdk/'+item['package']})
    utility={'id':'spar-build-tools','engineId':'spar-build-tools','title':'SPAR 私有 CUDA12.4 / Windows SDK 构建组件','kind':'utility','version':VERSION,
             'description':'仅下载官方编译工具和头文件；VS2022 C++ 是外部前置，先检测再下载，不自动安装或修改系统。',
             'files':tools,'required':[{'path':'runtime/toolchains/spar3d/cuda-12.4/bin/nvcc.exe'},
                                       {'path':'runtime/toolchains/spar3d/cuda-12.4/lib/x64/cudart_static.lib'},
                                       {'path':'runtime/toolchains/spar3d/windows-sdk/microsoft.windows.sdk.cpp/c/bin/10.0.26100.0/x64/rc.exe'},
                                       {'path':'runtime/toolchains/spar3d/windows-sdk/microsoft.windows.sdk.cpp/c/Include/10.0.26100.0/ucrt/stddef.h'}],
             'sourceUrl':'https://developer.download.nvidia.com/compute/cuda/redist/redistrib_12.4.1.json',
             'minimumVramMiB':0,'minimumRamGiB':8,'extractReserveBytes':3*1024**3,
             'prerequisites':[{'probe':'msvc','remedy':'请先安装 Visual Studio 2022 C++ Build Tools；程序不会自动安装系统组件。'}],
             'license':{'label':'NVIDIA CUDA 与 Microsoft Windows SDK 官方许可','url':'https://developer.nvidia.com/cuda-toolkit',
                        'requiresAcceptance':True,'notice':'仅在程序私有目录解压 CUDA/SDK；VS2022 C++ 工具链由用户维护。源码/模型许可另行适用。'},
             'validationStage':'researched','probes':[{'path':'runtime/toolchains/spar3d/cuda-12.4/bin/nvcc.exe','args':['--version'],'requiredFlags':['release 12.4'],'timeout':30}]}
    runtime={'type':'spar3d','family':'spar3d','python':'engines/spar3d/.venv/Scripts/python.exe','sourceDirectory':'engines/spar3d/src',
             'modelDirectory':'engines/spar3d/models','nativeDirectory':'engines/spar3d/native','vendorDirectory':'engines/spar3d/vendor',
             'jointMultiview':False,'lowVramMode':True,'revision':lock['sourceRevision'],'modelRevision':lock['modelRevision']}
    caps={'modes':['model'],'maxReferenceImages':1,'maxInputImages':1,'multiView':False,'geometry':True,'texturedOutput':True}
    model={'id':'spar3d','engineId':'spar3d','title':'单图显色 UV 建模 · SPAR3D · Powered by Stability AI','kind':'model','version':VERSION,
           'description':'官方单图点云条件建模与材质纹理烘焙；Windows 实验支持，安装会编译两个私有扩展，实际生成尚待本机验收。',
           'recommended':False,'mediaAdapter':False,'appearance':'texture','capabilities':caps,'defaults':{'textureResolution':1024},
           'runtime':runtime,'installable':True,'authRequired':True,'gated':True,'gpuVerified':False,'validationStage':'researched',
           'minimumVramMiB':7168,'minimumRamGiB':16,'extractReserveBytes':6*1024**3,
           'sourceUrl':'https://github.com/Stability-AI/stable-point-aware-3d','files':lock['files'],
           'required':[{'path':x['path'],'size':x['size']} for x in lock['files'] if not x.get('extractTo')]+[
               {'path':'engines/spar3d/src/spar3d/system.py'},{'path':'engines/spar3d/.venv/Scripts/python.exe'},
               {'path':'engines/spar3d/native/texture_baker/_C.cp311-win_amd64.pyd'},
               {'path':'engines/spar3d/native/uv_unwrapper/_C.cp311-win_amd64.pyd'},
               {'path':'engines/spar3d/models/factory-config.json'},{'path':'engines/spar3d/NOTICE.txt'},{'path':'engines/spar3d/run.ps1'}],
           'dependencies':['spar-build-tools','image-preparation'],'deploymentProfile':'spar3d',
           'prerequisites':utility['prerequisites'],
           'adapters':[{'source':'scripts/providers/spar-run.ps1','target':'engines/spar3d/run.ps1'}],
           'manifest':{'version':VERSION,'entrypoint':'run.ps1','kind':'model','appearance':'texture','runtime':runtime,'capabilities':caps,
                       'source':'https://github.com/Stability-AI/stable-point-aware-3d','multiView':False},
           'license':{'label':'Stability AI Community / DINOv2 Apache-2.0 / AlphaCLIP与CLIP MIT',
                      'url':'https://huggingface.co/stabilityai/stable-point-aware-3d','requiresAcceptance':True,
                      'notice':'Powered by Stability AI。SPAR 权重与config需要先接受HF受限模型许可并提供读取令牌。固定Community许可要求商用登记，年收入超过100万美元需另行授权；再分发保留许可与Notice，展示Powered by Stability AI。具体适用以下载的LICENSE.md为准，辅助编码器及SDK适用各自许可。Windows为官方实验支持，本批未执行GPU生成。'},
           'resourceProfile':{'targetGpu':'RTX 3060 12GB','lowVramMode':True,'officialLowVramGiB':7,'measured':False},
           'remedy':'先配置官方HF授权并安装VS2022 C++；应用安装私有环境、固定辅助权重并编译扩展。安装后首次生成需验收。',
           'probes':[{'path':runtime['python'],'args':['@root:scripts/providers/spar-run.py','--check'],
                      'requiredFlags':['"gpuInferenceExecuted": false'],'timeout':180}]}
    profile={'id':'spar3d','version':VERSION,'python':'3.11.14','environment':'engines/spar3d/.venv',
             'requirements':'config/deployment-requirements/spar3d.txt','torchIndex':'https://download.pytorch.org/whl/cu124',
             'postCommands':[['@root:scripts/providers/spar-setup.py']],
             'verifyCommands':[['@root:scripts/providers/spar-run.py','--check']]}
    write('config/spar-models.json',{'version':VERSION,'author':'Unclecow','models':[utility,model]})
    write('config/spar-profiles.json',{'version':VERSION,'author':'Unclecow','profiles':[profile]})
    print('Wrote 1 SPAR3D model + 1 private toolchain utility; other catalogs untouched.')


if __name__=='__main__':main()
