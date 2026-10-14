"""One MV-Adapter denoising batch to six coherent views. v0.7.0 / Unclecow."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[2]
ENGINE='mvadapter-i2mv-sdxl'
AZIMUTHS=[0,45,90,180,270,315]
VIEW_NAMES=['front','az045','left','back','right','az315']


def rooted(root,value):
    if not isinstance(value,str) or not value or '\0' in value:raise ValueError('多视角图片路径无效')
    path=Path(value);path=(root/path if not path.is_absolute() else path).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('多视角输入超出工厂目录')
    return path


def validate_request(value,root):
    if not isinstance(value,dict) or value.get('mode')!='multiview':raise ValueError('当前提供器只支持联合多视角模式')
    prompt=value.get('prompt','')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000 or '\0' in prompt:raise ValueError('请填写多视角对象描述')
    negative=value.get('negativePrompt','')
    if not isinstance(negative,str) or len(negative)>6000 or '\0' in negative:raise ValueError('反向描述无效')
    refs=value.get('imageReferences',[])
    if not isinstance(refs,list) or len(refs)!=1 or not isinstance(refs[0],dict):raise ValueError('图像联合多视角需要且仅接受一张参考图')
    image=rooted(root,refs[0].get('path'))
    if not image.is_file():raise ValueError('多视角参考图不存在')
    if refs[0].get('sha256') and hashlib.sha256(image.read_bytes()).hexdigest()!=refs[0]['sha256']:raise ValueError('多视角参考图已改变')
    from PIL import Image
    with Image.open(image) as opened:
        if opened.format not in {'PNG','JPEG','WEBP'} or max(opened.size)>8192:raise ValueError('多视角图片格式或尺寸不支持')
        opened.verify()
    for key in ('width','height'):
        if type(value.get(key,512)) is not int or value.get(key,512)!=512:raise ValueError('此低显存实验档固定为512×512')
    steps=value.get('steps',30);seed=value.get('seed',20261009);cfg=value.get('cfgScale',3.0)
    if type(steps) is not int or not 1<=steps<=50 or type(seed) is not int or not 0<=seed<=2147483647:raise ValueError('多视角步数或种子无效')
    if type(cfg) not in {int,float} or not math.isfinite(cfg) or not 1<=cfg<=10:raise ValueError('多视角CFG需为1至10')
    if value.get('transparentBackground'):raise ValueError('此联合视图配方不支持透明输出')
    return {'prompt':prompt.strip(),'negativePrompt':negative,'image':str(image),'width':512,'height':512,'steps':steps,'seed':seed,'cfgScale':cfg}


def pipeline_arguments(request):
    return {'prompt':request['prompt'],'height':request['height'],'width':request['width'],
            'num_inference_steps':request['steps'],'guidance_scale':request['cfgScale'],'num_images_per_prompt':6,
            'control_conditioning_scale':1.0,'reference_conditioning_scale':1.0,
            'negative_prompt':request['negativePrompt'],'cross_attention_kwargs':{'scale':1.0}}


def configure_memory(pipe,dtype):
    # cond_encoder is not registered in the inherited offload sequence. Keep it
    # explicit; preserve the trained custom joint attention processors on UNet.
    pipe.to(dtype=dtype)
    pipe.cond_encoder.to(device='cuda',dtype=dtype)
    pipe.enable_vae_slicing();pipe.enable_vae_tiling();pipe.enable_model_cpu_offload()


def offline():
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    def blocked(*_args,**_kwargs):raise RuntimeError('多视角推理禁止下载，请在模型与环境完成固定组件安装')
    socket.create_connection=blocked;socket.socket.connect=blocked


def imports(root):
    sys.path[:0]=[str(root/f'engines/{ENGINE}/src'),str(root/'scripts')]


def load_pipeline(root):
    import torch
    from diffusers import AutoencoderKL
    from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
    from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler
    base=root/f'engines/{ENGINE}/models/base'
    vae=AutoencoderKL.from_pretrained(str(root/f'engines/{ENGINE}/models/vae'),torch_dtype=torch.float16,local_files_only=True)
    pipe=MVAdapterI2MVSDXLPipeline.from_pretrained(str(base),vae=vae,torch_dtype=torch.float16,variant='fp16',
                                                add_watermarker=False,local_files_only=True)
    pipe.scheduler=ShiftSNRScheduler.from_scheduler(pipe.scheduler,shift_mode='interpolated',shift_scale=8.0)
    pipe.init_custom_adapter(num_views=6)
    pipe.load_custom_adapter(str(root/f'engines/{ENGINE}/models/adapter'),weight_name='mvadapter_i2mv_sdxl.safetensors',local_files_only=True)
    configure_memory(pipe,torch.float16)
    return pipe


def preprocess_reference(image):
    """Official i2mv subject fit: alpha foreground, 90% canvas, gray composite."""
    import numpy as np
    from PIL import Image
    rgba=np.array(image.convert('RGBA'));ys,xs=np.where(rgba[:,:,3]>0)
    if not len(xs):raise ValueError('多视角参考图完全透明')
    crop=rgba[max(ys.min()-1,0):min(ys.max()+1,rgba.shape[0]),max(xs.min()-1,0):min(xs.max()+1,rgba.shape[1])]
    h,w=crop.shape[:2];scale=512*.9/max(h,w)
    resized=np.array(Image.fromarray(crop).resize((max(1,int(w*scale)),max(1,int(h*scale)))))
    canvas=np.zeros((512,512,4),dtype=np.uint8);y=(512-resized.shape[0])//2;x=(512-resized.shape[1])//2
    canvas[y:y+resized.shape[0],x:x+resized.shape[1]]=resized
    normalized=canvas.astype(np.float32)/255.0
    rgb=normalized[:,:,:3]*normalized[:,:,3:4]+(1-normalized[:,:,3:4])*.5
    return Image.fromarray((rgb*255).clip(0,255).astype(np.uint8))


def publish_images(images,request,output):
    if len(images)!=6:raise ValueError('联合多视角必须返回完整6张图片')
    records=[]
    for index,(image,view,azimuth) in enumerate(zip(images,VIEW_NAMES,AZIMUTHS)):
        if image.size!=(request['width'],request['height']):raise ValueError('联合视图尺寸与请求不符')
        path=output/f'view-{index}-{view}.png';image.save(path,format='PNG')
        records.append({'path':str(path),'view':view,'azimuthDeg':azimuth,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return records


def run(root,request,output):
    import torch
    if not torch.cuda.is_available():raise RuntimeError('联合多视角需要CUDA，不会自动改用CPU或其他模型')
    offline();imports(root)
    output.mkdir(parents=True,exist_ok=True)
    if list(output.glob('view-*.png')):raise ValueError('多视角输出已存在，请使用新任务目录')
    from factory_image import prepare_image
    reference=preprocess_reference(prepare_image(request['image'],output/'work/input'))
    pipe=load_pipeline(root)
    from mvadapter.utils.mesh_utils.camera import get_orthogonal_camera
    from mvadapter.utils.geometry import get_plucker_embeds_from_cameras_ortho
    cameras=get_orthogonal_camera(elevation_deg=[0]*6,distance=[1.8]*6,left=-.55,right=.55,bottom=-.55,top=.55,
                                  azimuth_deg=[x-90 for x in AZIMUTHS],device='cuda')
    plucker=get_plucker_embeds_from_cameras_ortho(cameras.c2w,[1.1]*6,512)
    args=pipeline_arguments(request)
    args.update(control_image=((plucker+1)/2).clamp(0,1),reference_image=reference,
                generator=torch.Generator(device='cuda').manual_seed(request['seed']))
    torch.cuda.reset_peak_memory_stats()
    print('FACTORY_PHASE: multiview-joint-inference',flush=True)
    with torch.inference_mode():images=pipe(**args).images
    torch.cuda.synchronize();peak=torch.cuda.max_memory_allocated()
    if peak<=0:raise RuntimeError('本次联合生成未记录CUDA活动')
    outputs=publish_images(images,request,output)
    (output/'output-adapter.json').write_text(json.dumps({'engine':ENGINE,'version':'0.7.0','jointMultiView':True,
        'pipelineCalls':1,'outputCount':6,'azimuthsDeg':AZIMUTHS,'gpuInferenceExecuted':True,'peakVramBytes':peak,
        'memoryPolicy':'model-cpu-offload; cond-encoder-fp16-cuda; vae-slicing-tiling','outputs':outputs},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--request',type=Path);parser.add_argument('--output');parser.add_argument('--check',action='store_true')
    args=parser.parse_args();root=args.root.resolve()
    if args.check:
        offline();imports(root)
        import torch
        from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
        from mvadapter.utils.mesh_utils.camera import get_orthogonal_camera
        from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler
        if not torch.cuda.is_available():raise RuntimeError('CUDA未就绪')
        print(json.dumps({'imports':True,'cuda':True,'jointPipeline':True,'gpuInferenceExecuted':False}));return
    if not args.request or not args.output:parser.error('--request and --output required')
    request=validate_request(json.loads(args.request.read_text(encoding='utf-8-sig')),root)
    run(root,request,rooted(root,args.output))


if __name__=='__main__':main()
