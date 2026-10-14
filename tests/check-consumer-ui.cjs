/* Consumer onboarding acceptance. Isolated HTTP fixtures, headless Chromium; no downloads. */
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
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.FACTORY_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const mutations = [];
const uploads=[];
let job = null;
const model = {id:'fixture-3d',title:'示例建模工具',kind:'model',description:'图片变成立体模型',recommended:true,
  downloadBytes:1073741824,remainingBytes:1073741824,status:'missing',canInstall:true,blockers:[],
  license:{label:'MIT',url:'https://example.org/license',requiresAcceptance:true,notice:'按模型许可使用'},sourceUrl:'https://example.org/model'};
let models=[model];
let setupDelay=0;
let hardwareVramMiB=12288;
let samples=[];
const snapshot = {version:'0.5.0a',engines:[],imageEngines:[],rigEngines:[],animationEngines:[],textureEngines:[],batches:[],imageJobs:[],pipelines:[],resultTasks:[],machine:{gpu:'测试 GPU'}};
const pipelineRequests=[];
const pipelineMutations=[];
const server = http.createServer((request,response) => {
  const send = data => {response.writeHead(200,{'Content-Type':'application/json'});response.end(JSON.stringify(data));};
  if (request.url === '/api/state') return send(snapshot);
  if (request.url === '/api/pipelines' && request.method==='POST') {
    let body=''; request.on('data',data=>body+=data);request.on('end',()=>{
      const input=JSON.parse(body);pipelineRequests.push({body:input,header:request.headers['x-factory-request']});
      const pipeline={id:'pipeline-fixture',status:'running',phase:'image',imageJobId:'image-fixture',modelBatchId:null,...input};
      snapshot.pipelines=[pipeline];snapshot.imageJobs=[{id:'image-fixture',engine:input.imageEngineId,status:'running',progress:17,phase:'图片采样',prompt:input.prompt}];send(pipeline);
    });return;
  }
  if (/^\/api\/pipelines\/[^/]+\/(cancel|retry)$/.test(request.url) && request.method==='POST') {
    let body='';request.on('data',data=>body+=data);request.on('end',()=>{
      pipelineMutations.push({url:request.url,body:JSON.parse(body||'{}')});
      snapshot.pipelines[0].status=request.url.endsWith('/cancel')?'cancelled':'queued';send(snapshot.pipelines[0]);
    });return;
  }
  if (request.url === '/api/samples') return send({images:samples});
  if (request.url === '/files/inputs/existing-example.png') {response.writeHead(200,{'Content-Type':'image/png'});response.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=','base64'));return;}
  if (request.url === '/api/models' && request.method==='POST') {
    const chunks=[];request.on('data',chunk=>chunks.push(chunk));request.on('end',()=>{
      const bytes=Buffer.concat(chunks); uploads.push({bytes:bytes.length,name:request.headers['x-file-name'],type:request.headers['content-type'],header:request.headers['x-factory-request']});
      snapshot.resultTasks=[{id:'imported-fixture',kind:'imported-model',source:'history',name:'我的模型',inputMode:'model',jobs:[{id:'imported-job',engineId:'imported-model',status:'generated-unreviewed',assets:[{kind:'model',name:'我的模型.glb',url:'/files/inputs/imported-fixture.glb',bytes:bytes.length,modelInfo:{skins:0,joints:0,animationCount:0,skinnedVertices:0}}]}]}];send({id:'imported-fixture',name:'我的模型.glb'});
    });return;
  }
  if (request.url === '/api/setup') {setTimeout(()=>send({version:'0.5.0',hardware:{gpus:[{name:'测试 GPU',memoryMiB:hardwareVramMiB}],gpuAvailable:true,ramGiB:32,freeDiskBytes:100*1073741824,requirements:[{id:'gpu',label:'显卡',status:'ok',detail:'已检测到 NVIDIA GPU'}]},models,jobs:job?[job]:[]}),setupDelay);return;}
  if (request.url.startsWith('/api/setup/') && request.method==='POST') {
    let body=''; request.on('data', data => body+=data); request.on('end', () => {
      mutations.push({url:request.url,body:JSON.parse(body||'{}'),header:request.headers['x-factory-request']});
      job={id:'fixture-job',modelId:'fixture-3d',status:'running',progress:12,stage:'下载中',log:['正在下载模型'],totalBytes:1073741824,downloadedBytes:120000000};send(job);
    }); return;
  }
  const file=path.join(root,'visual',request.url.split('?')[0]==='/'?'index.html':request.url.split('?')[0]);
  if (!fs.existsSync(file)) {response.writeHead(404);response.end();return;}
  const types={'.html':'text/html','.css':'text/css','.js':'text/javascript'};
  response.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});fs.createReadStream(file).pipe(response);
});
(async () => {
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForSelector('#connection-status.connected');
    assert.match(await page.title(),/build something/,'Consumer product name should be visible');
    const navigation=await page.locator('.navigation [data-page]').evaluateAll(nodes=>nodes.map(node=>node.dataset.page));
    assert.equal(navigation[0],'home','One-click remains the first entry');
    assert.deepEqual(navigation,['home','results','queue','setup'],'Four purpose-based primary entries');
    const creation=await page.locator('#creation-nav [data-page]').evaluateAll(nodes=>nodes.map(node=>node.dataset.page));
    assert.ok(creation.indexOf('imagegen')<creation.indexOf('workspace'),'Image creation precedes model creation');
    for(const capability of ['edit','multiview','video'])assert.ok(creation.includes(capability),'Capabilities remain accessible in the creation toolbar');
    await page.waitForSelector('#setup-page:not([hidden])',{timeout:5000});
    assert.match(await page.locator('#setup-page').innerText(),/示例建模工具/);
    assert.equal(mutations.length,0,'First run must not download without an explicit install');
    await navigate(page, 'workspace');
    await page.waitForSelector('#workspace-page:not([hidden])');
    assert.equal(await page.locator('[data-action="sample"]').isVisible(),false,'Source-only installation must not offer missing sample images');
    samples=[{id:'existing-sample',name:'existing-example.png',url:'/files/inputs/existing-example.png',width:32,height:32,bytes:128}];
    await page.goto(`http://127.0.0.1:${server.address().port}/?existing-samples=1#workspace`);
    await page.waitForSelector('[data-action="sample"]:not([hidden])');
    await page.locator('[data-action="sample"]').click();
    await page.waitForSelector('#objects-list img[src="/files/inputs/existing-example.png"]');
    assert.equal(await page.locator('#objects-list img[src="/files/inputs/existing-example.png"]').count(),1,'Existing local sample usage must remain available');
    await navigate(page, 'setup');
    await page.waitForSelector('#setup-page:not([hidden])');
    await page.locator('[data-setup-action="install"]').click();
    assert.equal(await page.locator('#setup-install-confirm').isDisabled(),true,'License review is required before downloading');
    await page.locator('#setup-license-accept').check();
    await page.locator('#setup-install-confirm').click();
    await page.waitForFunction(()=>document.querySelector('#setup-jobs').textContent.includes('下载中'));
    assert.deepEqual(mutations[0],{url:'/api/setup/install',body:{modelId:'fixture-3d',acceptLicense:true},header:'1'});
    await navigate(page, 'imagegen');
    await page.waitForSelector('#imagegen-page:not([hidden])');
    assert.equal(await page.locator('#image-advanced').isVisible(),true);
    assert.equal(await page.locator('#image-advanced').getAttribute('open'),null,'Advanced controls stay collapsed by default');
    await page.setViewportSize({width:375,height:812});
    await navigate(page, 'help');
    await page.waitForSelector('#help-page:not([hidden])');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'Mobile page must fit the viewport');
    await page.getByText('作品在哪？关闭窗口会丢吗？',{exact:true}).click();
    assert.match(await page.locator('#help-page').innerText(),/关闭应用会停止正在运行的任务.*已完成作品保留/s,'Help must describe the owned desktop application lifecycle');
    assert.doesNotMatch(await page.locator('#help-page').innerText(),/关闭窗口不会停止后台任务|保持后台运行/,'Consumer flow must not ask users to manage a separate backend');
    snapshot.imageEngines=[{id:'qwen-image-21',title:'测试生图模型',status:'installed',defaultWeightVariant:'Q5_K_M',weightVariants:[
      {id:'Q5_K_M',label:'待安装档位',status:'missing',verified:false},
      {id:'Q4_K_M',label:'已校验档位',status:'installed',verified:true,bytes:1024}
    ]}];
    models=[{...model,id:'trellis'}, {...model,id:'qwen-image-21',title:'默认生图工具',kind:'image',engineId:'qwen-image-21',weightVariant:'Q4_K_M'},
      {...model,id:'another-model',title:'另一建模工具',recommended:false},
      {...model,id:'qwen-image-21-q5-k-m',title:'Q5_K_M 生图版本',kind:'image',engineId:'qwen-image-21',weightVariant:'Q5_K_M',recommended:false}];
    await page.reload();
    await page.waitForSelector('#connection-status.connected');
    await navigate(page, 'imagegen');
    await page.waitForSelector('#imagegen-page:not([hidden])');
    assert.notEqual(await page.locator('#image-advanced').getAttribute('open'),null,'Blocked weight choice must reveal the control that fixes it');
    await navigate(page, 'setup');
    await page.waitForSelector('#setup-recommended');
    assert.equal(await page.locator('#setup-models article[data-model-id]:visible').count(),2,'Only the two first-use recommendations should dominate');
    assert.equal(await page.locator('#setup-group-variants').getAttribute('open'),null,'Optional image variants are collapsed');
    await navigate(page, 'imagegen');
    await page.waitForSelector('#imagegen-page:not([hidden])');
    await page.locator('#imagegen-page [data-setup-variant="Q5_K_M"]').click();
    await page.waitForSelector('#setup-page:not([hidden])');
    await page.waitForSelector('#setup-models article[data-model-id="qwen-image-21-q5-k-m"]');
    assert.equal(await page.locator('#setup-models article[data-model-id="qwen-image-21-q5-k-m"]').isVisible(),true,'Missing image variant opens its exact install card');
    const glb=Buffer.alloc(20);glb.write('glTF');glb.writeUInt32LE(2,4);glb.writeUInt32LE(20,8);
    await page.locator('#import-model-file').setInputFiles({name:'我的模型.glb',mimeType:'model/gltf-binary',buffer:glb});
    await page.waitForSelector('#results-page:not([hidden])');
    await page.waitForSelector('[data-result-task-id="imported-fixture"]');
    assert.deepEqual(uploads[0],{bytes:20,name:encodeURIComponent('我的模型.glb'),type:'model/gltf-binary',header:'1'},'GLB import uses the raw upload contract');
    assert.match(await page.locator('[data-result-task-id="imported-fixture"]').innerText(),/本机导入/,'Import is never presented as model generation');
    assert.doesNotMatch(await page.locator('[data-result-task-id="imported-fixture"]').innerText(),/已生成/,'Import status must describe an import');
    setupDelay=1500;
    await page.goto(`http://127.0.0.1:${server.address().port}/?fresh=1#imagegen`);
    await page.waitForSelector('#connection-status.connected');
    await page.locator('#imagegen-page [data-setup-variant="Q5_K_M"]').click();
    await page.waitForSelector('#setup-models article[data-model-id="qwen-image-21-q5-k-m"]',{timeout:3000});
    setupDelay=0;hardwareVramMiB=6144;
    models=[{...model,id:'trellis',canInstall:false,blockers:['需要12GB显存']},
      {...model,id:'qwen-image-21',kind:'image',canInstall:false,blockers:['需要12GB显存']},
      {...model,id:'triposr',title:'6GB 建模工具',recommended:false,remainingBytes:1073741824},
      {...model,id:'hunyuan3d',title:'另一轻量工具',recommended:false,remainingBytes:4*1073741824}];
    await page.goto(`http://127.0.0.1:${server.address().port}/?fresh=2#setup`);
    await page.waitForSelector('#setup-recommended');
    assert.equal(await page.locator('#setup-recommended article[data-model-id="triposr"]').count(),1,'Six GB hardware should surface the lighter compatible model');
    assert.equal(await page.locator('#setup-recommended article[data-model-id="trellis"]').count(),0,'An unsupported default must not displace an installable alternative');
    snapshot.engines=[
      {id:'white-model',title:'白模工具',status:'installed',appearance:'geometry'},
      {id:'colored-model',title:'彩色模型工具',status:'ready',appearance:'texture'},
      {id:'vertex-model',title:'顶点色工具',status:'installed',appearance:'vertex'},
      {id:'views-model',title:'多视角工具',status:'ready',appearance:'geometry',multiView:true},
      {id:'missing-model',title:'待安装模型',status:'missing',appearance:'texture'}];
    snapshot.imageEngines=[{id:'local-image',title:'本地生图模型',status:'ready',defaultWeightVariant:'Q4_K_M',weightVariants:[
      {id:'Q4_K_M',label:'推荐版本',status:'ready',verified:true,bytes:1024},
      {id:'Q5_K_M',label:'待安装版本',status:'missing',verified:false}]}];
    await page.evaluate(()=>localStorage.removeItem('2d3d-factory-draft-v1'));
    await page.setViewportSize({width:1280,height:900});
    await page.goto(`http://127.0.0.1:${server.address().port}/?pipeline=1#home`);
    await page.waitForSelector('#home-page:not([hidden])');
    await page.waitForSelector('#pipeline-engines [data-pipeline-engine-id="colored-model"]',{state:'attached'});
    assert.equal(await page.locator('[data-pipeline-engine-id="colored-model"]').isChecked(),true,'Default one-click modeling must retain color');
    await navigate(page, 'workspace');
    assert.equal(await page.locator('#engines-list input[data-engine-id]').first().getAttribute('data-engine-id'),'colored-model','Textured modeling must appear before white geometry');
    assert.equal(await page.locator('#engines-list [data-engine-id="colored-model"]').isChecked(),true,'The workspace defaults to a colored output');
    await navigate(page, 'texture');
    assert.match(await page.locator('#texture-page').innerText(),/效果待验收/,'Old paint has no user quality acceptance');
    await navigate(page, 'home');
    await page.locator('#pipeline-prompt').fill('橙色机械小牛，完整物体，白色背景');
    assert.equal(await page.locator('#pipeline-more').count(),1,'Additional choices need an expandable catalog');
    assert.equal(await page.locator('#pipeline-more').getAttribute('open'),null,'Optional model catalog should not obscure the one-click start');
    assert.equal(await page.locator('#start-pipeline').evaluate(button=>button.getBoundingClientRect().bottom<=window.innerHeight),true,'Primary action must appear on the first desktop screen');
    if (process.env.FACTORY_UI_CAPTURE_DIR) {
      fs.mkdirSync(process.env.FACTORY_UI_CAPTURE_DIR,{recursive:true});
      await page.screenshot({path:path.join(process.env.FACTORY_UI_CAPTURE_DIR,'oneclick-home-fixture.png'),fullPage:true});
    }
    if (!(await page.locator('.pipeline-model-settings').getAttribute('open'))) await page.locator('.pipeline-model-settings > summary').click();
    await page.locator('#pipeline-more summary').click();
    await page.locator('[data-pipeline-engine-id="colored-model"]').check();
    await page.locator('[data-pipeline-engine-id="white-model"]').check();
    assert.match(await page.locator('#pipeline-engines').innerText(),/一张图.*有颜色.*贴图/s,'Color output must be clear to newcomers');
    assert.match(await page.locator('#pipeline-engines').innerText(),/只有形状.*白模/s,'Geometry only must not imply colored output');
    assert.match(await page.locator('#pipeline-engines').innerText(),/顶点颜色/,'Vertex color must be distinguished from texture');
    assert.equal(await page.locator('[data-pipeline-engine-id="views-model"]').isDisabled(),true,'One generated image cannot masquerade as multi-view inputs');
    assert.equal(await page.locator('[data-pipeline-engine-id="missing-model"]').isDisabled(),true,'Missing models cannot enter the pipeline');
    await page.locator('#start-pipeline').click();
    await page.waitForSelector('article[data-pipeline-id="pipeline-fixture"]');
    assert.match(pipelineRequests[0].body.clientRequestId || '',/^[0-9a-f-]{36}$/,'Each click must have an idempotent request identity');
    assert.deepEqual(pipelineRequests[0],{body:{prompt:'橙色机械小牛，完整物体，白色背景',imageEngineId:'local-image',weightVariant:'Q4_K_M',modelEngineIds:['colored-model','white-model'],width:512,height:512,steps:20,clientRequestId:pipelineRequests[0].body.clientRequestId},header:'1'},'One click submits the chosen model IDs to one durable pipeline request');
    assert.match(await page.locator('article[data-pipeline-id="pipeline-fixture"]').innerText(),/图片采样.*等待图片完成/s,'Progress must display actual image phase before 3D');
    snapshot.pipelines[0].phase='model';snapshot.pipelines[0].modelBatchId='model-fixture';
    snapshot.imageJobs[0].status='generated-unreviewed';snapshot.imageJobs[0].assets=[{kind:'image',name:'generated.png',url:'/files/inputs/existing-example.png'}];
    snapshot.batches=[{id:'model-fixture',name:'一键创作',jobs:[{id:'model-job',engine:'colored-model',status:'running',progress:34,phase:'加载模型'}]}];
    await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(()=>document.querySelector('article[data-pipeline-id="pipeline-fixture"]').textContent.includes('图片已完成'));
    assert.match(await page.locator('article[data-pipeline-id="pipeline-fixture"]').innerText(),/图片已完成.*加载模型/s,'Pipeline continues automatically and displays the model job phase');
    assert.equal(await page.locator('article[data-pipeline-id="pipeline-fixture"] [data-action="cancel-pipeline"]').count(),1,'Active one-click work must be cancellable');
    await page.locator('[data-action="cancel-pipeline"]').click();
    await page.waitForFunction(()=>document.querySelector('article[data-pipeline-id="pipeline-fixture"]').textContent.includes('已取消'));
    assert.deepEqual(pipelineMutations[0],{url:'/api/pipelines/pipeline-fixture/cancel',body:{}});
    await page.locator('[data-action="retry-pipeline"]').click();
    await page.waitForFunction(()=>document.querySelector('article[data-pipeline-id="pipeline-fixture"]').textContent.includes('一键创作进行中'));
    assert.deepEqual(pipelineMutations[1],{url:'/api/pipelines/pipeline-fixture/retry',body:{}});
    snapshot.pipelines[0].status='completed';snapshot.pipelines[0].phase='completed';snapshot.batches[0].jobs[0].status='generated-unreviewed';
    await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(()=>document.querySelector('article[data-pipeline-id="pipeline-fixture"]').textContent.includes('创作完成'));
    assert.match(await page.locator('article[data-pipeline-id="pipeline-fixture"]').innerText(),/创作完成/);
    assert.equal(await page.locator('article[data-pipeline-id="pipeline-fixture"] a[href="#results"]').count(),1,'Completed pipeline provides a direct result entry');
    if (!(await page.locator('.pipeline-advanced').getAttribute('open'))) await page.locator('.pipeline-advanced > summary').click();
    await page.locator('#pipeline-weight-variant').selectOption('Q5_K_M');
    assert.equal(await page.locator('#start-pipeline').isDisabled(),true,'An explicitly selected missing weight never falls back silently');
    assert.equal(await page.locator('#pipeline-weight-variant').inputValue(),'Q5_K_M');
    assert.equal(await page.locator('#pipeline-readiness [data-setup-variant="Q5_K_M"]').count(),1,'Missing weight directs to its exact install item');
    await page.setViewportSize({width:375,height:812});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'One-click controls must fit the mobile viewport');
    snapshot.pipelines=Array.from({length:12},(_,index)=>({id:`pipeline-history-${12-index}`,status:'completed',phase:'completed',prompt:'历史创作',createdAt:`2026-10-08T${String(12-index).padStart(2,'0')}:00:00+08:00`}));
    await page.locator('[data-action="refresh"]').click();
    await page.waitForSelector('article[data-pipeline-id="pipeline-history-9"]');
    assert.equal(await page.locator('#pipeline-jobs article').first().getAttribute('data-pipeline-id'),'pipeline-history-12','Newest pipeline must stay visible even after ten or more historical runs');
    assert.deepEqual(errors,[],'All application modules must remain error-free');
    console.log('PASS: onboarding, model input/color labels, one-click exact selections, automatic phase progress, missing variant protection, desktop lifecycle copy, mobile navigation.');
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
