/* v0.9.0 scoped experience acceptance: isolated fixtures, no runtime or model downloads. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.FACTORY_PLAYWRIGHT_MODULE || path.join(process.env.USERPROFILE, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root = path.resolve(__dirname, '..');
const productVersion = JSON.parse(fs.readFileSync(path.join(root, 'VERSION.json'), 'utf8')).version;
const evidence = path.resolve(process.env.FACTORY_UI_CAPTURE_DIR || path.join(root, 'output/scope-v090/experience'));
const mutations = [], unexpectedRequests = [], pageErrors = [], checks = [];
let failMedia = false, uploadCount = 0;
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=', 'base64');
const imageRecord = (id, name) => ({ id, name, url: `/files/inputs/${id}.png`, width: 512, height: 512, bytes: png.length });
const generatedImage = { kind: 'image', name: '已生成小牛.png', url: '/files/outputs/fixture/generated.png', bytes: png.length };
const imageJob = { id: 'image-done', engine: 'image-fixture', engineId: 'image-fixture', kind: 'image', status: 'generated-unreviewed', createdAt: '2026-10-13T04:00:00Z', prompt: '示例小牛', assets: [generatedImage] };
const snapshot = {
  version: productVersion, machine: { gpu: '隔离测试 GPU · 12 GB', queue: '串行' },
  engines: [{ id: 'model-fixture', title: '彩色建模示例', kind: 'model', status: 'installed', appearance: 'texture', multiView: false }],
  imageEngines: [{ id: 'image-fixture', title: '图片模型示例', kind: 'image', status: 'installed', defaults: { width: 512, height: 512, steps: 4 } }],
  mediaEngines: [
    { id: 'edit-fixture', title: '图片编辑示例', kind: 'image', status: 'installed', capabilities: { modes: ['edit', 'img2img', 'inpaint'], maxInputImages: 3, supportsTransparency: true }, defaults: { width: 512, height: 512, steps: 8 } },
    { id: 'view-fixture', title: '联合视角示例', kind: 'image', status: 'installed', capabilities: { modes: ['multiview'], maxInputImages: 1 }, defaults: { width: 512, height: 512, steps: 20 } },
    { id: 'edit-missing', title: '待安装图片编辑模型', kind: 'image', status: 'missing', setupModelId: 'edit-missing', capabilities: { modes: ['edit'], maxInputImages: 1 }, defaults: { width: 512, height: 512, steps: 8 } },
    { id: 'video-fixture', title: '视频生成示例', kind: 'video', status: 'installed', capabilities: { modes: ['txt2video', 'img2video'], maxInputImages: 1 }, defaults: { width: 512, height: 288, frames: 49, fps: 16, steps: 8 } },
    { id: 'video-missing', title: '待安装视频模型', kind: 'video', status: 'missing', setupModelId: 'video-missing', capabilities: { modes: ['txt2video', 'img2video'], maxInputImages: 1 }, defaults: { width: 512, height: 288, frames: 49, fps: 16, steps: 8 } }
  ],
  rigEngines: [{ id: 'rig-fixture', title: '绑骨示例', status: 'installed' }, { id: 'rig-alternate', title: '备用绑骨示例', status: 'installed' }], animationEngines: [{ id: 'basic-motion', title: '基础动作', status: 'installed' }], textureEngines: [{ id: 'hunyuan-paint', title: '上色示例', status: 'installed' }], batches: [], images: {}, jobs: [imageJob], imageJobs: [imageJob], mediaJobs: [], pipelines: [], history: [],
  resultTasks: [{ id: 'result-image', kind: 'image', source: 'queue', name: '可继续创作的图片', createdAt: '2026-10-13T04:00:00Z', prompt: '示例小牛', jobs: [imageJob] }]
};
for (const [id, name, modelInfo] of [['static-cow', '静态小牛', { skins: 0, joints: 0, skinnedVertices: 0, animationCount: 0 }], ['skinned-cow', '已绑骨小牛', { skins: 1, joints: 8, skinnedVertices: 120, animationCount: 0 }], ['legacy-skinned-cow', '仅蒙皮顶点的历史模型', { skinnedVertices: 120 }]]) {
  snapshot.resultTasks.push({ id, name, kind: 'model', source: 'history', createdAt: '2026-10-13T04:00:00Z', inputs: [{ view: 'front', ...imageRecord('model-source', '来源原图.png') }, { view: 'left', ...imageRecord('model-left', '来源左侧.png') }], jobs: [{ id: `${id}-job`, engineId: 'model-fixture', status: 'generated-unreviewed', assets: [{ kind: 'model', name: `${name}.glb`, url: `/files/outputs/fixture/${id}.glb`, bytes: 128, modelInfo }] }] });
}
const models = [
  { id: 'catalog-edit', engineId: 'edit-fixture', title: '图片编辑示例', kind: 'image', capabilities: { modes: ['edit', 'inpaint'] }, status: 'installed', validationStage: 'installed-unverified' },
  { id: 'edit-missing', engineId: 'edit-missing', title: '待安装图片编辑模型', kind: 'image', capabilities: { modes: ['edit'] }, status: 'missing', canInstall: true, blockers: [], downloadBytes: 1024 ** 3, remainingBytes: 1024 ** 3, license: { label: '测试许可', requiresAcceptance: true, url: 'https://example.org/fixture-license' } },
  { id: 'catalog-rig', engineId: 'rig-fixture', title: '历史绑骨工具', kind: 'rig', status: 'installed' },
  { id: 'catalog-motion', engineId: 'basic-motion', title: '历史动作工具', kind: 'animation', status: 'installed' },
  { id: 'catalog-video', engineId: 'video-fixture', title: '视频生成示例', kind: 'video', capabilities: { modes: ['txt2video', 'img2video'] }, status: 'installed', validationStage: 'installed-unverified' },
  { id: 'video-missing', engineId: 'video-missing', title: '待安装视频模型', kind: 'video', status: 'missing', canInstall: true, blockers: [], downloadBytes: 1024 ** 3, remainingBytes: 1024 ** 3, license: { label: '测试许可', requiresAcceptance: true, url: 'https://example.org/fixture-license' } }
];
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://fixture.invalid');
  const send = (value, status = 200) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); };
  if (request.method === 'GET' && url.pathname === '/api/state') return send(snapshot);
  if (request.method === 'GET' && url.pathname === '/api/samples') return send({ images: [] });
  if (request.method === 'GET' && url.pathname === '/api/setup') return send({ version: productVersion, models, jobs: [], credentials: { huggingface: { configured: false } }, hardware: { gpuAvailable: true, gpus: [{ name: '隔离测试 GPU', memoryMiB: 12288 }], ramGiB: 32, freeDiskBytes: 100 * 1024 ** 3, requirements: [] } });
  if (request.method === 'POST' && ['/api/images', '/api/images/from-output', '/api/media-jobs'].includes(url.pathname)) {
    const chunks = []; request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => {
      const raw = Buffer.concat(chunks);
      const body = url.pathname === '/api/images' ? { uploadedBytes: raw.length } : JSON.parse(raw.toString('utf8') || '{}');
      mutations.push({ url: url.pathname, body, header: request.headers['x-factory-request'] });
      if (url.pathname === '/api/images' || url.pathname === '/api/images/from-output') {
        const image = imageRecord(`${url.pathname === '/api/images' ? 'input' : 'continued'}-${++uploadCount}`, url.pathname === '/api/images' ? decodeURIComponent(request.headers['x-file-name'] || '原图.png') : '已生成小牛.png');
        snapshot.images[image.id] = image; return send(image);
      }
      if (failMedia) return send({ error: '测试故障：模型暂未完成准备，请检查模型状态后重试。' }, 400);
      const job = { id: `media-${mutations.length}`, engine: body.engineId, engineId: body.engineId, kind: body.mode.includes('video') ? 'video' : 'image', status: 'queued', phase: '排队中', request: body, assets: [] };
      snapshot.mediaJobs.push(job); snapshot.jobs.push(job); return send(job);
    }); return;
  }
  if (request.method === 'GET' && /^\/files\/(inputs|outputs\/fixture)\/[a-z0-9-]+\.png$/.test(url.pathname)) {
    response.writeHead(200, { 'Content-Type': 'image/png' }); response.end(png); return;
  }
  if (url.pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
  const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
  const file = path.resolve(root, 'visual', relative);
  const staticRoot = path.resolve(root, 'visual') + path.sep;
  if (request.method === 'GET' && file.startsWith(staticRoot) && fs.existsSync(file) && fs.statSync(file).isFile()) {
    response.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream' }); fs.createReadStream(file).pipe(response); return;
  }
  unexpectedRequests.push(`${request.method} ${url.pathname}`); send({ error: 'Fixture rejected unexpected request' }, 404);
});

(async () => {
  fs.mkdirSync(evidence, { recursive: true });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser, page;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const openPage = async () => {
      const tab = await context.newPage(); tab.on('pageerror', error => pageErrors.push(error.message));
      await tab.goto(`${origin}/#home`); await tab.waitForSelector('#connection-status.connected'); return tab;
    };
    page = await openPage();
    const go = async target => {
      if (['home', 'results', 'queue', 'setup'].includes(target)) await page.locator(`#main-nav [data-page="${target}"]`).click();
      else if (target === 'help') await page.locator('[data-page="help"]:visible').first().click();
      else { await page.locator('#main-nav [data-page="home"]').click(); await page.locator(`#creation-nav [data-page="${target}"]`).click(); }
      await page.waitForSelector(`#${target}-page:not([hidden])`);
    };
    const reveal = async selector => {
      const ancestors = page.locator(selector).locator('xpath=ancestor::details');
      for (let index = 0; index < await ancestors.count(); index++) {
        const details = ancestors.nth(index); if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click();
      }
    };
    const field = async (id, value) => { await reveal(`#${id}`); await page.locator(`#${id}`).fill(String(value)); };
    const mark = name => { checks.push(name); console.log(`PASS ${name}`); };

    assert.deepEqual(await page.locator('#main-nav > [data-page]').evaluateAll(nodes => nodes.map(node => node.dataset.page)), ['home', 'results', 'queue', 'setup']);
    for (const child of ['imagegen', 'edit', 'multiview', 'workspace', 'texture']) {
      await go(child);
      assert.equal(await page.locator('#main-nav [aria-current="page"]').getAttribute('data-page'), 'home', `${child} belongs to creation`);
      assert.equal(await page.locator('#creation-nav [aria-current="page"]').getAttribute('data-page'), child);
    }
    for (const target of ['results', 'queue', 'setup']) { await go(target); assert.equal(await page.locator('#main-nav [aria-current="page"]').getAttribute('data-page'), target); }
    assert.equal(await page.locator('[data-page="video"], [data-page="animation"], a[href="#video"], a[href="#animation"]').count(), 0, 'Retired creation routes are absent from every navigation link');
    mark('four primary destinations and correct creative subpage ownership');

    await go('edit'); await page.locator('#edit-prompt').fill('把小牛改成橙色，保留姿势');
    await page.locator('#edit-input').setInputFiles({ name: '参考牛.png', mimeType: 'image/png', buffer: png });
    await page.waitForSelector('#edit-input-preview img');
    await page.locator('#edit-mode').selectOption('inpaint');
    await page.locator('#edit-mask').setInputFiles({ name: '蒙版.png', mimeType: 'image/png', buffer: png });
    await field('edit-width', 768); await field('edit-height', 512); await field('edit-steps', 13); await field('edit-seed', 42); await field('edit-negative', '文字、水印');
    const expectedEdit = { engine: 'edit-fixture', mode: 'inpaint', prompt: '把小牛改成橙色，保留姿势', width: '768', height: '512', steps: '13', seed: '42', negative: '文字、水印' };
    const readEdit = async () => ({ engine: await page.locator('#edit-engine').inputValue(), mode: await page.locator('#edit-mode').inputValue(), ...await page.locator('#edit-page').evaluate(section => Object.fromEntries(['prompt', 'width', 'height', 'steps', 'seed', 'negative'].map(key => [key, section.querySelector(`#edit-${key}`).value]))) });
    snapshot.mediaEngines[0].title = '图片编辑示例 · 状态已刷新';
    await page.waitForResponse(response => response.url().endsWith('/api/state'), { timeout: 7000 });
    assert.deepEqual(await readEdit(), expectedEdit, 'Polling metadata may not replace entered parameters');
    await go('multiview'); await page.locator('#multiview-prompt').fill('同一只小牛的侧面和背面');
    await page.locator('#multiview-input').setInputFiles({ name: '视角主图.png', mimeType: 'image/png', buffer: png }); await page.waitForSelector('#multiview-input-preview img');
    await field('multiview-steps', 23); await field('multiview-seed', 101);
    const oldVideoDraft = { engineId: 'video-fixture', mode: 'img2video', inputs: [imageRecord('legacy-video-source', '旧首帧.png')], fields: { prompt: '旧视频草稿必须原样保留', frames: '65', fps: '12', seed: '19' }, pendingRequest: { requestId: 'legacy-pending-video', seed: 19 } };
    const oldAnimationDraft = JSON.stringify({ 'rig-model': '/files/outputs/fixture/static-cow.glb', 'rig-engine': 'rig-alternate', 'motion-model': '/files/outputs/fixture/skinned-cow.glb', 'motion-preset': 'walk' });
    await page.evaluate(({ video, animation }) => {
      const draft = JSON.parse(localStorage.getItem('2d3d-factory-draft-v1'));
      draft.mediaForms.video = video; localStorage.setItem('2d3d-factory-draft-v1', JSON.stringify(draft));
      localStorage.setItem('2d3d-factory-animation-draft-v1', animation);
    }, { video: oldVideoDraft, animation: oldAnimationDraft });
    await page.close(); page = await openPage(); await go('edit');
    assert.deepEqual(await readEdit(), expectedEdit, 'Reopening preserves saved edit inputs and exact model selection');
    assert.equal(await page.locator('#edit-input-preview img').count(), 1); assert.equal(await page.locator('#edit-submit').isDisabled(), false, 'The restored mask and input remain usable');
    await go('multiview'); assert.equal(await page.locator('#multiview-mode').inputValue(), 'multiview'); assert.equal(await page.locator('#multiview-prompt').inputValue(), '同一只小牛的侧面和背面');
    for (const [name, value] of [['steps', '23'], ['seed', '101']]) assert.equal(await page.locator(`#multiview-${name}`).inputValue(), value);
    assert.equal(await page.locator('#multiview-input-preview img').count(), 1);
    mark('media drafts survive polling and reopening with masks, references and exact parameters');

    const mutationsBeforeOldRoutes = mutations.length;
    for (const route of ['video', 'animation']) {
      await page.evaluate(value => { location.hash = value; }, route);
      await page.waitForSelector('#home-page:not([hidden])'); await page.waitForSelector('#notification:not([hidden])');
      assert.equal(new URL(page.url()).hash, '#home', 'Old bookmarks resolve to the current creation home');
      assert.match(await page.locator('#notification').innerText(), /视频|绑骨|动作/);
      assert.match(await page.locator('#notification').innerText(), /生图|3D|本地/);
      await page.locator('#notification button').click();
    }
    assert.equal(mutations.length, mutationsBeforeOldRoutes, 'Opening old bookmarks must never enqueue');
    const savedLegacy = await page.evaluate(() => ({ video: JSON.parse(localStorage.getItem('2d3d-factory-draft-v1')).mediaForms.video, animation: localStorage.getItem('2d3d-factory-animation-draft-v1') }));
    assert.deepEqual(savedLegacy, { video: oldVideoDraft, animation: oldAnimationDraft }, 'Inactive drafts survive active-page saves unchanged');
    mark('old video and animation bookmarks explain the scope without submitting or destroying old drafts');

    await go('texture'); await page.locator('#texture-model-picker').click(); await page.waitForSelector('#model-picker-dialog[open]');
    assert.equal(await page.locator('#model-picker-list .model-choice-button').count(), 1, 'Texturing only offers unskinned models');
    assert.doesNotMatch(await page.locator('#model-picker-list').innerText(), /已绑骨小牛/);
    await page.locator('#model-picker-list .model-choice-button').click();
    await page.locator('#texture-reference-mode').selectOption('manual');
    for (const direction of ['正面', '左侧']) {
      await page.getByLabel(`上传${direction}上色参考图`, { exact: true }).setInputFiles({ name: `${direction}.png`, mimeType: 'image/png', buffer: png });
      await page.waitForSelector(`#texture-references img[alt="${direction}参考图"]`);
    }
    await page.reload(); await page.waitForSelector('#connection-status.connected');
    await go('texture');
    assert.equal(await page.locator('#texture-model').inputValue(), '/files/outputs/fixture/static-cow.glb');
    assert.equal(await page.locator('#texture-reference-mode').inputValue(), 'manual');
    assert.equal(await page.locator('#texture-references img').count(), 2);
    assert.equal(await page.locator('#start-texturing').isDisabled(), false);
    mark('shared texturing picker excludes skinned models and retains model and image choices');
    const textureFrontId = await page.evaluate(() => JSON.parse(localStorage.getItem('2d3d-factory-texture-draft-v1')).references.find(([view]) => view === 'front')[1].id);
    const textureFrontRecord = snapshot.images[textureFrontId]; delete snapshot.images[textureFrontId];
    await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(() => document.querySelector('#start-texturing').disabled);
    assert.match(await page.locator('#texture-page').innerText(), /失效|重新上传|不存在/);
    snapshot.images[textureFrontId] = textureFrontRecord; await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(() => !document.querySelector('#start-texturing').disabled);
    mark('missing saved texturing input remains visible but cannot be submitted');
    snapshot.jobs.push(
      { id: 'texture-own', kind: 'texture', pipelineStage: 'texture', engineId: 'hunyuan-paint', status: 'generated-unreviewed', sourceModel: { url: '/files/outputs/fixture/static-cow.glb' }, createdAt: '2026-10-13T04:00:00Z', phase: '当前小牛上色完成', assets: [{ kind: 'model', name: '当前小牛上色.glb', url: '/files/outputs/fixture/painted-cow.glb' }] },
      { id: 'texture-other', kind: 'texture', pipelineStage: 'texture', engineId: 'hunyuan-paint', status: 'running', sourceModel: { url: '/files/outputs/fixture/another-cow.glb' }, createdAt: '2026-10-13T05:00:00Z', phase: '其他模型专属任务', assets: [] }
    );
    await page.locator('[data-action="refresh"]').click(); await page.waitForSelector('#texture-stage-progress:not([hidden])');
    assert.match(await page.locator('#texture-stage-progress').innerText(), /当前小牛上色完成/);
    assert.doesNotMatch(await page.locator('#texture-stage-progress').innerText(), /其他模型专属任务/);
    assert.equal(await page.locator('#texture-stage-progress a[download]').getAttribute('href'), '/files/outputs/fixture/painted-cow.glb?download=1');
    assert.equal(await page.locator('#texture-stage-progress [data-action="use-model"]').count(), 0, 'Completed texturing does not offer retired rigging or motion creation');
    mark('inline post-processing progress selects this model and exposes the actual completed output');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const textureScroll = await page.evaluate(() => window.scrollY); assert.ok(textureScroll > 0, 'The fixture exercises a long page');
    await page.waitForResponse(response => response.url().endsWith('/api/state'), { timeout: 7000 });
    assert.equal(await page.evaluate(() => window.scrollY), textureScroll, 'Same-page polling keeps the reading position');
    await go('home'); assert.equal(await page.evaluate(() => window.scrollY), 0, 'Changing destination starts at the page heading');
    mark('navigation resets to the heading while background polling preserves scroll');

    await go('setup');
    assert.equal(await page.locator('#setup-filter option[value="video"], #setup-filter option[value="rig"], #setup-filter option[value="animation"]').count(), 0);
    for (const id of ['catalog-video', 'video-missing', 'catalog-rig', 'catalog-motion']) assert.equal(await page.locator(`article[data-model-id="${id}"]`).count(), 0, 'Even a stale catalog may not expose retired capabilities');
    await page.locator('#setup-filter').selectOption('edit');
    await page.locator('[data-model-id="catalog-edit"] [data-setup-action="use-model"]').click();
    await page.waitForSelector('#edit-page:not([hidden])'); assert.equal(await page.locator('#edit-engine').inputValue(), 'edit-fixture');
    await page.locator('#edit-engine').selectOption('edit-missing');
    assert.equal(await page.locator('#edit-submit').isDisabled(), true); assert.equal(await page.locator('#edit-engine').inputValue(), 'edit-missing');
    await page.locator('#edit-readiness [data-setup-model="edit-missing"]').click();
    await page.waitForSelector('#setup-page:not([hidden])');
    assert.equal(await page.locator('article[data-model-id="edit-missing"]').isVisible(), true);
    assert.equal(mutations.some(item => item.url.startsWith('/api/setup/')), false, 'Navigation never installs a model');
    mark('installed model opens exact capability and missing selection opens exact install card');
    await go('edit'); await page.locator('#edit-engine').selectOption('edit-fixture');

    await go('results');
    assert.equal(await page.locator('#results-list [data-action="use-model"], #results-list [data-action="continue-image"][data-media-page="video"]').count(), 0, 'Result cards only offer retained creative capabilities');
    assert.equal(await page.locator('[data-result-task-id="result-image"] .task-variant').count(), 0, 'Fixed image engines do not invent a legacy Qwen variant');
    imageJob.weightVariant = 'explicit-fixture-variant'; await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(() => document.querySelector('[data-result-task-id="result-image"]').textContent.includes('explicit-fixture-variant'));
    delete imageJob.weightVariant; await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(() => !document.querySelector('[data-result-task-id="result-image"]').textContent.includes('explicit-fixture-variant'));
    assert.doesNotMatch(await page.locator('[data-result-task-id="result-image"]').innerText(), /Q4_K/);
    mark('result precision displays explicit provenance without inventing a fixed-engine variant');

    // BEGIN focused result-detail acceptance
    await go('results');
    const detailKeys = ['result-image:metadata', 'result-image:prompt', 'static-cow:source-images'];
    for (const key of detailKeys) {
      const details = page.locator(`[data-result-details="${key}"]`);
      assert.equal(await details.evaluate(element => element.open), false);
      await details.locator(':scope > summary').focus(); await page.keyboard.press('Enter');
      assert.equal(await details.evaluate(element => element.open), true, `${key} opens from the keyboard`);
    }
    const originalTaskName = snapshot.resultTasks[0].name;
    snapshot.resultTasks[0].name = `${originalTaskName} · 轮询刷新一次`;
    await page.waitForFunction(name => document.querySelector('[data-result-task-id="result-image"] h2').textContent === name, snapshot.resultTasks[0].name, { timeout: 10000 });
    for (const key of detailKeys) assert.equal(await page.locator(`[data-result-details="${key}"]`).evaluate(element => element.open), true, `${key} stays open after a changed snapshot rerenders results`);
    assert.equal(await page.locator('[data-result-details="static-cow:metadata"]').evaluate(element => element.open), false, 'Expansion does not leak into another task');
    for (const key of detailKeys) {
      const details = page.locator(`[data-result-details="${key}"]`);
      await details.locator(':scope > summary').focus(); await page.keyboard.press('Space');
      assert.equal(await details.evaluate(element => element.open), false, `${key} closes from the keyboard`);
    }
    snapshot.resultTasks[0].name = originalTaskName;
    await page.waitForFunction(name => document.querySelector('[data-result-task-id="result-image"] h2').textContent === name, originalTaskName, { timeout: 10000 });
    for (const key of detailKeys) assert.equal(await page.locator(`[data-result-details="${key}"]`).evaluate(element => element.open), false, `${key} stays closed after the next polling update`);
    mark('result metadata, image prompt and auxiliary sources keep keyboard expansion state through changed snapshots');
    // END focused result-detail acceptance

    for (const destination of ['edit', 'multiview']) {
      await go('results'); const countBefore = mutations.length;
      await page.locator('[data-result-task-id="result-image"] .asset-continue-actions > summary').click();
      await page.locator(`[data-result-task-id="result-image"] [data-action="continue-image"][data-media-page="${destination}"]`).first().click();
      await page.waitForSelector(`#${destination}-page:not([hidden])`); await page.waitForSelector(`#${destination}-input-preview img`);
      assert.equal(mutations.length, countBefore + 1, 'Continuing imports the actual output exactly once');
      assert.deepEqual(mutations.at(-1), { url: '/api/images/from-output', body: { url: generatedImage.url }, header: '1' });
      if (destination === 'multiview') assert.equal(await page.locator('#multiview-mode').inputValue(), 'multiview');
    }
    mark('generated image continues into editing and multiview without a new generation');
    await go('edit');
    const mediaInputId = await page.evaluate(() => JSON.parse(localStorage.getItem('2d3d-factory-draft-v1')).mediaForms.edit.inputs[0].id);
    const mediaInputRecord = snapshot.images[mediaInputId]; delete snapshot.images[mediaInputId];
    await page.locator('[data-action="refresh"]').click(); await page.waitForFunction(() => document.querySelector('#edit-submit').disabled);
    assert.match(await page.locator('#edit-page').innerText(), /失效|重新上传|不存在|重新导入/);
    snapshot.images[mediaInputId] = mediaInputRecord; await page.locator('[data-action="refresh"]').click();
    await page.waitForFunction(() => !document.querySelector('#edit-submit').disabled);
    mark('missing saved media reference is explained and blocks submission');

    await go('edit'); await page.locator('#edit-mode').selectOption('edit'); failMedia = true;
    await page.locator('#edit-submit').click(); await page.waitForSelector('#notification.error:not([hidden])');
    await page.locator('#notification details > summary').click();
    assert.match(await page.locator('#notification').innerText(), /测试故障/);
    await page.locator('[data-action="refresh"]').click(); assert.match(await page.locator('#notification').innerText(), /测试故障/, 'Refresh success cannot hide the failure detail');
    await page.waitForTimeout(10500); assert.equal(await page.locator('#notification').isVisible(), true, 'Errors remain until explicitly dismissed');
    await page.locator('#notification button').click(); assert.equal(await page.locator('#notification').isVisible(), false);
    const failedRequest = mutations.at(-1);
    await page.reload(); await page.waitForSelector('#connection-status.connected'); await go('edit');
    await page.locator('#edit-submit').click(); await page.waitForSelector('#notification.error:not([hidden])');
    assert.deepEqual(mutations.at(-1), failedRequest, 'Reopening and retrying identical input retains the pending idempotency key');
    await page.locator('#notification button').click();
    failMedia = false; mark('failure remains visible through refresh and former timeout until dismissed');

    const overflowEvidence = [], layoutFailures = [];
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 1280, height: 900 }, { width: 1024, height: 768 }, { width: 375, height: 812 }]) {
      await page.setViewportSize(viewport);
      for (const destination of ['home', 'imagegen', 'edit', 'multiview', 'workspace', 'texture', 'results', 'queue', 'setup', 'help']) {
        await go(destination);
        await page.evaluate(() => window.scrollTo(0, 0));
        const overflow = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, controls: [...document.querySelectorAll('main input,main textarea,main select,main button,main [role="button"],.navigation a')].filter(element => { const box = element.getBoundingClientRect(); return !element.closest('.sr-only') && box.width && box.height && getComputedStyle(element).visibility !== 'hidden' && (box.left < -1 || box.right > innerWidth + 1); }).map(element => ({ id: element.id, text: element.textContent.trim().slice(0, 60), left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right })) }));
        overflowEvidence.push({ viewport, destination, ...overflow });
        fs.writeFileSync(path.join(evidence, 'viewport-checks.json'), JSON.stringify(overflowEvidence, null, 2));
        if (overflow.scrollWidth > overflow.width + 1) fs.writeFileSync(path.join(evidence, 'overflow-elements.json'), JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('body *')].map(element => ({ element: element.tagName, id: element.id, className: String(element.className), right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width })).filter(item => item.width && item.right > innerWidth + 1)), null, 2));
        if (overflow.scrollWidth > overflow.width + 1) layoutFailures.push(`${destination} page overflows ${viewport.width}px: ${overflow.scrollWidth}px`);
        if (overflow.controls.length) layoutFailures.push(`${destination} has clipped controls at ${viewport.width}px: ${JSON.stringify(overflow.controls)}`);
        if (destination === 'edit') {
          const row = await page.locator('#edit-transparent-field').evaluate(element => ({ label: element.getBoundingClientRect().toJSON(), checkbox: element.querySelector('input').getBoundingClientRect().toJSON() }));
          if (row.checkbox.left - row.label.left > 8 || row.label.height > 48) layoutFailures.push(`Transparent-background checkbox separates from its label at ${viewport.width}px`);
        }
        if (destination === 'home') {
          const button = await page.locator('#start-pipeline').boundingBox();
          if (viewport.width >= 1280 && (button.y < 0 || button.y + button.height > viewport.height)) layoutFailures.push(`Primary creation action is outside the first screen at ${viewport.width}px`);
          const coversLoaded = await page.locator('.goal-cover').evaluateAll(elements => Promise.all(elements.map(element => new Promise(resolve => {
            const url = getComputedStyle(element).backgroundImage.match(/^url\(["']?(.*?)["']?\)$/)?.[1];
            if (!url) return resolve(false);
            const image = new Image(); image.onload = () => resolve(image.naturalWidth > 0); image.onerror = () => resolve(false); image.src = url;
          }))));
          assert.deepEqual(coversLoaded, [true, true], 'The two retained creative illustrations load');
          await page.mouse.move(0, 0); await page.screenshot({ path: path.join(evidence, `home-first-screen-${viewport.width}x${viewport.height}.png`) });
        }
        if (['home', 'edit', 'multiview', 'results', 'setup', 'texture'].includes(destination)) { await page.mouse.move(0, 0); await page.screenshot({ path: path.join(evidence, `${destination}-${viewport.width}x${viewport.height}.png`), fullPage: true }); }
      }
      await go('texture'); await page.locator('#texture-model-picker').click(); await page.waitForSelector('#model-picker-dialog[open]');
      const pickerBounds = await page.locator('#model-picker-dialog').evaluate(element => ({ box: element.getBoundingClientRect().toJSON(), clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }));
      if (pickerBounds.box.left < -1 || pickerBounds.box.right > viewport.width + 1 || pickerBounds.scrollWidth > pickerBounds.clientWidth + 1) layoutFailures.push(`Model picker overflows at ${viewport.width}px`);
      await page.mouse.move(0, 0); await page.screenshot({ path: path.join(evidence, `model-picker-${viewport.width}x${viewport.height}.png`) });
      await page.locator('#close-model-picker').click();
    }
    fs.writeFileSync(path.join(evidence, 'viewport-checks.json'), JSON.stringify(overflowEvidence, null, 2));
    assert.deepEqual(layoutFailures, [], 'All pages must remain within their viewport');
    assert.deepEqual(pageErrors, []); assert.deepEqual(unexpectedRequests, []);
    assert.ok(mutations.every(item => item.header === '1'), 'All fixture mutations preserve local request protection');
    mark('10 retained destinations and shared model picker fit four viewport sizes; desktop creation action and artwork are visible');
    fs.writeFileSync(path.join(evidence, 'receipt.json'), JSON.stringify({ status: 'passed', productVersion, checks, mutations, unexpectedRequests, pageErrors, note: 'Isolated fixtures only. No runtime, weights, GPU inference or user browser.' }, null, 2));
  } catch (error) {
    if (page && !page.isClosed()) { await page.screenshot({ path: path.join(evidence, 'failure.png'), fullPage: true }).catch(() => {}); fs.writeFileSync(path.join(evidence, 'failure-dom.txt'), await page.locator('body').innerText().catch(() => 'Unavailable')); }
    fs.writeFileSync(path.join(evidence, 'receipt.json'), JSON.stringify({ status: 'failed', checks, error: error.stack, mutations, unexpectedRequests, pageErrors }, null, 2));
    throw error;
  } finally { if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
