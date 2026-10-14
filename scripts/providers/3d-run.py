"""Offline shape extensions, with explicit single/joint-view inputs. v0.7.0 / Unclecow."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from PIL import Image

def rooted(root,value):
    p=Path(value);p=(Path(root)/p if not p.is_absolute() else p).resolve()
    if not p.is_relative_to(Path(root).resolve()):raise ValueError('建模输入或组件路径超出工具目录')
    return p

def validate_request(value,recipe,root):
    if not isinstance(value,dict):raise ValueError('建模请求需为对象')
    refs=value.get('imageReferences',[])
    if not isinstance(refs,list) or not refs:raise ValueError('需要一张主图')
    joint=recipe.get('runtime',{}).get('jointMultiview') is True
    if not joint and len(refs)!=1:raise ValueError('此工具是单图建模，不接受多图联合输入')
    if len(refs)>recipe.get('capabilities',{}).get('maxReferenceImages',4):raise ValueError('建模参考数量超过限制')
    views={}
    for index,item in enumerate(refs):
        if not isinstance(item,dict):raise ValueError('建模参考格式无效')
        view=item.get('view','front' if index==0 else '')
        if view not in {'front','left','back','right'} or view in views:raise ValueError('视角名称无效或重复')
        path=rooted(root,item.get('path',''))
        if not path.is_file():raise ValueError('建模图片不存在')
        if item.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('建模参考图已经改变')
        with Image.open(path) as image:image.verify()
        views[view]=str(path)
    if 'front' not in views:raise ValueError('多图建模需要front主图')
    defaults=recipe.get('defaults',{})
    result={'views':views}
    for key,minimum,maximum,default in [('steps',1,100,5),('resolution',128,512,256),('seed',0,2147483647,20261009),('chunks',1000,32000,8000)]:
        number=value.get(key,defaults.get(key,default))
        if type(number) is not int or not minimum<=number<=maximum:raise ValueError(key+'参数超出范围')
        result[key]=number
    return result

def load_recipe(root,engine):
    d=json.loads((root/'config/image3d-models.json').read_text(encoding='utf-8'))
    recipe=next((m for m in d['models'] if m.get('engineId',m['id'])==engine),None)
    if not recipe or recipe.get('installable') is False:raise ValueError('此建模工具没有可执行配方')
    return recipe

def load_pipeline(root,recipe):
    import torch
    if not torch.cuda.is_available():raise RuntimeError('未检测到可用CUDA；不会自动换模型或CPU')
    runtime=recipe['runtime']
    sys.path.insert(0,str(rooted(root,runtime['sourceDirectory'])))
    if runtime['family']=='hunyuan2':
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
    elif runtime['family']=='hunyuan21':
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
    else:raise ValueError('未注册的建模模型系列')
    pipeline=Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(str(rooted(root,runtime['modelDirectory'])),
        subfolder=runtime['subfolder'],use_safetensors=runtime.get('useSafetensors',True),variant='fp16',device='cpu',dtype=torch.float16)
    if runtime.get('turboVae'):
        from hy3dgen.shapegen.models import ShapeVAE
        pipeline.vae=ShapeVAE.from_pretrained(str(rooted(root,runtime['turboVae']['directory'])),
            subfolder=runtime['turboVae']['subfolder'],use_safetensors=True,device='cpu')
    if runtime.get('flashVDM'):
        pipeline.enable_flashvdm(replace_vae=False,mc_algo='mc')
    pipeline.components={name:getattr(pipeline,name) for name in ('conditioner','model','vae')}
    for component in pipeline.components.values():component.eval()
    pipeline.enable_model_cpu_offload(device='cuda')
    pipeline.device=pipeline._execution_device
    return pipeline

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--engine',required=True)
    parser.add_argument('--input');parser.add_argument('--input-set');parser.add_argument('--output');parser.add_argument('--check',action='store_true')
    args=parser.parse_args();root=args.root.resolve();recipe=load_recipe(root,args.engine)
    runtime=recipe['runtime'];sys.path[:0]=[str(rooted(root,runtime['sourceDirectory'])),str(root/'scripts')]
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    os.environ['PATH']=str(root/'runtime/cuda12')+os.pathsep+os.environ.get('PATH','')
    if args.check:
        import torch
        if runtime['family']=='hunyuan2':from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
        elif runtime['family']=='hunyuan21':from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
        else:raise ValueError('建模系列未注册')
        if not torch.cuda.is_available():raise RuntimeError('CUDA依赖检测失败')
        print(json.dumps({'imports':True,'cuda':True,'gpuInferenceExecuted':False}));return
    if not args.input or not args.output:parser.error('--input and --output required')
    views=json.loads(Path(args.input_set).read_text(encoding='utf-8-sig')) if args.input_set else {'front':args.input}
    if views.get('front')!=args.input:raise ValueError('主视角与输入图片不一致')
    request=validate_request({'imageReferences':[{'path':path,'view':view} for view,path in views.items()]},recipe,root)
    output=rooted(root,args.output);output.mkdir(parents=True,exist_ok=True)
    from factory_image import prepare_image
    images={view:prepare_image(path,output/'work'/view) for view,path in request['views'].items()}
    image=images if runtime.get('jointMultiview') else images['front']
    pipeline=load_pipeline(root,recipe)
    import torch
    with torch.inference_mode():
        mesh=pipeline(image=image,num_inference_steps=request['steps'],octree_resolution=request['resolution'],
            num_chunks=request['chunks'],generator=torch.Generator('cuda').manual_seed(request['seed']),
            output_type='trimesh',guidance_scale=recipe.get('defaults',{}).get('cfgScale',1.01))[0]
    if mesh is None or not len(mesh.vertices) or not len(mesh.faces):raise RuntimeError('建模结果为空')
    mesh.export(output/'model.glb')
    (output/'output-adapter.json').write_text(json.dumps({'engine':args.engine,'family':runtime['family'],
        'sourceRevision':runtime.get('revision'),'gpuInferenceExecuted':True,'appearance':'geometry','request':request},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
