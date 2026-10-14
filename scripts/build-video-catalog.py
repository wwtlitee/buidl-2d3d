"""Pinned WanGP video install recipes. v0.7.0 / Unclecow."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
TMP=Path(__import__('tempfile').gettempdir())/'factory-video-research-20261009'
REV='6479db36bdc2619a904a852bba9c2d78e1a83f82'
SRC=TMP/('Wan2GP-'+REV)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:7897'}))
PIN={}; CACHE={}
SOURCE_LOCK=ROOT/'scripts/providers/video-source-lock.json'
LOCK_DATA=json.loads(SOURCE_LOCK.read_text(encoding='utf-8')) if SOURCE_LOCK.is_file() else None
if LOCK_DATA:
    CACHE={(x['repo'],x['path'].removeprefix('video-lab/wangp/models/')):x for x in LOCK_DATA['files']}

def fetch(url):
    last=None
    for _ in range(3):
        try:
            return opener.open(urllib.request.Request(url,headers={'User-Agent':'build-something-source-audit/0.7.0'}),timeout=35).read()
        except Exception as error:last=error
    raise last

def repo(repo):
    if LOCK_DATA:
        return LOCK_DATA['repositories'][repo]
    local=TMP/('metadata-'+repo.replace('/','--')+'.json')
    if local.is_file():return json.loads(local.read_text(encoding='utf-8'))
    d=json.loads(fetch('https://huggingface.co/api/models/'+repo+'?blobs=true'))
    local.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8')
    return d

repos=['DeepBeepMeep/MiniMax-H3','DeepBeepMeep/LTX-2','DeepBeepMeep/Wan2.1','DeepBeepMeep/Wan2.2','DeepBeepMeep/LTX_Video','DeepBeepMeep/Qwen_image','DeepBeepMeep/HunyuanVideo','DeepBeepMeep/HunyuanVideo1.5','DeepBeepMeep/Kandinsky5']
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    for name,meta in zip(repos,pool.map(repo,repos)):
        PIN[name]=meta
        print(name,meta['sha'],flush=True)

def weight(repo,filename):
    key=(repo,filename)
    if key in CACHE:return CACHE[key]
    meta=PIN[repo]; revision=meta['sha']
    item=next((x for x in meta['siblings'] if x['rfilename']==filename),None)
    if item is None:raise RuntimeError('Missing HF file '+repo+'/'+filename)
    url='https://huggingface.co/'+repo+'/resolve/'+revision+'/'+quote(filename,safe='/')
    checksum=(item.get('lfs') or {}).get('sha256')
    if not checksum:
        if item['size']>40*1024**2:raise RuntimeError('Cannot download model weights for audit')
        local=TMP/'small-files'/repo/filename;local.parent.mkdir(parents=True,exist_ok=True)
        if not local.is_file():local.write_bytes(fetch(url))
        data=local.read_bytes()
        if len(data)!=item['size']:raise RuntimeError('Small source file length mismatch')
        checksum=hashlib.sha256(data).hexdigest()
    result={'path':'video-lab/wangp/models/'+filename,'size':item['size'],'sha256':checksum,'url':url,
            'mirrors':[url.replace('huggingface.co','hf-mirror.com')], 'repo':repo,'revision':revision}
    CACHE[key]=result;return result

def files(repo,names):return [(repo,name) for name in names]
wan_shared=files('DeepBeepMeep/Wan2.1',['umt5-xxl/'+name for name in ['special_tokens_map.json','spiece.model','tokenizer.json','tokenizer_config.json','models_t5_umt5-xxl-enc-quanto_int8.safetensors']])
wan_other=files('DeepBeepMeep/Wan2.1',['xlm-roberta-large/'+name for name in ['models_clip_open-clip-xlm-roberta-large-vit-huge-14-bf16.safetensors','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json']]+['Wan2.1_VAE.safetensors','Wan2.1_VAE_upscale2x_imageonly_real_v1.safetensors'])
qwen_shared=files('DeepBeepMeep/Qwen_image',['qwen_vae.safetensors','qwen_vae_config.json']+['Qwen2.5-VL-7B-Instruct/'+name for name in ['merges.txt','tokenizer_config.json','config.json','vocab.json','video_preprocessor_config.json','preprocessor_config.json','chat_template.json','Qwen2.5-VL-7B-Instruct_quanto_bf16_int8.safetensors']])
h3_shared=files('DeepBeepMeep/MiniMax-H3',['MiniMax-H3-video_vae_fp16.safetensors','MiniMax-H3-audio_vae_fp32.safetensors','minimax_h3/minimax_h3_latent_upscaler_3d_bf16.safetensors']+['Qwen3-VL-32B-Instruct/'+name for name in ['config.json','tokenizer.json','tokenizer_config.json','preprocessor_config.json','vocab.json','qwen3vl-32B-MiniMax-H3-Q4_K_M.gguf']])
ltx25_shared=files('DeepBeepMeep/LTX-2',['ltx-2.5-spatial-upscaler-x2-1.0_bf16.safetensors','ltx-2.5-temporal-upscaler-x2-1.0_bf16.safetensors','ltx-2.5-22b_video_vae_bf16.safetensors','ltx-2.5-22b_diffusion_video_vae_bf16.safetensors','ltx-2.5-22b_audio_vae_bf16.safetensors','ltx-2.5-22b_vocoder_bf16.safetensors','ltx-2.5-22b_text_embedding_projection_bf16.safetensors','ltx-2.5-22b_video_embeddings_connector_int8_convrot.safetensors','ltx-2.5-22b_audio_embeddings_connector_int8_convrot.safetensors']+['gemma4-12b-ltx-v1/'+name for name in ['config.json','chat_template.jinja','tokenizer.json','tokenizer_config.json','gemma4-12b-ltx-v1_int8_convrot.safetensors']])
ltxv_shared=files('DeepBeepMeep/LTX_Video',['ltxv_0.9.7_VAE.safetensors','ltxv_0.9.7_spatial_upscaler.safetensors','ltxv_scheduler.json']+['T5_xxl_1.1/'+name for name in ['added_tokens.json','special_tokens_map.json','spiece.model','tokenizer_config.json','T5_xxl_1.1_enc_quanto_bf16_int8.safetensors']])
k5_shared=qwen_shared+files('DeepBeepMeep/HunyuanVideo',['hunyuan_video_VAE_fp32.safetensors','hunyuan_video_VAE_config.json']+['clip_vit_large_patch14/'+name for name in ['text_config.json','merges.txt','model.safetensors','preprocessor_config.json','special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.json']])
hy15_shared=qwen_shared+files('DeepBeepMeep/HunyuanVideo1.5',['Glyph-SDXL-v2/color_idx.json','Glyph-SDXL-v2/multilingual_10-lang_idx.json','Glyph-SDXL-v2/byt5-small/config.json','Glyph-SDXL-v2/byt5-small/model.safetensors','Glyph-SDXL-v2/byt5-small/byt5_model.safetensors','siglip_vision_model/model.safetensors','siglip_vision_model/config.json','siglip_vision_model/preprocessor_config.json','hunyuan_video_1_5_VAE_fp32.safetensors','hunyuan_video_1_5_VAE.json'])

source={'path':'cache/downloads/wangp-'+REV+'.zip','url':'https://codeload.github.com/deepbeepmeep/Wan2GP/zip/'+REV,'size':61784271,'sha256':'019b10a0d83dae9e72cb66493b0443f0b6fbdc7a101c31b7e76a270170018df5','extractTo':'video-lab/wangp/src','stripPrefix':'Wan2GP-'+REV+'/'}
specs=[
 ('h3-fl2va','MiniMax H3 FL2VA Pruned 20B · 首尾帧与音视频','minimax_h3_fl2va_pruned','DeepBeepMeep/MiniMax-H3','MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors',h3_shared,['txt2video','img2video'],107,24,30,17,5,107,192,640,384,'https://huggingface.co/MiniMaxAI/MiniMax-H3'),
 ('h3-ref2va','MiniMax H3 Ref2VA Pruned 20B · 多参考音视频','minimax_h3_ref2va_pruned','DeepBeepMeep/MiniMax-H3','MiniMax-H3-Ref2VA-pruned_rank8_int8_convrot.safetensors',h3_shared,['txt2video','img2video','reference-video'],107,24,30,17,5,107,192,640,384,'https://huggingface.co/MiniMaxAI/MiniMax-H3'),
 ('ltx25-distilled','LTX-2.5 · 8 步音视频','ltx2_25_22B_distilled','DeepBeepMeep/LTX-2','ltx-2.5-22b-distilled_diffusion_model_int8_convrot.safetensors',ltx25_shared,['txt2video','img2video'],97,24,8,8,1,17,129,640,384,'https://huggingface.co/Lightricks/LTX-2.5'),
 ('wan22-5b','Wan2.2 · 5B 文生 / 图生视频','ti2v_2_2','DeepBeepMeep/Wan2.2','wan2.2_text2video_5B_quanto_mbf16_int8.safetensors',wan_shared+files('DeepBeepMeep/Wan2.2',['Wan2.2_VAE.safetensors']),['txt2video','img2video'],49,24,30,4,1,5,121,640,384,'https://github.com/Wan-Video/Wan2.2'),
 ('wan22-14b-t2v','Wan2.2 · 14B 文字生视频','t2v_2_2','DeepBeepMeep/Wan2.2','wan2.2_text2video_14B_high_quanto_mbf16_int8.safetensors',wan_shared+wan_other+files('DeepBeepMeep/Wan2.2',['wan2.2_text2video_14B_low_quanto_mbf16_int8.safetensors']),['txt2video'],49,16,30,4,1,5,81,640,384,'https://github.com/Wan-Video/Wan2.2'),
 ('wan22-14b-i2v','Wan2.2 · 14B 图片生视频','i2v_2_2','DeepBeepMeep/Wan2.2','wan2.2_image2video_14B_high_quanto_mbf16_int8.safetensors',wan_shared+wan_other+files('DeepBeepMeep/Wan2.2',['wan2.2_image2video_14B_low_quanto_mbf16_int8.safetensors']),['img2video'],49,16,30,4,1,5,81,640,384,'https://github.com/Wan-Video/Wan2.2'),
 ('ltxv098','LTX Video 0.9.8 · 6 步短片','ltxv_distilled','DeepBeepMeep/LTX_Video','ltxv_0.9.8_13B_distilled_quanto_bf16_int8.safetensors',ltxv_shared,['txt2video','img2video'],97,30,6,8,1,17,129,640,384,'https://github.com/Lightricks/LTX-Video'),
 ('kandinsky5-lite','Kandinsky 5 Lite · 16 步视频','k5_lite_t2v_5s_distil','DeepBeepMeep/Kandinsky5','kandinsky5lite_t2v_distilled16steps_5s_bf16.safetensors',k5_shared,['txt2video'],81,24,16,4,1,5,121,640,384,'https://github.com/kandinskylab/kandinsky-5'),
 ('wan21-13b','Wan2.1 · 1.3B 轻量视频','t2v_1.3B','DeepBeepMeep/Wan2.1','wan2.1_text2video_1.3B_mbf16.safetensors',wan_shared+wan_other,['txt2video'],49,16,30,4,1,5,81,640,384,'https://github.com/Wan-Video/Wan2.1'),
 ('hunyuan15-t2v','HunyuanVideo 1.5 · 480p','hunyuan_1_5_480_t2v','DeepBeepMeep/HunyuanVideo1.5','hunyuan_video_1.5_480_quanto_bf16_int8.safetensors',hy15_shared,['txt2video'],49,24,30,4,1,5,97,640,384,'https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5'),
]
ffmpeg={'path':'cache/setup/ffmpeg-9.0.2-essentials_build.zip','url':'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip',
        'size':114768076,'sha256':'60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba',
        'extractTo':'video-lab/wangp/src/ffmpeg_bins','stripPrefix':'ffmpeg-9.0.2-essentials_build/bin/'}
models=[{'id':'wangp-video-runtime','engineId':'wangp-video-runtime','title':'Uses WanGP · 视频共用运行器','kind':'utility','version':'0.7.0',
         'description':'固定 WanGP 源码、FFmpeg 与独立 Python/CUDA 环境；安装期间准备全部组件，推理期间不联网。',
         'sourceUrl':'https://github.com/deepbeepmeep/Wan2GP/tree/'+REV,'files':[source,ffmpeg],
         'required':[{'path':'video-lab/wangp/src/shared/api.py'},{'path':'video-lab/wangp/.venv/Scripts/python.exe'}]+[{'path':'video-lab/wangp/src/ffmpeg_bins/'+name+'.exe'} for name in ['ffmpeg','ffprobe','ffplay']],
         'minimumVramMiB':0,'minimumRamGiB':8,'extractReserveBytes':12*1024**3,'deploymentProfile':'wangp-video',
         'license':{'label':'WanGP Community 2.0 / FFmpeg GPL','url':'https://github.com/deepbeepmeep/Wan2GP/blob/'+REV+'/LICENSE.txt',
                    'requiresAcceptance':True,'notice':'Uses WanGP。个人及企业内部免费使用按 Community 2.0；出售运行器、白牌、收费产品/API/SaaS 需要另行商业授权。FFmpeg 适用其 GPL 与分发许可，原始 ZIP 保留许可证。'},
         'validationStage':'researched','probes':[{'path':'video-lab/wangp/src/ffmpeg_bins/ffmpeg.exe','args':['-version'],'requiredFlags':['ffmpeg version'],'timeout':30}]}]
for spec in specs:
    suffix,title,mtype,mainrepo,mainfile,shared,modes,frames,fps,steps,multiple,offset,minframes,maxframes,width,height,original=spec
    eid='video-'+suffix
    entries=[(mainrepo,mainfile)]+shared
    installed=[weight(repo,name) for repo,name in entries]
    precision='BF16' if suffix in ['wan21-13b','kandinsky5-lite'] else 'INT8 weights / BF16 compute'
    limits={'sizeMultiple':32,'maxPixels':640*480,'frameRule':{'multiple':multiple,'offset':offset,'min':minframes,'max':maxframes},'fps':[fps],'maxSteps':80}
    caps={'modes':modes,'maxReferenceImages':9 if suffix=='h3-ref2va' else 2 if suffix in ['h3-fl2va','ltx25-distilled','ltxv098'] else 1 if 'img2video' in modes else 0,'endImage':suffix in ['h3-fl2va','h3-ref2va','ltx25-distilled','ltxv098'],'referenceVideo':suffix=='h3-ref2va','referenceAudio':suffix=='h3-ref2va','audioOutput':suffix.startswith('h3-') or suffix=='ltx25-distilled',**limits}
    if suffix in {'ltx25-distilled','ltxv098'}:
        limits['maxSteps']=steps
        limits['allowedSteps']=[steps]
        caps.update(maxSteps=steps,allowedSteps=[steps])
    if suffix=='ltxv098':
        caps['supportsNegativePrompt']=False
    if suffix=='ltx25-distilled':
        limits['sizeMultiple']=64
        caps['sizeMultiple']=64
    quant='bf16' if precision=='BF16' else 'int8'
    defaults={'width':width,'height':height,'frames':frames,'fps':fps,'steps':steps,'seed':12345}
    notice='使用 WanGP（Uses WanGP）。Community License 2.0 允许个人、本地、研究及符合许可的免费集成；禁止白牌、出售运行器、收费嵌入或收费 API/SaaS，相关用途需要作者另行授权。各模型仍分别适用原模型许可。当前是固定源配方，未完成这台 3060 的真实生成验收。'
    if suffix.startswith('h3-'):notice+=' H3 大权重及 32B 文本编码器需要大量内存/磁盘，32 GB 电脑可能严重换页或失败；建议先试较轻模型。'
    model={'id':eid,'engineId':eid,'title':title+' · Uses WanGP','kind':'video','version':'0.7.0','mediaAdapter':True,
           'description':'本地实验视频配方；显存卸载使用 WanGP profile 5，速度、画质和峰值内存需自行短测。',
           'recommended':suffix=='wan21-13b','installable':True,'gpuVerified':False,'minimumVramMiB':6144 if suffix.startswith('h3-') else 8192,
           'minimumRamGiB':32 if suffix.startswith('h3-') or '14b' in suffix or suffix=='ltx25-distilled' else 16,'extractReserveBytes':2*1024**3,
           'sourceUrl':original,'runtimeSourceUrl':'https://github.com/deepbeepmeep/Wan2GP','runtimeRevision':REV,'runtimeModelType':mtype,
           'precision':precision,'defaults':defaults,'limits':limits,'capabilities':caps,'inputOnly':'txt2video' not in modes,
           'runtimeConfig':{'transformer_quantization':quant,'transformer_dtype_policy':'bf16','text_encoder_quantization':'int8','generation_preview':'rgb','attention_mode':'sdpa','save_format':'mp4','profile':5,'video_profile':5,'compile':False},
           'settings':{'prompt_enhancer':'','config':'gguf_q4_k_m,bf16' if suffix.startswith('h3-') else '', 'spatial_upsampling':'','temporal_upsampling':''},
           'files':installed,'required':[{'path':x['path'],'size':x['size'],'sha256':x['sha256']} for x in installed]+[{'path':'video-lab/wangp/.venv/Scripts/python.exe'},{'path':'video-lab/wangp/src/shared/api.py'},{'path':'engines/'+eid+'/run.ps1'}],
           'dependencies':['wangp-video-runtime'],'adapters':[{'source':'scripts/providers/video_wangp.ps1','target':'engines/'+eid+'/run.ps1'}],
           'license':{'label':'WanGP Community 2.0 + 原模型许可','url':'https://github.com/deepbeepmeep/Wan2GP/blob/'+REV+'/LICENSE.txt','requiresAcceptance':True,'notice':notice,'modelUrl':original},
           'remedy':'点击下载部署完整固定版本组件。安装校验只检查 CUDA、运行器与文件；首次生成结果需你验收。',
           'runtime':{'type':'wangp','environment':'video-lab/wangp/.venv','python':'video-lab/wangp/.venv/Scripts/python.exe'},
           'manifest':{'id':eid,'title':title+' · Uses WanGP','kind':'video','version':'0.7.0','providerVersion':'0.7.0','entrypoint':'run.ps1','mediaAdapter':True,'capabilities':caps,'defaults':defaults,'runtime':{'type':'wangp','environment':'video-lab/wangp/.venv','python':'video-lab/wangp/.venv/Scripts/python.exe'},'source':original,'runtimeSource':'https://github.com/deepbeepmeep/Wan2GP','runtimeRevision':REV,'verificationStatus':'recipe-verified-generation-unverified'},
           'validationStage':'researched','resourceProfile':{'targetGpu':'RTX 3060 12GB','memoryProfile':5,'measured':False,'defaultResolution':f'{width}x{height}'},
           'probes':[{'path':'video-lab/wangp/.venv/Scripts/python.exe','args':['@root:scripts/providers/video_wangp.py','--engine',eid,'--check'],'requiredFlags':['"gpuInferenceExecuted": false'],'timeout':240}]}
    if suffix=='h3-ref2va':
        caps['inputTypes']=['image','video','audio']
    if suffix.startswith('h3-'):
        model['license']['notice']+=' H3 许可地域排除欧盟、英国、韩国和美国；商业产品界面必须展示 MiniMax H3，年收入超过 2000 万美元需额外书面授权；品牌使用范围按原许可。'
    models.append(model)
    print(eid,len(installed),'files',sum(x['size'] for x in installed),flush=True)
    (ROOT/'config/video-models.json').write_text(json.dumps({'version':'0.7.0','author':'Unclecow','verifiedAt':'2026-10-09','models':models},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

for candidate_id,title,url,mode in [
    ('video-framepack','FramePack / F1 · 已发布，独立适配器待接入','https://github.com/lllyasviel/FramePack','img2video'),
    ('video-cogvideox15','CogVideoX 1.5 · 已发布，独立适配器待接入','https://github.com/zai-org/CogVideo','txt2video'),
    ('video-sana20-preview','SANA-Video 2.0 四步 Preview · 已发布，独立适配器待接入','https://huggingface.co/Efficient-Large-Model/SANA-Video_2.0_5B_720p_4step','txt2video')]:
    models.append({'id':candidate_id,'engineId':candidate_id,'title':title,'kind':'video','version':'0.7.0',
                   'installable':False,'availability':'unsupported','releaseStatus':'released','installStatus':'blocked',
                   'validationStage':'adapter-not-integrated','gpuVerified':False,'files':[],'required':[],
                   'sourceUrl':url,'capabilities':{'modes':[mode],'maxReferenceImages':1 if mode=='img2video' else 0},
                   'description':'官方已发布的独立路线；当前固定 WanGP 运行器未接入对应完整安装和推理适配器。',
                   'remedy':'本模型已发布。本批尚未交付该模型的独立原生适配器、固定完整权重与 Windows 运行环境，当前不能安装；需要专项接入后启用。',
                   'license':{'label':'官方模型与依赖许可','url':url,'requiresAcceptance':True,'notice':'独立模型/底模/编码器分别适用其官方许可。当前条目只说明接入边界，不代表已安装或验收。'}})
(ROOT/'config/video-models.json').write_text(json.dumps({'version':'0.7.0','author':'Unclecow','verifiedAt':'2026-10-09','models':models},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
profiles={'version':'0.7.0','author':'Unclecow','profiles':[{'id':'wangp-video','python':'3.11.14','environment':'video-lab/wangp/.venv','requirements':'config/deployment-requirements/wangp-video.txt','torchIndex':'https://download.pytorch.org/whl/cu130','verifyCommands':[['-c','import torch,av,triton; assert torch.cuda.is_available(), "CUDA unavailable"; import importlib.metadata as m; assert m.version("torch").startswith("2.10.0+cu130"); assert m.version("av")=="16.1.0"; print("WanGP CUDA13 imports checked; no model weights loaded")']]}]}
(ROOT/'config/video-profiles.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(ROOT/'config/deployment-requirements').mkdir(parents=True,exist_ok=True)
SOURCE_LOCK.write_text(json.dumps({'runtimeRevision':REV,'repositories':PIN,'files':list(CACHE.values())},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('saved',len(models),'models',len(CACHE),'unique files',flush=True)
