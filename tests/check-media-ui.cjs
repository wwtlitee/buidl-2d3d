/* v0.9.0 isolated image/3D and historical-media flows. No real runtime, downloads or user browser. */
'use strict';
async function navigate(page, id) {
  const main = ['home','results','queue','setup'];
  if (main.includes(id)) await page.locator(`.navigation [data-page="${id}"]`).click();
  else if (id === 'help') await page.locator('[data-page="help"]:visible').first().click();
  else {
    if (!(await page.locator('#creation-nav').isVisible())) await page.locator('.navigation [data-page="home"]').click();
    await page.locator(`#creation-nav [data-page="${id}"]`).click();
  }
}
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require(process.env.FACTORY_PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const productVersion=JSON.parse(fs.readFileSync(path.join(__dirname,'../VERSION.json'),'utf8')).version;
const evidence=path.resolve(process.env.FACTORY_MEDIA_CAPTURE_DIR||path.join(__dirname,'../output/scope-v090/media'));
const mutations=[],checks=[],externalRequests=[];
const mark=label=>{checks.push(label);console.log('PASS '+label);};
const snapshot={version:productVersion,engines:[],imageEngines:[],images:{},jobs:[],batches:[],imageJobs:[],pipelines:[],resultTasks:[],mediaJobs:[],machine:{gpu:'RTX 3060 · 12 GB'},mediaEngines:[
  {id:'edit-fixture',title:'颜色编辑',kind:'image',status:'installed',capabilities:{modes:['edit','img2img','inpaint'],maxInputImages:3,supportsTransparency:true},defaults:{width:512,height:512,steps:8}},
  {id:'video-fixture',title:'视频试用',kind:'video',status:'installed',capabilities:{modes:['txt2video','img2video']},defaults:{width:512,height:288,frames:49,fps:16,steps:8}},
  {id:'video-missing',title:'未安装的视频',kind:'video',status:'missing',capabilities:{modes:['txt2video']},setupModelId:'video-missing'},
  {id:'view-fixture',title:'多视角',kind:'image',status:'installed',capabilities:{modes:['multiview']},defaults:{width:512,height:512,steps:20}}
]};
const models=[{id:'edit-installable',engineId:'edit-installable',title:'图片编辑实验档',kind:'image',capabilities:{modes:['edit']},status:'missing',canInstall:true,downloadBytes:15*1024**3,remainingBytes:15*1024**3,description:'本地图片编辑',resourceProfile:{tier:'experimental',label:'12 GB 实验',minVramGiB:8,minRamGiB:32,note:'尚未在本机生成'},validationStage:'installed-unverified',license:{label:'测试许可',url:'https://example.org/license',notice:'需查看许可'},sourceUrl:'https://example.org/model'},
{id:'unreleased',title:'尚未发布的图片模型',kind:'image',capabilities:{modes:['edit']},status:'blocked',canInstall:false,releaseStatus:'unreleased',description:'权重未发布',blockers:['官方权重尚未发布']},
...['video','rig','animation'].map(kind=>({id:'retired-'+kind,title:'旧能力 '+kind,kind,status:'installed',canInstall:true}))];
snapshot.imageEngines=[{id:'plain-image',title:'固定档位生图',kind:'image',status:'installed',defaults:{width:512,height:512,steps:4}},
{id:'qwen-image-21',title:'Qwen 精确版本夹具',kind:'image',status:'installed',defaultWeightVariant:'Q4_K_M',defaults:{width:512,height:512,steps:7},weightVariants:[{id:'Q4_K_M',status:'installed',verified:true,bytes:1024},{id:'Q6_K',status:'installed',verified:true,bytes:2048}]}];
snapshot.engines=[{id:'mesh-fixture',title:'单图建模',kind:'model',status:'installed',multiView:false,appearance:'texture'}];
const server=http.createServer((req,res)=>{
 const send=data=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
 if(req.url==='/api/state')return send(snapshot);
 if(req.url==='/api/setup')return send({models,jobs:[],credentials:{huggingface:{configured:false}},hardware:{ramGiB:32,gpus:[{name:'RTX 3060',memoryMiB:12288}],requirements:[]}});
 if(req.url==='/api/samples')return send({images:[]});
 if(req.method==='POST') {let body='';req.on('data',data=>body+=data);req.on('end',()=>{
  if(req.url==='/api/images'){const image={id:'input-fixture',name:'原图.png',url:'/files/input.png'};snapshot.images[image.id]=image;return send(image);}
  const data=JSON.parse(body||'{}');mutations.push({url:req.url,body:data,header:req.headers['x-factory-request']});
  if(req.url==='/api/setup/credentials')return send({configured:!!data.token});
  if(req.url==='/api/media-jobs'){const job={id:'media-fixture',kind:'image',engineId:data.engineId,engine:data.engineId,status:'queued',phase:'排队',request:data,assets:[]};snapshot.mediaJobs=[job];return send(job);}
  if(req.url.endsWith('/cancel')){snapshot.mediaJobs[0].status='cancelled';return send(snapshot.mediaJobs[0]);}
  return send({});
 });return;}
 const url=req.url.split('?')[0];
 if(url==='/files/input.png'){res.writeHead(200,{'Content-Type':'image/png'});return res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=','base64'));}
 if(url==='/files/video.mp4'){res.writeHead(200,{'Content-Type':'video/mp4'});return res.end(Buffer.from('00000018667479706d703432000000006d70343269736f6d','hex'));}
 const file=path.join(__dirname,'../visual',url==='/'?'index.html':url);
 if(!fs.existsSync(file)){res.writeHead(404);res.end();return;}res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(res);
});
(async()=>{let browser,page;const errors=[];fs.mkdirSync(evidence,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));try{
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',error=>errors.push(error.message));
 const origin=`http://127.0.0.1:${server.address().port}`;
 await page.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();externalRequests.push(route.request().url());return route.abort();});
 const reveal=async selector=>{for(const details of await page.locator(selector).locator('xpath=ancestor::details').all())if(await details.getAttribute('open')===null)await details.locator(':scope > summary').click();};
 await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.waitForSelector('#connection-status.connected');
 for(const section of ['edit','multiview','texture','imagegen','workspace'])assert.equal(await page.locator(`#creation-nav [data-page="${section}"]`).count(),1,`${section} must be directly accessible`);
 assert.equal(await page.locator('[data-page="video"], [data-page="animation"], #video-submit, #start-rigging, #start-motion').count(),0);
 mark('only retained creation capabilities remain');
 await navigate(page, 'imagegen');await page.locator('#image-prompt').fill('单物体小牛');
 assert.equal(await page.locator('#start-imagegen').isDisabled(),false,'A fixed-variant image engine needs no Qwen variant selector');
 await page.locator('#start-imagegen').click();await page.waitForFunction(()=>document.querySelector('#start-imagegen').textContent==='生成图片');
 const imageRequest=mutations.find(x=>x.url==='/api/image-jobs');assert.equal(imageRequest.body.engineId,'plain-image');assert.equal(imageRequest.body.steps,4);assert.equal(imageRequest.body.weightVariant,undefined);
 await page.locator('[data-image-engine-id="qwen-image-21"]').check();await reveal('#image-weight-variant');await page.locator('#image-weight-variant').selectOption('Q6_K');
 await page.reload();await page.waitForSelector('#connection-status.connected');await navigate(page,'imagegen');
 assert.equal(await page.locator('[data-image-engine-id="qwen-image-21"]').isChecked(),true);assert.equal(await page.locator('#image-weight-variant').inputValue(),'Q6_K');
 await page.locator('#start-imagegen').click();await page.waitForFunction(()=>document.querySelector('#start-imagegen').textContent==='生成图片');
 let variantRequest=mutations.filter(x=>x.url==='/api/image-jobs').at(-1);assert.equal(variantRequest.body.engineId,'qwen-image-21');assert.equal(variantRequest.body.weightVariant,'Q6_K');
 snapshot.imageEngines[1].weightVariants[1].status='missing';await page.locator('[data-action="refresh"]').click();await page.waitForFunction(()=>document.querySelector('#start-imagegen').disabled);
 assert.equal(await page.locator('#image-weight-variant').inputValue(),'Q6_K','Missing selected variant must never silently downgrade');
 snapshot.imageEngines[1].weightVariants[1].status='installed';await page.locator('[data-action="refresh"]').click();
 mark('fixed image defaults and exact Qwen variant survive reload without downgrading');
 await navigate(page, 'home');await page.locator('#pipeline-prompt').fill('单物体小牛');
 assert.equal(await page.locator('#start-pipeline').isDisabled(),false,'One-click supports fixed-variant image engines');
 await reveal('#pipeline-image-engine');await page.locator('#pipeline-image-engine').selectOption('qwen-image-21');await reveal('#pipeline-weight-variant');await page.locator('#pipeline-weight-variant').selectOption('Q6_K');
 await page.locator('#start-pipeline').click();await page.waitForFunction(()=>document.querySelector('#start-pipeline').textContent==='一键生成图片和 3D');
 const pipelineRequest=mutations.find(x=>x.url==='/api/pipelines');assert.equal(pipelineRequest.body.imageEngineId,'qwen-image-21');assert.equal(pipelineRequest.body.weightVariant,'Q6_K');assert.deepEqual(pipelineRequest.body.modelEngineIds,['mesh-fixture']);
 mark('one-click image and 3D retains exact image variant and selected model');
 await navigate(page, 'edit');await page.locator('#edit-prompt').fill('把小牛改成橙色');
 assert.equal(await page.locator('#edit-submit').isDisabled(),true,'Editing requires its input image');
 await page.locator('#edit-input').setInputFiles({name:'原图.png',mimeType:'image/png',buffer:Buffer.from('image fixture')});
 await page.waitForFunction(()=>!document.querySelector('#edit-submit').disabled);await page.locator('#edit-transparent').check();await page.locator('#edit-submit').click();
 await page.waitForFunction(()=>document.querySelector('#edit-jobs').textContent.includes('排队中'));
 let request=mutations.find(item=>item.url==='/api/media-jobs');assert.equal(request.body.engineId,'edit-fixture');assert.equal(request.body.mode,'edit');assert.deepEqual(request.body.inputImageIds,['input-fixture']);assert.equal(request.header,'1');assert.equal(request.body.transparentBackground,true);
 await page.locator('#edit-mode').selectOption('inpaint');assert.equal(await page.locator('#edit-submit').isDisabled(),true,'Inpainting requires a mask');
 mark('image editing retains references, transparent output and mask requirements');
 await navigate(page, 'multiview');assert.equal(await page.locator('#multiview-submit').isDisabled(),true);
 await page.locator('#multiview-input').setInputFiles({name:'正面.png',mimeType:'image/png',buffer:Buffer.from('image fixture')});await page.waitForFunction(()=>!document.querySelector('#multiview-submit').disabled);await page.locator('#multiview-submit').click();
 await page.waitForFunction(()=>document.querySelector('#multiview-jobs').textContent.includes('排队中'));request=mutations.filter(item=>item.url==='/api/media-jobs').at(-1);
 assert.equal(request.body.mode,'multiview');assert.equal(request.body.engineId,'view-fixture');assert.deepEqual(request.body.inputImageIds,['input-fixture']);assert.equal(request.body.frames,undefined);assert.equal(request.body.fps,undefined);assert.match(request.body.clientRequestId,/^[0-9a-f]{32}$/);assert.ok(request.body.seed>=0);
 snapshot.mediaJobs[0].status='running';snapshot.mediaJobs[0].phase='正在加载模型';snapshot.mediaJobs[0].progress=null;await page.locator('[data-action="refresh"]').click();
 await page.waitForFunction(()=>document.querySelector('#multiview-jobs').textContent.includes('正在加载模型'));assert.equal(await page.locator('#multiview-jobs [role="progressbar"]').getAttribute('aria-valuenow'),null,'Unknown progress must remain unknown');
 await page.locator('#multiview-jobs [data-action="cancel-job"]').click();await page.waitForFunction(()=>document.querySelector('#multiview-jobs').textContent.includes('已取消'));assert.equal(mutations.at(-1).url,'/api/jobs/media-fixture/cancel');
 mark('multiview retains shared queue submission, honest progress and cancellation');
 const oldVideo={id:'old-video',kind:'video',engine:'video-fixture',engineId:'video-fixture',status:'generated-unreviewed',creationEnabled:false,request:{mode:'img2video'},assets:[{name:'video.mp4',kind:'video',url:'/files/video.mp4',bytes:24}]};
 const failures=['video','rig','animation'].map(kind=>({id:'old-'+kind+'-failed',kind,engine:kind+'-fixture',engineId:kind+'-fixture',status:'failed',request:{mode:kind==='video'?'txt2video':kind},assets:[],error:'旧任务失败',pipelineStage:kind==='video'?undefined:kind}));
 snapshot.mediaJobs.push(oldVideo,failures[0]);snapshot.batches=[{id:'old-postprocess',name:'旧后处理任务',objects:[],jobs:failures.slice(1)}];snapshot.jobs=[...snapshot.mediaJobs,...failures.slice(1)];
 snapshot.resultTasks=[oldVideo,...failures].map(job=>({id:'result-'+job.id,name:job.id,kind:job.kind==='video'?'video':'model',source:'queue',pipelineStage:job.pipelineStage,createdAt:'2026-10-12T04:00:00Z',jobs:[job]}));
 await page.locator('[data-action="refresh"]').click();await navigate(page,'results');await page.waitForSelector('[data-result-task-id="result-old-video"]');
 const oldVideoCard=page.locator('[data-result-task-id="result-old-video"]');
 const oldVideoHref=await oldVideoCard.locator('.asset-card-actions a[download]').getAttribute('href');assert.equal((await page.request.get(new URL(oldVideoHref,origin).href)).status(),200);
 await oldVideoCard.locator('[data-action="preview-result"]').first().click();assert.equal(await page.locator('#preview-content video[controls]').count(),1,'Historical video retains its controlled player');assert.match(await page.locator('#preview-download').getAttribute('href'),/video\.mp4/);await page.locator('[data-action="close-preview"]').click();
 for(const job of failures)assert.equal(await page.locator(`[data-result-task-id="result-${job.id}"] [data-action="retry-job"]`).count(),0,'Retired result failures cannot retry');
 await page.screenshot({path:path.join(evidence,'historical-video-and-retired-failures.png'),fullPage:true});
 await navigate(page,'queue');for(const job of failures)assert.equal(await page.locator(`[data-action="retry-job"][data-job-id="${job.id}"]`).count(),0,'Retired queue failures cannot retry');
 mark('historical video player and download survive while retired result and queue retries are absent');
 await navigate(page, 'setup');assert.equal(await page.locator('[data-model-id^="retired-"]').count(),0);await page.locator('#setup-filter').selectOption('edit');await page.waitForSelector('[data-model-id="edit-installable"]');
 assert.match(await page.locator('.setup-model-card[data-model-id="edit-installable"]').innerText(),/12 GB 实验/);assert.match(await page.locator('.setup-model-card[data-model-id="edit-installable"]').innerText(),/32 GB/);assert.equal(await page.locator('.setup-model-card[data-model-id="unreleased"] button').isDisabled(),true);
 await page.locator('#setup-search').fill('尚未');assert.equal(await page.locator('.setup-model-card[data-model-id="edit-installable"]').count(),0);await page.locator('#setup-search').fill('');
 await page.locator('.setup-credentials > summary').click();await page.locator('#setup-hf-token').fill('hf_secret-fixture-only');await page.locator('[data-setup-action="save-credentials"]').click();await page.waitForFunction(()=>document.querySelector('#setup-hf-token').value==='');
 assert.deepEqual(mutations.at(-1).body,{provider:'huggingface',token:'hf_secret-fixture-only'});assert.equal(await page.evaluate(()=>JSON.stringify(localStorage).includes('hf_secret-fixture-only')),false);assert.equal((await page.locator('#setup-feedback').innerText()).includes('hf_secret'),false);
 mark('retained catalog resources, search and ephemeral credentials remain usable');
 await page.setViewportSize({width:375,height:812});for(const id of ['edit','multiview','results','setup']){await navigate(page,id);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);}
 await navigate(page,'multiview');await page.screenshot({path:path.join(evidence,'multiview-mobile.png'),fullPage:true});
 snapshot.engines[0].appearance='geometry';await page.evaluate(()=>localStorage.clear());
 await page.goto(`http://127.0.0.1:${server.address().port}/?only-white=1#home`);await page.waitForSelector('#connection-status.connected');
 assert.equal(await page.locator('#pipeline-engines input:checked').count(),0,'A white-only installation must never become a colored default');
 assert.equal(await page.locator('#start-pipeline').isDisabled(),true,'White geometry remains an explicit user choice');
 await page.locator('.pipeline-model-settings > summary').click();await page.locator('#pipeline-more summary').click();await page.locator('[data-pipeline-engine-id="mesh-fixture"]').check();await page.locator('#pipeline-prompt').fill('只需要形状');
 assert.equal(await page.locator('#start-pipeline').isDisabled(),false,'An explicit geometry selection is still supported');
 mark('mobile retained flows fit and white geometry still requires explicit choice');
 assert.deepEqual(errors,[]);assert.deepEqual(externalRequests,[]);assert.equal(mutations.some(item=>item.url==='/api/model-jobs'||item.body.mode?.includes('video')||/\/retry$/.test(item.url)),false,'No retired new creation or retry is sent');
 fs.writeFileSync(path.join(evidence,'receipt.json'),JSON.stringify({status:'passed',productVersion,checks,mutations,pageErrors:errors,externalRequests,note:'Isolated fixtures. Historical video checks file access and player controls, not video decoding. No real GPU/models/user browser.'},null,2));
 }catch(error){if(page){await page.screenshot({path:path.join(evidence,'failure.png'),fullPage:true}).catch(()=>{});fs.writeFileSync(path.join(evidence,'failure-dom.txt'),await page.locator('body').innerText().catch(()=>''));}fs.writeFileSync(path.join(evidence,'receipt.json'),JSON.stringify({status:'failed',checks,error:error.stack,mutations,pageErrors:errors,externalRequests},null,2));console.error(error);process.exitCode=1;}finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}})().catch(error=>{console.error(error);process.exitCode=1;});
