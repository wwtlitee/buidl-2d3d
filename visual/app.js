/* build something · 造点啥 v0.8.0 · Unclecow · API-backed UI */
(() => {
  'use strict';

  const STORAGE_KEY = '2d3d-factory-draft-v1';
  const MAX_OBJECTS = 20;
  const MAX_IMAGE_SEED = 2147483647;
  const MAX_MODEL_BYTES = 512 * 1024 * 1024;
  const APPEARANCE_LABELS = { texture: '有颜色 · 自带贴图', painted: '有颜色 · 彩色贴图', geometry: '只有形状 · 白模，需另行上色', vertex: '有颜色 · 顶点颜色，无独立贴图' };
  function colorRank(engine) { return ['texture', 'painted'].includes(engine.appearance) ? 0 : engine.appearance === 'vertex' ? 1 : engine.appearance === 'geometry' ? 3 : 2; }
  function coloredEngine(engine) { return colorRank(engine) < 2; }
  const VIEW_LABELS = { left: '左侧', back: '背面', right: '右侧' };
  const STATUS_LABELS = {
    queued: '排队中', running: '生产中', 'generated-unreviewed': '已生成',
    failed: '失败', cancelled: '已取消', interrupted: '已中断', imported: '已导入',
    ready: '已就绪', installed: '已安装', installing: '准备中', downloading: '下载中', missing: '未安装', blocked: '暂不可用'
  };
  const TERMINAL = new Set(['generated-unreviewed', 'failed', 'cancelled', 'interrupted']);
  const RETRYABLE = new Set(['failed', 'cancelled', 'interrupted']);
  const state = {
    api: null, connected: false, polling: false, page: 'home', queueFilter: 'all',
    objects: [], selectedEngines: new Set(), uploading: new Map(), busy: new Set(),
    openLogs: new Set(), preview: null, noticeTimer: null, pollTimer: null,
    engineSignature: '', imageEngineSignature: '', resultsSignature: '', previousJobs: new Map(), selectedBefore: false,
    selectedImageEngine: '', selectedWeightVariant: '', weightVariantSignature: '', imageUse: null, deletion: null, openResultDetails: new Set(), openAuxiliaries: new Set(), samplesChecked: false, samplesLoading: false,
    pipelineImageEngine: '', pipelineWeightVariant: '', pipelineEngines: new Set(), pipelineChosen: false, pipelineControlSignature: '', pipelineJobsSignature: '', pipelineRequest: null, pipelineMoreOpen: false,
    mediaForms: {}, preservedMediaDrafts: {}, activeError: false, referenceRevision: 0, recentReferences: new Map()
  };
  const $ = (selector, parent = document) => parent.querySelector(selector);

  function node(tag, attributes = {}, children = []) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value === null || value === undefined || (value === false && !name.startsWith('aria-'))) continue;
      if (name === 'class') element.className = value;
      else if (name === 'text') element.textContent = value;
      else if (name === 'dataset') Object.assign(element.dataset, value);
      else if (name === 'checked' || name === 'disabled' || name === 'hidden' || name === 'open') element[name] = value;
      else element.setAttribute(name, value === true ? '' : String(value));
    }
    for (const child of Array.isArray(children) ? children : [children]) {
      if (child !== null && child !== undefined) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return element;
  }

  function safeUrl(value) {
    if (typeof value !== 'string' || !value) return '';
    try {
      const url = new URL(value, window.location.origin);
      return url.origin === window.location.origin && ['http:', 'https:'].includes(url.protocol)
        ? `${url.pathname}${url.search}${url.hash}` : '';
    } catch { return ''; }
  }

  function imageRecord(value) {
    if (!value || typeof value.id !== 'string' || !value.id || !safeUrl(value.url)) return null;
    return { id: value.id, name: String(value.name || '图片'), url: safeUrl(value.url),
      width: Number(value.width) || 0, height: Number(value.height) || 0, bytes: Number(value.bytes) || 0 };
  }

  function newObject(image = null) {
    return { id: window.crypto?.randomUUID?.() || `object-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      name: image ? String(image.name).replace(/\.[^.]+$/, '').slice(0, 80) : `物体 ${String(state.objects.length + 1).padStart(2, '0')}`,
      main: image, auxiliary: {} };
  }

  const MEDIA_FIELDS = ['prompt', 'negative', 'width', 'height', 'steps', 'seed', 'strength', 'transparent'];
  function cachedReference(value, kind = 'image') {
    const record = imageRecord(value);
    if (!record || !/^[a-zA-Z0-9_-]{1,128}$/.test(record.id) || !/^\/files\/inputs\//.test(record.url) || /[?#]/.test(record.url)) return null;
    if (kind !== 'image' && value.kind !== kind) return null;
    return { ...record, ...(kind === 'image' ? {} : { kind }) };
  }
  function rememberReference(record, kind = 'image') {
    state.referenceRevision += 1;
    state.recentReferences.set(`${kind}:${record.id}`, { record, revision: state.referenceRevision });
    return record;
  }
  function referenceAvailable(record, kind = 'image') {
    if (!record) return false;
    const recent = state.recentReferences.get(`${kind}:${record.id}`)?.record;
    if (recent && safeUrl(recent.url) === safeUrl(record.url)) return true;
    const catalog = kind === 'image' ? state.api?.images : state.api?.mediaInputs;
    // Older API snapshots may omit the catalog; generation still performs server validation.
    if (catalog === undefined) return true;
    const current = kind === 'image' ? catalog?.[record.id] : Array.isArray(catalog) ? catalog.find(item => item.id === record.id && item.kind === kind) : null;
    return !!current && safeUrl(current.url) === safeUrl(record.url);
  }
  function unavailableMediaReferences(page) {
    const draft = state.mediaForms[page];
    const references = $(`#${page}-input-field`).hidden ? [] : draft.inputs.map(record => ({ record, kind: 'image' }));
    for (const name of ['mask', 'control', 'ipAdapter']) {
      if (draft[name] && !$(`#${page}-${name}-field`).hidden) references.push({ record: draft[name], kind: 'image' });
    }
    return references.filter(({ record, kind }) => !referenceAvailable(record, kind));
  }
  function mediaDrafts() {
    return Object.fromEntries(Object.entries(state.mediaForms).map(([page, draft]) => [page, {
      engineId: draft.engineId, mode: draft.mode, inputs: draft.inputs,
      ...Object.fromEntries(['mask', 'control', 'ipAdapter'].map(name => [name, draft[name]])),
      fields: Object.fromEntries(MEDIA_FIELDS.filter(name => $(`#${page}-${name}`)).map(name => {
        const field = $(`#${page}-${name}`); return [name, field.type === 'checkbox' ? field.checked : field.value];
      })),
      pendingRequest: draft.pendingRequest ? { signature: draft.pendingRequest.signature,
        requestId: draft.pendingRequest.body?.clientRequestId || draft.pendingRequest.requestId,
        seed: draft.pendingRequest.body?.seed ?? draft.pendingRequest.seed } : null
    }]));
  }
  function restoreMediaDrafts(saved) {
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return;
    // Keep every stored value of retired and unknown drafts when active forms save.
    state.preservedMediaDrafts = Object.fromEntries(Object.entries(saved).filter(([page]) => !Object.hasOwn(state.mediaForms, page)));
    for (const [page, draft] of Object.entries(state.mediaForms)) {
      const record = saved[page]; if (!record || typeof record !== 'object') continue;
      if (typeof record.engineId === 'string' && /^[a-zA-Z0-9_.-]{1,128}$/.test(record.engineId)) { draft.engineId = record.engineId; draft.explicitEngine = true; }
      if (MEDIA_MODES[record.mode] && mediaCategory(record.mode) === page) draft.mode = record.mode;
      draft.inputs = Array.isArray(record.inputs) ? record.inputs.slice(0, 20).map(item => cachedReference(item)).filter(Boolean) : [];
      for (const name of ['mask', 'control', 'ipAdapter']) draft[name] = cachedReference(record[name], 'image');
      draft.restoredFields = new Set();
      for (const name of MEDIA_FIELDS) {
        const field = $(`#${page}-${name}`), value = record.fields?.[name]; if (!field || value === undefined) continue;
        if (field.type === 'checkbox') { if (typeof value === 'boolean') field.checked = value; continue; }
        if (field.type === 'number') {
          if (!['number', 'string'].includes(typeof value) || String(value).trim() === '' || !Number.isFinite(Number(value))) continue;
          const previous = field.value; field.value = String(value);
          if (!field.checkValidity()) { field.value = previous; continue; }
        } else if (typeof value === 'string') field.value = value.slice(0, 3000);
        else continue;
        draft.restoredFields.add(name);
      }
      const pending = record.pendingRequest;
      if (pending && typeof pending.signature === 'string' && pending.signature.length <= 20000 && /^[a-f0-9]{32}$/.test(pending.requestId || '') && Number.isSafeInteger(pending.seed) && pending.seed >= 0 && pending.seed <= MAX_IMAGE_SEED) {
        draft.pendingRequest = { signature: pending.signature, requestId: pending.requestId, seed: pending.seed };
      }
    }
  }

  function restoreDraft() {
    try {
      const draft = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || 'null');
      restoreMediaDrafts(draft?.mediaForms);
      if (draft && Array.isArray(draft.objects)) {
        state.objects = draft.objects.filter(item => item && typeof item.id === 'string').slice(0, MAX_OBJECTS).map(item => ({
          id: item.id, name: String(item.name || '未命名物体').slice(0, 80), main: imageRecord(item.main),
          auxiliary: Object.fromEntries(Object.keys(VIEW_LABELS).map(view => [view, imageRecord(item.auxiliary?.[view])]).filter(([, image]) => image))
        }));
        if (Array.isArray(draft.engineIds)) {
          state.selectedEngines = new Set(draft.engineIds.filter(id => typeof id === 'string'));
          state.selectedBefore = true;
        }
        $('#batch-name').value = String(draft.name || '').slice(0, 100);
        const imageForm = draft.imageForm || {};
        $('#image-prompt').value = String(imageForm.prompt || '').slice(0, 3000);
        for (const dimension of ['width', 'height']) {
          if ([512, 768, 1024].includes(Number(imageForm[dimension]))) $(`#image-${dimension}`).value = String(imageForm[dimension]);
        }
        if (Number.isInteger(imageForm.steps) && imageForm.steps >= 1 && imageForm.steps <= 100) $('#image-steps').value = String(imageForm.steps);
        if (Number.isSafeInteger(imageForm.seed) && imageForm.seed >= 0 && imageForm.seed <= MAX_IMAGE_SEED) $('#image-seed').value = String(imageForm.seed);
        $('#image-random-seed').checked = imageForm.randomSeed !== false;
        $('#image-seed').disabled = $('#image-random-seed').checked;
        state.selectedImageEngine = typeof imageForm.engineId === 'string' ? imageForm.engineId : '';
        state.selectedWeightVariant = typeof imageForm.weightVariant === 'string' ? imageForm.weightVariant : '';
        const pipelineForm = draft.pipelineForm || {};
        $('#pipeline-prompt').value = String(pipelineForm.prompt || '').slice(0, 3000);
        state.pipelineImageEngine = typeof pipelineForm.imageEngineId === 'string' ? pipelineForm.imageEngineId : '';
        state.pipelineWeightVariant = typeof pipelineForm.weightVariant === 'string' ? pipelineForm.weightVariant : '';
        if (typeof pipelineForm.pendingRequest?.id === 'string' && /^[0-9a-f-]{36}$/i.test(pipelineForm.pendingRequest.id) && typeof pipelineForm.pendingRequest.signature === 'string') state.pipelineRequest = pipelineForm.pendingRequest;
        if (Array.isArray(pipelineForm.modelEngineIds)) {
          state.pipelineEngines = new Set(pipelineForm.modelEngineIds.filter(id => typeof id === 'string'));
          state.pipelineChosen = true;
        }
      }
    } catch { /* A damaged or disabled local storage must not prevent opening the factory. */ }
    if (!state.objects.length) state.objects.push(newObject());
  }

  function saveDraft() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ objects: state.objects, mediaForms: { ...state.preservedMediaDrafts, ...mediaDrafts() },
        engineIds: [...state.selectedEngines], name: $('#batch-name').value,
        imageForm: { prompt: $('#image-prompt').value, width: Number($('#image-width').value), height: Number($('#image-height').value),
          steps: Number($('#image-steps').value), seed: Number($('#image-seed').value), randomSeed: $('#image-random-seed').checked,
          engineId: state.selectedImageEngine, weightVariant: state.selectedWeightVariant },
        pipelineForm: { prompt: $('#pipeline-prompt').value, imageEngineId: state.pipelineImageEngine,
          weightVariant: state.pipelineWeightVariant, modelEngineIds: state.pipelineChosen ? [...state.pipelineEngines] : null,
          pendingRequest: state.pipelineRequest } }));
    } catch { /* The running queue lives on the server; local storage is optional for the draft. */ }
  }

  function errorExplanation(message, key = '', historicalOnly = false) {
    const detail = String(message || '未收到详细错误。');
    let title = '这一步没有完成', next = '请查看详细原因，确认输入与模型状态后再操作；提交中断时先查看任务，避免重复创建。';
    if (/out of memory|cuda.*memory|显存不足|显存.*不够/i.test(detail)) { title = '显存不够，生成没有完成'; next = '关闭其他占用显卡的程序，或降低图片尺寸后再试。需要更换模型时请手动选择。'; }
    else if (/timeout|timed out|超时|连接中断/i.test(detail)) { title = '暂时没有收到完成确认'; next = '先查看任务或安装进度。任务已经出现时继续等待；确认没有创建后，再按当前页面提示重试。'; }
    else if (/unauthorized|forbidden|401|403|授权|token/i.test(detail)) { title = '模型访问授权需要检查'; next = '到“模型与设置”查看授权要求，在官方页面获得访问权限后重新准备模型。'; }
    else if (/disk|no space|磁盘|空间不足/i.test(detail)) { title = '存储空间或文件访问需要检查'; next = '到“模型与设置”查看安装位置和可用空间，处理后再重试。'; }
    else if (/无法连接|failed to fetch|network|connection refused/i.test(detail)) { title = '暂时无法连接本机引擎'; next = '点击刷新检查连接。仍无法连接时重新打开应用，已完成作品会保留。'; }
    else if (/未安装|权重|not found|no such file|文件不存在|素材.*不存在/i.test(detail)) { title = '所需模型或素材尚未准备好'; next = '检查所选模型是否已安装、输入素材是否仍存在，再重新选择缺少的内容。'; }
    if (historicalOnly) { title = '历史任务没有完成'; next = '保留详细原因与运行记录。此功能已退出当前版本，不再提供重试；已生成的作品仍可预览、下载。'; }
    const details = node('details', { open: key && state.openLogs.has(`error:${key}`) }, [node('summary', { text: '查看详细原因' }), node('pre', { text: detail })]);
    if (key) details.addEventListener('toggle', () => { if (details.open) state.openLogs.add(`error:${key}`); else state.openLogs.delete(`error:${key}`); });
    return node('div', { class: 'error-explanation' }, [node('strong', { text: title }), node('p', { text: next }), details]);
  }

  function notify(message, error = false) {
    const banner = $('#notification');
    if (!error && state.activeError && !banner.hidden) {
      let update = $('#notification-update');
      if (!update) { update = node('p', { id: 'notification-update', role: 'status' }); banner.append(update); }
      update.textContent = String(message); announce(message); return;
    }
    state.activeError = error;
    banner.replaceChildren(error ? errorExplanation(message) : document.createTextNode(String(message)),
      node('button', { type: 'button', class: 'button small ghost', text: '关闭提示', dataset: { action: 'dismiss-notification' } }));
    banner.classList.toggle('error', error);
    banner.hidden = false;
    clearTimeout(state.noticeTimer);
    if (!error) state.noticeTimer = setTimeout(() => { banner.hidden = true; }, 4500);
  }

  function announce(message) { $('#live-announcement').textContent = message; }

  async function api(path, { method = 'GET', body, headers = {}, timeout = 15000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, { method, body, headers: { ...headers,
        ...(method === 'POST' ? { 'X-Factory-Request': '1' } : {}) },
        cache: 'no-store', signal: controller.signal });
      const contentType = response.headers.get('content-type') || '';
      let result;
      if (contentType.includes('application/json')) result = await response.json();
      else result = { error: (await response.text()).slice(0, 600) };
      if (!response.ok) throw new Error(typeof result.error === 'string' ? result.error : `请求失败（${response.status}）`);
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请查看队列状态后再操作。');
      if (error instanceof TypeError) throw new Error('暂时无法连接本机创作引擎，请重新打开应用。');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function bytes(value) {
    const count = Number(value) || 0;
    if (count >= 1024 ** 3) return `${(count / 1024 ** 3).toFixed(1)} GB`;
    if (count >= 1024 ** 2) return `${(count / 1024 ** 2).toFixed(1)} MB`;
    if (count >= 1024) return `${(count / 1024).toFixed(0)} KB`;
    return `${count} B`;
  }

  function duration(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    if (seconds < 60) return `${seconds} 秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
    return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
  }

  function timeLabel(value) {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }) : '';
  }

  function engineTitle(id) { return id === 'imported-model' ? '本机导入' : [...(state.api?.engines || []), ...(state.api?.imageEngines || []), ...(state.api?.mediaEngines || []), ...(state.api?.textureEngines || []), ...(state.api?.historicalEngines || []), ...(state.api?.rigEngines || []), ...(state.api?.animationEngines || [])].find(engine => engine.id === id)?.title || id || '未知工具'; }
  function engineAvailable(engine) { return engine?.creationEnabled !== false && ['ready', 'installed'].includes(engine?.status); }
  function engineAppearance(job) {
    if (job.kind === 'texture' || job.pipelineStage === 'texture') return 'painted';
    if (['rig', 'animation'].includes(job.kind) || job.inputMode === 'model') return '';
    const engine = (state.api?.engines || []).find(item => item.id === (job.engineId || job.engine));
    return job.appearance || engine?.appearance || '';
  }
  function weightVariants(engine) { return Array.isArray(engine?.weightVariants) ? engine.weightVariants.filter(variant => variant && typeof variant.id === 'string') : []; }
  function variantAvailable(variant) { return variant && ['ready', 'installed'].includes(variant.status) && variant.verified === true; }
  function imageVariantReady(engine, variant) { return !!engine && (!Array.isArray(engine.weightVariants) || variantAvailable(variant)); }
  function applyImageDefaults(engine) {
    for (const name of ['width','height','steps']) if (Number.isFinite(Number(engine?.defaults?.[name]))) $(`#image-${name}`).value=String(engine.defaults[name]);
  }
  function jobWeightVariant(job, taskVariant = '') {
    const recorded = job.weightVariant || job.request?.weightVariant || taskVariant;
    return recorded || ((job.engineId || job.engine) === 'qwen-image-21' ? 'Q4_K' : '');
  }
  function threeDJobs() { return (state.api?.batches || []).flatMap(batch => Array.isArray(batch.jobs) ? batch.jobs : []); }
  function imageJobs() { return Array.isArray(state.api?.imageJobs) ? state.api.imageJobs : []; }
  function mediaJobs() { return Array.isArray(state.api?.mediaJobs) ? state.api.mediaJobs : []; }
  function allJobs() { return [...new Map([...threeDJobs(), ...imageJobs(), ...mediaJobs()].map(job => [job.id, job])).values()]; }
  function isImageJob(job) { return imageJobs().some(item => item.id === job.id); }
  function validAssets(items) { return (Array.isArray(items) ? items : []).filter(asset => asset && safeUrl(asset.url)); }
  function extension(asset) { return String(asset.name || '').split('.').pop().toLowerCase(); }
  function isModel(asset) { return ['glb', 'gltf', 'obj', 'ply', 'stl'].includes(extension(asset)); }
  function isImage(asset) { return ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(extension(asset)); }
  function isVideo(asset) { return ['mp4', 'webm', 'mov'].includes(extension(asset)); }
  function isText(asset) { return ['json', 'log', 'txt', 'md', 'yaml', 'yml'].includes(extension(asset)); }

  const RETIRED_FEATURES = new Set(['video', 'rig', 'animation', 'motion']);
  const RETIRED_MODES = new Set(['txt2video', 'img2video', 'reference-video', 't2v', 'i2v']);
  function scopeNotice() {
    const message = '首版专注本地生图与 3D 创作。视频生成、绑骨和动作制作已退出当前版本；历史作品仍可预览、下载，原草稿保留。';
    const banner = $('#notification');
    clearTimeout(state.noticeTimer); state.activeError = true;
    banner.classList.remove('error'); banner.hidden = false;
    banner.replaceChildren(node('div', { id: 'scope-notice' }, [node('strong', { text: '此功能不在当前版本的创作范围内' }), node('p', { text: message })]),
      node('button', { type: 'button', class: 'button small ghost', text: '关闭提示', dataset: { action: 'dismiss-notification' } }));
    announce(message);
  }
  function retiredJob(job, task = null) {
    const engineId = job?.engineId || job?.engine;
    const retiredEngines = [...(state.api?.rigEngines || []), ...(state.api?.animationEngines || []), ...[...(state.api?.mediaEngines || []), ...(state.api?.historicalEngines || [])].filter(engine => engine.creationEnabled === false || RETIRED_FEATURES.has(engine.kind))];
    return job?.creationEnabled === false || task?.creationEnabled === false
      || [job?.kind, job?.pipelineStage, task?.kind, task?.pipelineStage].some(kind => RETIRED_FEATURES.has(kind))
      || RETIRED_MODES.has(job?.request?.mode || job?.mode)
      || retiredEngines.some(engine => engine.id === engineId);
  }

  function setPage(page) {
    if (RETIRED_FEATURES.has(page)) { scopeNotice(); history.replaceState(null, '', '#home'); page = 'home'; }
    const previousPage = state.page;
    state.page = ['home', 'workspace', 'imagegen', 'edit', 'multiview', 'texture', 'queue', 'results', 'setup', 'help'].includes(page) ? page : 'home';
    for (const item of ['home', 'workspace', 'imagegen', 'edit', 'multiview', 'texture', 'queue', 'results', 'setup', 'help']) {
      const panel = $(`#${item}-page`);
      if (panel) panel.hidden = item !== state.page;
    }
    const creation = ['home', 'workspace', 'imagegen', 'edit', 'multiview', 'texture'].includes(state.page);
    for (const link of document.querySelectorAll('[data-page]')) {
      const current = link.closest('#main-nav') ? link.dataset.page === (creation ? 'home' : state.page) : link.dataset.page === state.page;
      link.classList.toggle('active', current);
      if (current) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    if ($('#creation-nav')) $('#creation-nav').hidden = !creation;
    const title = { home: '开始创作', workspace: '图片变 3D', imagegen: '文字生成图片', edit: '生图与图片编辑', multiview: '补充多个视角', texture: '模型上色', queue: '任务进度', results: '我的作品', setup: '模型与设置', help: '使用指南' }[state.page];
    $('#page-crumb').textContent = creation && state.page !== 'home' ? `开始创作 / ${title}` : title;
    document.dispatchEvent(new CustomEvent('factory-page', { detail: state.page }));
    if (state.page === 'imagegen') { renderImageEngines(); renderImageJobs(); }
    if (state.page === 'home') { renderPipelineControls(); renderPipelines(); }
    if (state.mediaForms[state.page]) renderMediaForm(state.page);
    if (state.page === 'queue') renderQueue();
    if (state.page === 'results') renderResults();
    if (previousPage !== state.page) window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  }

  function actionButton(label, action, data = {}, className = 'button small') {
    return node('button', { type: 'button', class: className, text: label,
      dataset: { action, ...data }, disabled: state.busy.has(data.jobId || data.batchId || data.pipelineId || action) });
  }

  const MEDIA_MODES = { txt2img: '文字生成图片', img2img: '图生图', edit: '按描述编辑图片', inpaint: '局部重绘', control: '按控制图生成', multiview: '从主图补充其他角度' };
  const MODE_ALIASES = { t2i: 'txt2img' };
  function engineModes(engine) { return (Array.isArray(engine?.capabilities?.modes) ? engine.capabilities.modes : []).map(mode => MODE_ALIASES[mode] || mode).filter(mode => MEDIA_MODES[mode]); }
  function mediaCategory(mode) { return mode === 'multiview' ? 'multiview' : MEDIA_MODES[mode] ? 'edit' : ''; }
  function mediaEngines(page) { return (state.api?.mediaEngines || []).filter(engine => engine.creationEnabled !== false && !RETIRED_FEATURES.has(engine.kind) && engineModes(engine).some(mode => mediaCategory(mode) === page)); }
  function mediaField(page, name, label, attributes) {
    return node('label', { for: `${page}-${name}` }, [label, node('input', { id: `${page}-${name}`, ...attributes })]);
  }
  function initializeMediaForms() {
    const copy = {
      edit: ['生图与图片编辑', '从描述生图，也可改颜色、换材质或局部重绘。选择的模型决定可用功能。'],
      multiview: ['补充同一物体的其他角度。', '放入主图，生成辅助视角。生成结果需要检查一致性，再加入 3D 准备区。'],
    };
    for (const [page, [title, description]] of Object.entries(copy)) {
      state.mediaForms[page] = { engineId: '', mode: '', inputs: [], mask: null, control: null, ipAdapter: null, uploading: false, busy: false, signature: '', pendingRequest: null, explicitEngine: false };
      const form = node('section', { class: 'panel media-creation-panel', 'aria-label': title });
      const fields = node('div', { class: 'media-fields' }, [
        node('div', { class: 'media-settings media-engine-settings' }, [node('label', {}, ['选择模型', node('select', { id: `${page}-engine`, disabled: true })]), node('label', {}, ['想做什么', node('select', { id: `${page}-mode`, disabled: true })])]),
        node('div', { id: `${page}-readiness`, class: 'readiness-banner compact', role: 'status', hidden: true }),
        node('div', { id: `${page}-input-field`, class: 'media-reference-field' }, [mediaField(page, 'input', '输入图片', { type: 'file', accept: 'image/png,image/jpeg,image/webp', multiple: true }), node('div', { id: `${page}-input-preview`, class: 'media-reference-previews' }), node('p', { id: `${page}-input-note`, class: 'field-hint' })]),
        node('div', { id: `${page}-mask-field`, hidden: true }, [mediaField(page, 'mask', '重绘蒙版（与输入图同尺寸）', { type: 'file', accept: 'image/png,image/jpeg,image/webp' }), node('p', { class: 'field-hint', text: '白色区域重绘，黑色区域保留。只选择带局部重绘能力的模型。' })]),
        node('div', { id: `${page}-control-field`, hidden: true }, [mediaField(page, 'control', '控制图片', { type: 'file', accept: 'image/png,image/jpeg,image/webp' })]),
        node('div', { id: `${page}-ipAdapter-field`, hidden: true }, [mediaField(page, 'ipAdapter', '外观参考图片（可选）', { type: 'file', accept: 'image/png,image/jpeg,image/webp' })]),
        node('label', { id: `${page}-transparent-field`, class: 'checkbox-row', hidden: true }, [node('input', { id: `${page}-transparent`, type: 'checkbox' }), '生成透明背景 PNG']),
        node('label', { for: `${page}-prompt` }, ['描述你想生成的内容', node('textarea', { id: `${page}-prompt`, rows: '4', maxlength: '3000', placeholder: page === 'multiview' ? '可选：同一物体的左侧、背面和右侧，保持颜色与比例。' : '例如：把小牛的外壳改成橙色，保持形状和背景。' })]),
        node('details', { class: 'advanced-options' }, [node('summary', { text: '高级设置 · 尺寸、步数与随机种子' }),
          node('div', { class: 'media-settings' }, [mediaField(page, 'width', '宽度', { type: 'number', min: 128, max: 4096, step: 8, value: 512 }), mediaField(page, 'height', '高度', { type: 'number', min: 128, max: 4096, step: 8, value: 512 }), mediaField(page, 'steps', '步数', { type: 'number', min: 1, max: 100, value: 8 }), mediaField(page, 'seed', '随机种子（-1 随机）', { type: 'number', min: -1, max: 2147483647, step: 1, value: -1 })]),
          node('div', { id: `${page}-strength-field`, class: 'media-settings', hidden: true }, [mediaField(page, 'strength', '变化幅度（0–1）', { type: 'number', min: 0, max: 1, step: .05, value: .65 })]),
          node('label', { for: `${page}-negative` }, ['不想出现的内容（可选）', node('textarea', { id: `${page}-negative`, rows: 2, maxlength: 3000 })])])
      ]);
      form.append(fields, node('div', { class: 'imagegen-submit' }, [node('span', { id: `${page}-note`, role: 'status', text: '正在读取模型能力…' }), node('button', { id: `${page}-submit`, type: 'button', class: 'button primary', disabled: true, text: page === 'multiview' ? '生成辅助视角' : '生成图片', dataset: { action: 'start-media', mediaPage: page } })]));
      $(`#${page}-page`).append(node('div', { class: 'page-heading' }, [node('div', {}, [node('div', { class: 'eyebrow', text: 'BUILD SOMETHING' }), node('h1', { text: title }), node('p', { text: description })]), node('a', { class: 'button', href: '#setup', text: '下载与安装模型 →' })]), form,
        node('div', { class: 'imagegen-jobs-heading' }, [node('h2', { text: '生成记录' }), node('span', { text: '与其他生成共用本机队列 · 已生成的结果仍需检查' })]), node('div', { id: `${page}-jobs`, class: 'batches-list' }));
      if (page === 'multiview') $(`#${page}-prompt`).value = '同一物体的其他角度，保持颜色、形状和比例。';
      for (const name of ['mask', 'control', 'ipAdapter']) $(`#${page}-${name}`).after(node('div', { id: `${page}-${name}-record`, class: 'media-restored-reference' }));
      $(`#${page}-engine`).addEventListener('change', () => { state.mediaForms[page].engineId = $(`#${page}-engine`).value; state.mediaForms[page].explicitEngine = true; state.mediaForms[page].mode = ''; renderMediaForm(page, true); saveDraft(); });
      $(`#${page}-mode`).addEventListener('change', () => { state.mediaForms[page].mode = $(`#${page}-mode`).value; renderMediaForm(page); saveDraft(); });
      for (const name of ['input', 'mask', 'control', 'ipAdapter']) $(`#${page}-${name}`).addEventListener('change', event => uploadMediaReference(page, name, [...event.target.files]));
      fields.addEventListener('input', () => { updateMediaSummary(page); saveDraft(); });
    }
  }
  function renderMediaForm(page, applyDefaults = false) {
    const draft = state.mediaForms[page]; if (!draft) return;
    const engines = mediaEngines(page);
    if (!draft.engineId) draft.engineId = (engines.find(engineAvailable) || engines[0])?.id || '';
    const engine = engines.find(item => item.id === draft.engineId);
    const modes = engineModes(engine).filter(mode => mediaCategory(mode) === page);
    if (!draft.mode && modes.length) draft.mode = modes.includes('edit') ? 'edit' : modes[0];
    const signature = JSON.stringify([engines, draft.engineId, draft.mode]);
    if (signature !== draft.signature) {
      draft.signature = signature;
      const engineOptions = engines.map(item => node('option', { value: item.id, text: `${item.title} · ${STATUS_LABELS[item.status] || item.status}` }));
      if (draft.engineId && !engine) engineOptions.unshift(node('option', { value: draft.engineId, text: `已选模型 ${draft.engineId} 暂不可用` }));
      $(`#${page}-engine`).replaceChildren(...(engineOptions.length ? engineOptions : [node('option', { value: '', text: '暂无可用模型，请先下载部署' })]));
      $(`#${page}-engine`).value = draft.engineId; $(`#${page}-engine`).disabled = !engines.length;
      const modeOptions = modes.map(mode => node('option', { value: mode, text: MEDIA_MODES[mode] }));
      if (draft.mode && !modes.includes(draft.mode)) modeOptions.unshift(node('option', { value: draft.mode, text: `${MEDIA_MODES[draft.mode]} · 所选模型暂不支持` }));
      $(`#${page}-mode`).replaceChildren(...modeOptions);
      $(`#${page}-mode`).value = draft.mode; $(`#${page}-mode`).disabled = !modes.length;
      if (applyDefaults || !draft.defaultsApplied) {
        for (const name of ['width', 'height', 'steps', 'strength']) {
          if ($(`#${page}-${name}`) && (applyDefaults || !draft.restoredFields?.has(name)) && Number.isFinite(Number(engine?.defaults?.[name]))) $(`#${page}-${name}`).value = String(engine.defaults[name]);
        }
        draft.defaultsApplied = !!engine;
      }
    }
    const imageRequired = ['img2img', 'edit', 'inpaint', 'multiview'].includes(draft.mode);
    $(`#${page}-input-field`).hidden = ['txt2img', 'control'].includes(draft.mode);
    $(`#${page}-mask-field`).hidden = draft.mode !== 'inpaint';
    $(`#${page}-control-field`).hidden = draft.mode !== 'control';
    $(`#${page}-ipAdapter-field`).hidden = !engine?.capabilities?.inputTypes?.includes('ip-adapter');
    $(`#${page}-strength-field`).hidden = !['img2img', 'inpaint'].includes(draft.mode);
    $(`#${page}-transparent-field`).hidden = !engine?.capabilities?.supportsTransparency;
    if (engine && !engine.capabilities?.supportsTransparency) $(`#${page}-transparent`).checked = false;
    const maxImages = Math.max(1, Number(engine?.capabilities?.maxInputImages || engine?.capabilities?.maxReferenceImages) || 1); $(`#${page}-input`).multiple = maxImages > 1;
    $(`#${page}-input-note`).textContent = `${imageRequired ? '需要输入图片' : '可选参考图片'} · 此模型最多 ${maxImages} 张。${draft.uploading ? '正在导入…' : draft.inputs.length ? `已导入 ${draft.inputs.length} 张` : ''}`;
    $(`#${page}-input-preview`).replaceChildren(...draft.inputs.map((image, index) => node('div', { class: 'media-reference-preview' }, [node('img', { src: safeUrl(image.url), alt: image.name }), node('span', { text: image.name }),
      referenceAvailable(image) ? null : node('span', { class: 'error-caption', text: '素材已失效，请移除后重新导入' }),
      node('button', { type: 'button', class: 'button small ghost', text: '移除', disabled: draft.busy || draft.uploading, dataset: { action: 'remove-media-reference', mediaPage: page, referenceIndex: index } })])));
    for (const name of ['mask', 'control', 'ipAdapter']) {
      const reference = draft[name];
      $(`#${page}-${name}-record`).replaceChildren(...(reference ? [node('span', { text: `已选：${reference.name}` }),
        referenceAvailable(reference, 'image') ? null : node('span', { class: 'error-caption', text: '素材已失效，请移除后重新导入' }),
        node('button', { type: 'button', class: 'button small ghost', text: '移除', disabled: draft.busy || draft.uploading, dataset: { action: 'remove-media-reference', mediaPage: page, referenceName: name } })].filter(Boolean) : []));
    }
    const readiness = $(`#${page}-readiness`); readiness.hidden = !!engine && engineAvailable(engine);
    readiness.replaceChildren(node('div', {}, [node('strong', { text: engine ? `${engine.title} 尚未准备好` : '先下载一个对应模型' }), node('p', { text: engine?.error || engine?.note || '在工具内下载安装环境与权重，再回来开始。' })]), node('a', { class: 'button', href: '#setup', text: '查看部署与检测', dataset: engine ? { setupModel: engine.setupModelId || engine.id } : {} }));
    updateMediaSummary(page);
    const jobs = mediaJobs().filter(job => mediaCategory(MODE_ALIASES[job.request?.mode || job.mode] || job.request?.mode || job.mode) === page);
    $(`#${page}-jobs`).replaceChildren(...(jobs.length ? jobs.slice().reverse().map(renderJob) : [emptyState('还没有生成记录', '准备好模型和输入后，任务进度及产物会出现在这里。')]));
  }
  function updateMediaSummary(page) {
    const draft = state.mediaForms[page]; if (!draft) return;
    const engine = mediaEngines(page).find(item => item.id === draft.engineId);
    const imageRequired = ['img2img', 'edit', 'inpaint', 'multiview'].includes(draft.mode);
    const maxImages = Math.max(1, Number(engine?.capabilities?.maxInputImages || engine?.capabilities?.maxReferenceImages) || 1);
    const unavailable = unavailableMediaReferences(page);
    let message = !state.connected ? '正在连接本机创作引擎'
      : !engineAvailable(engine || {}) ? '先下载安装所选模型'
      : !engineModes(engine).includes(draft.mode) ? '所选模型不支持当前操作，请选择相应功能或模型'
      : draft.uploading ? '正在导入参考素材…'
      : draft.busy ? '正在提交任务…'
      : unavailable.length ? `${unavailable.length} 个素材已失效，请移除后重新导入；原选择已保留供你核对`
      : imageRequired && !draft.inputs.length ? '先放入输入图片'
      : draft.inputs.length > maxImages ? `此模型最多接受 ${maxImages} 张图，请移除多余图片`
      : draft.mode === 'inpaint' && !draft.mask ? '局部重绘还需要蒙版图片'
      : draft.mode === 'control' && !draft.control && !draft.ipAdapter ? '先放入控制图片或外观参考图'
      : !$(`#${page}-prompt`).value.trim() ? '写下描述，就可以开始' : '';
    const numberFields = ['width', 'height', 'steps', 'seed', ...(['img2img', 'inpaint'].includes(draft.mode) ? ['strength'] : [])];
    if (!message && numberFields.some(name => !$(`#${page}-${name}`).checkValidity())) message = '检查高级设置中的参数范围';
    $(`#${page}-submit`).disabled = !!message;
    $(`#${page}-note`).textContent = message || (engine?.status === 'ready' ? '本机已有成功记录；本次效果仍需检查' : '模型已安装，尚需本机生成试用');
  }
  function selectCreationModel(choice) {
    if (!choice || typeof choice.engineId !== 'string') return;
    const { page, engineId, weightVariant, mode } = choice;
    if (RETIRED_FEATURES.has(page) || RETIRED_MODES.has(mode)) { scopeNotice(); return; }
    if (page === 'workspace') {
      state.selectedEngines = new Set([engineId]); state.selectedBefore = true; renderEngines();
    } else if (page === 'imagegen') {
      const changed = state.selectedImageEngine !== engineId;
      state.selectedImageEngine = engineId;
      if (typeof weightVariant === 'string' && weightVariant) state.selectedWeightVariant = weightVariant;
      else if (changed) state.selectedWeightVariant = '';
      if (changed) applyImageDefaults((state.api?.imageEngines || []).find(engine => engine.id === engineId));
      renderImageEngines();
    } else if (state.mediaForms[page]) {
      const draft = state.mediaForms[page], changed = draft.engineId !== engineId;
      draft.engineId = engineId; draft.explicitEngine = true;
      if (MEDIA_MODES[mode] && mediaCategory(mode) === page) draft.mode = mode;
      else if (changed) draft.mode = '';
      renderMediaForm(page, changed);
    } else return;
    saveDraft(); window.location.hash = page; setPage(page);
  }

  function outputImageActions(asset, source, id, index, jobIndex = 0) {
    if (!isImage(asset) || !/\.(png|jpe?g|webp)$/i.test(asset.name) || !safeUrl(asset.url).startsWith('/files/outputs/')) return null;
    const data = source === 'task' ? { taskId: id, resultJobIndex: jobIndex } : { jobId: id };
    return node('details', { class: 'asset-continue-actions' }, [node('summary', { text: '用这张图继续创作' }),
      node('div', { class: 'asset-card-actions' }, [['edit', '继续编辑'], ['multiview', '补充视角']].map(([page, label]) => node('button', {
        type: 'button', class: 'button small', text: label, disabled: state.mediaForms[page]?.busy || state.mediaForms[page]?.uploading,
        dataset: { action: 'continue-image', mediaPage: page, source, assetIndex: index, ...data }
      }))) ]);
  }

  async function continueWithImage(button) {
    const page = button.dataset.mediaPage, draft = state.mediaForms[page];
    if (RETIRED_FEATURES.has(page)) { scopeNotice(); return; }
    if (!draft || draft.busy || draft.uploading) return;
    const asset = button.dataset.source === 'task'
      ? resultSelection(button.dataset.taskId, button.dataset.resultJobIndex, button.dataset.assetIndex).asset
      : validAssets(allJobs().find(job => job.id === button.dataset.jobId)?.assets)[Number(button.dataset.assetIndex)];
    const url = safeUrl(asset?.url);
    if (!asset || !isImage(asset) || !url.startsWith('/files/outputs/') || !/\.(png|jpe?g|webp)$/i.test(asset.name)) { notify('这张图片已不在当前作品记录中，请刷新作品列表后再试。', true); return; }
    draft.uploading = true; button.disabled = true; renderMediaForm(page);
    try {
      const imported = imageRecord(await api('/api/images/from-output', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }), timeout: 20000 }));
      if (!imported) throw new Error('图片未返回可用素材记录，请刷新作品后重新选择。');
      rememberReference(imported);
      const modes = page === 'multiview' ? ['multiview'] : ['edit', 'img2img', 'inpaint'];
      const engines = mediaEngines(page); let engine = engines.find(item => item.id === draft.engineId);
      let changed = false;
      if (!draft.explicitEngine && !modes.some(mode => engineModes(engine).includes(mode))) {
        const compatible = engines.find(item => engineAvailable(item) && modes.some(mode => engineModes(item).includes(mode))) || engines.find(item => modes.some(mode => engineModes(item).includes(mode)));
        if (compatible) { engine = compatible; changed = draft.engineId !== engine.id; draft.engineId = engine.id; }
      }
      draft.inputs = [imported]; draft.mask = null;
      draft.mode = modes.find(mode => engineModes(engine).includes(mode)) || modes[0];
      renderMediaForm(page, changed); saveDraft();
      window.location.hash = page; setPage(page);
      notify(`已带入“${asset.name}”，补充描述后即可继续。${engineModes(engine).includes(draft.mode) ? '' : '所选模型不支持该操作，请手动选择相应模型。'}`);
    } catch (error) { notify(error.message || '图片复用失败，请刷新作品后重试。', true); }
    finally { draft.uploading = false; button.disabled = false; renderMediaForm(page); }
  }
  async function uploadMediaReference(page, name, files) {
    const draft = state.mediaForms[page]; if (!draft || !['input', 'mask', 'control', 'ipAdapter'].includes(name) || !files.length || draft.uploading || draft.busy) return;
    const engine = mediaEngines(page).find(item => item.id === draft.engineId);
    const maxImages = Math.max(1, Number(engine?.capabilities?.maxInputImages || engine?.capabilities?.maxReferenceImages) || 1);
    if (name === 'input' && files.length > maxImages) { notify(`所选模型最多接受 ${maxImages} 张输入图，请重新选择。`, true); $(`#${page}-${name}`).value = ''; return; }
    draft.uploading = true; renderMediaForm(page);
    try {
      const records = [];
      for (const file of files) {
        records.push(await uploadImage(file));
      }
      if (name === 'input') draft.inputs = records; else draft[name] = records[0];
      saveDraft(); notify('参考素材已导入。');
    } catch (error) { notify(error.message || '素材导入失败，请重试。', true); $(`#${page}-${name}`).value = ''; }
    finally { draft.uploading = false; renderMediaForm(page); }
  }
  async function startMediaGeneration(page) {
    if (RETIRED_FEATURES.has(page)) { scopeNotice(); return; }
    const draft = state.mediaForms[page]; if (!draft) return; updateMediaSummary(page); if ($(`#${page}-submit`).disabled) return;
    const engine = mediaEngines(page).find(item => item.id === draft.engineId);
    const body = { engineId: draft.engineId, mode: draft.mode, prompt: $(`#${page}-prompt`).value.trim(), negativePrompt: $(`#${page}-negative`).value.trim(), width: Number($(`#${page}-width`).value), height: Number($(`#${page}-height`).value), steps: Number($(`#${page}-steps`).value), seed: Number($(`#${page}-seed`).value), inputImageIds: $(`#${page}-input-field`).hidden ? [] : draft.inputs.map(image => image.id) };
    if (draft.mode === 'inpaint') body.maskImageId = draft.mask.id;
    if (draft.mode === 'control' && draft.control) body.controlImageId = draft.control.id;
    if (engine?.capabilities?.supportsTransparency) body.transparentBackground = $(`#${page}-transparent`).checked;
    if (['img2img', 'inpaint'].includes(draft.mode)) body.strength = Number($(`#${page}-strength`).value);
    if (engine?.capabilities?.inputTypes?.includes('ip-adapter') && draft.ipAdapter) body.ipAdapterImageId = draft.ipAdapter.id;
    const signature = JSON.stringify(body);
    const pending = draft.pendingRequest?.signature === signature ? draft.pendingRequest : null;
    if (body.seed === -1) body.seed = pending ? pending.body?.seed ?? pending.seed : window.crypto.getRandomValues(new Uint32Array(1))[0] & MAX_IMAGE_SEED;
    body.clientRequestId = pending ? pending.body?.clientRequestId || pending.requestId : window.crypto.randomUUID().replaceAll('-', '');
    draft.pendingRequest = { signature, body }; saveDraft();
    draft.busy = true; updateMediaSummary(page);
    try { await api('/api/media-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft.pendingRequest.body), timeout: 30000 }); draft.pendingRequest = null; saveDraft(); notify('任务已加入本机生产队列，下方可查看进度，也可继续准备其他作品。'); await loadState(); }
    catch (error) { notify(error.message || '提交失败，请先查看队列后再重试。', true); }
    finally { draft.busy = false; renderMediaForm(page); }
  }

  function emptyState(title, description, symbol = '◇', link = null) {
    return node('div', { class: 'empty' }, [node('span', { class: 'empty-symbol', text: symbol, 'aria-hidden': 'true' }),
      node('h3', { text: title }), node('p', { text: description }), link ? node('a', { class: 'button', href: link.href, text: link.label }) : null]);
  }

  function renderSlot(object, view) {
    const main = view === 'main';
    const image = main ? object.main : object.auxiliary[view];
    const slot = node('div', { class: 'image-slot', dataset: { objectId: object.id, view, dropSlot: '1' } });
    if (state.uploading.has(`${object.id}:${view}`)) {
      slot.append(node('div', { class: 'uploading-slot', role: 'status' }, [node('span', { class: 'spinner', 'aria-hidden': 'true' }), '导入中…']));
    } else if (image) {
      const img = node('img', { src: safeUrl(image.url), alt: `${object.name}的${main ? '主图' : VIEW_LABELS[view]}`, draggable: 'false' });
      img.addEventListener('error', () => {
        img.hidden = true;
        if (!slot.querySelector('.error-caption')) slot.append(node('span', { class: 'error-caption', text: '图片无法读取，请重新导入' }));
      }, { once: true });
      slot.append(img, node('div', { class: 'image-actions' }, [
        actionButton('↻', 'pick-slot', { objectId: object.id, view }, 'image-action'),
        node('button', { type: 'button', class: 'image-action', text: '×', 'aria-label': `移除${main ? '主图' : VIEW_LABELS[view]}`, dataset: { action: 'remove-image', objectId: object.id, view } })
      ]), node('span', { class: 'image-filename', text: image.name, title: image.name }));
      slot.querySelector('[data-action="pick-slot"]').setAttribute('aria-label', `更换${main ? '主图' : VIEW_LABELS[view]}`);
    } else {
      slot.append(node('button', { class: 'upload-button', type: 'button', dataset: { action: 'pick-slot', objectId: object.id, view },
        'aria-label': `为${object.name}导入${main ? '主图' : VIEW_LABELS[view]}` }, [
        node('span', { class: 'upload-symbol', text: main ? '＋' : '+', 'aria-hidden': 'true' }),
        node('span', { text: main ? '点击或拖入主图' : '导入图片' }), main ? node('small', { text: '正面或最佳角度' }) : null
      ]));
    }
    return slot;
  }

  function renderObjects() {
    const list = $('#objects-list');
    const active = document.activeElement;
    const focusedObjectId = active?.dataset?.objectName;
    const selection = focusedObjectId ? [active.selectionStart, active.selectionEnd] : null;
    const cards = state.objects.map((object, index) => {
      const nameId = `object-name-${object.id}`;
      const card = node('article', { class: 'object-card' });
      card.append(node('div', { class: 'object-header' }, [
        node('span', { class: 'object-number', text: String(index + 1).padStart(2, '0'), 'aria-hidden': 'true' }),
        node('label', { class: 'sr-only', for: nameId, text: '物体名称' }),
        node('input', { id: nameId, class: 'object-name', value: object.name, maxlength: '80', placeholder: '物体名称', dataset: { objectName: object.id } }),
        node('button', { type: 'button', class: 'button icon-button ghost', text: '×', 'aria-label': `移除${object.name}`, dataset: { action: 'remove-object', objectId: object.id } })
      ]));
      const auxiliaries = node('details', { class: 'auxiliary-options', open: state.openAuxiliaries.has(object.id) || Object.keys(object.auxiliary).length > 0, dataset: { auxiliaryObject: object.id } }, [node('summary', {}, ['补充其他角度', node('span', { text: '可选，支持多图的工具才需要' })]),
        node('div', { class: 'aux-grid' }, Object.entries(VIEW_LABELS).map(([view, label]) => node('div', {}, [renderSlot(object, view), node('div', { class: 'aux-direction', text: label })]))),
        node('p', { class: 'aux-note', text: '每个方向 1 张，辅助图会交给支持多图的工具。' })]);
      card.append(node('div', { class: 'object-body' }, [node('div', {}, [node('div', { class: 'slot-heading' }, ['主图', node('span', { text: '必需' })]), renderSlot(object, 'main')]), auxiliaries]));
      return card;
    });
    list.replaceChildren(...cards);
    if (focusedObjectId) {
      const input = [...list.querySelectorAll('[data-object-name]')].find(item => item.dataset.objectName === focusedObjectId);
      if (input) { input.focus({ preventScroll: true }); input.setSelectionRange(...selection); }
    }
    $('#object-count').textContent = `${state.objects.length} / ${MAX_OBJECTS} 个物体`;
    const addButton = $('[data-action="add-object"]');
    addButton.disabled = state.objects.length >= MAX_OBJECTS;
    addButton.title = state.objects.length >= MAX_OBJECTS ? `每批最多 ${MAX_OBJECTS} 个物体，可先生产本批或移除一个物体` : '添加同一批次中的另一个物体';
    updateSummary();
  }

  function renderEngines() {
    const engines = [...(state.api?.engines || [])].sort((first, second) => colorRank(first) - colorRank(second));
    const signature = JSON.stringify([engines, [...state.selectedEngines]]);
    if (signature === state.engineSignature) return;
    state.engineSignature = signature;
    const focusedId = document.activeElement?.dataset?.engineId;
    const cards = engines.map(engine => {
      const available = engineAvailable(engine);
      const selected = state.selectedEngines.has(engine.id);
      const description = engine.multiView ? '多视角：主图 + 同一物体的其他角度，生成一个模型' : '一张图：使用主图，生成一个模型';
      const rawAppearance = String(engine.appearance || '');
      const appearance = APPEARANCE_LABELS[rawAppearance] || (/^[\u4e00-\u9fff\s，、（）+]+$/.test(rawAppearance) ? rawAppearance : '');
      const note = Array.isArray(engine.notes) ? engine.notes.join('；') : String(engine.notes || '');
      const content = node('div', { class: 'engine-content' }, [
        node('div', { class: 'engine-title-row' }, [node('span', { class: 'engine-title', text: engine.title }), node('span', { class: 'engine-kind', text: engine.multiView ? '多视角' : '一张图' })]),
        node('p', { class: 'engine-description', text: description + (appearance ? ` · ${appearance}` : '') }),
        node('div', { class: `engine-state ${engine.status}` }, [node('span', { class: 'dot', 'aria-hidden': 'true' }), STATUS_LABELS[engine.status] || engine.status])
      ]);
      if (!available && note) content.append(node('p', { class: 'engine-note', text: note }));
      if (!available) content.append(node('a', { class: 'inline-link engine-setup-link', href: '#setup', text: '查看安装与解决步骤 →', dataset: { setupModel: engine.id } }));
      if (selected && !available) content.append(actionButton('移除此选择', 'remove-engine-selection', { engineId: engine.id }, 'button small ghost'));
      return node('label', { class: `engine-card${selected ? ' selected' : ''}${available ? '' : ' unavailable'}`, title: note }, [
        node('input', { type: 'checkbox', checked: selected, disabled: !available, dataset: { engineId: engine.id }, 'aria-label': `${engine.title}，${description}，${STATUS_LABELS[engine.status] || engine.status}` }), content
      ]);
    });
    for (const id of state.selectedEngines) {
      if (!engines.some(engine => engine.id === id)) cards.unshift(node('div', { class: 'engine-card unavailable' }, [node('p', { text: `已选模型 ${id} 暂不可用，保留选择等待恢复。` }), actionButton('移除此选择', 'remove-engine-selection', { engineId: id }, 'button small ghost')]));
    }
    $('#engines-list').replaceChildren(...(cards.length ? cards : [emptyState('先安装一个建模工具', '检测电脑并安装推荐模型，就能开始。', '◇', { href: '#setup', label: '检测并安装模型' })]));
    if (focusedId) [...$('#engines-list').querySelectorAll('input')].find(input => input.dataset.engineId === focusedId)?.focus();
    updateEngineFilter();
    updateSummary();
  }

  function updateEngineFilter() {
    const engines = [...new Map([...(state.api?.engines || []), ...(state.api?.imageEngines || []), ...(state.api?.mediaEngines || []), ...(state.api?.textureEngines || []), ...(state.api?.historicalEngines || []), ...(state.api?.rigEngines || []), ...(state.api?.animationEngines || [])].map(engine => [engine.id, engine])).values()];
    const filter = $('#result-engine-filter');
    const filterFocused = document.activeElement === filter;
    const selectedFilter = filter.value;
    filter.replaceChildren(node('option', { value: '', text: '全部工具' }), ...engines.map(engine => node('option', { value: engine.id, text: engine.title })));
    if (engines.some(engine => engine.id === selectedFilter)) filter.value = selectedFilter;
    if (filterFocused) filter.focus({ preventScroll: true });
  }

  function renderImageEngines() {
    const engines = state.api?.imageEngines || [];
    if (!state.selectedImageEngine) {
      state.selectedImageEngine = (engines.find(engineAvailable) || engines[0])?.id || '';
      applyImageDefaults(engines.find(engine=>engine.id===state.selectedImageEngine));
    }
    const signature = JSON.stringify([engines, state.selectedImageEngine]);
    if (signature !== state.imageEngineSignature) {
      state.imageEngineSignature = signature;
      const cards = engines.map(engine => {
        const available = engineAvailable(engine);
        const selected = engine.id === state.selectedImageEngine;
        const note = Array.isArray(engine.notes) ? engine.notes.join('；') : String(engine.notes || '');
        return node('label', { class: `engine-card imagegen-engine-card${selected ? ' selected' : ''}${available ? '' : ' unavailable'}`, title: note }, [
          node('input', { type: 'radio', name: 'image-engine', checked: selected, disabled: !available, dataset: { imageEngineId: engine.id }, 'aria-label': `${engine.title}，${STATUS_LABELS[engine.status] || engine.status}` }),
          node('div', { class: 'engine-content' }, [node('div', { class: 'engine-title-row' }, [node('span', { class: 'engine-title', text: engine.title }), node('span', { class: 'engine-kind', text: '本地生图' })]),
            node('div', { class: `engine-state ${engine.status}` }, [node('span', { class: 'dot', 'aria-hidden': 'true' }), STATUS_LABELS[engine.status] || engine.status]),
            note ? node('p', { class: 'engine-note', text: note }) : null,
            !available ? node('a', { class: 'inline-link engine-setup-link', href: '#setup', text: '查看安装与解决步骤 →', dataset: { setupModel: engine.id } }) : null])
        ]);
      });
      if (state.selectedImageEngine && !engines.some(engine => engine.id === state.selectedImageEngine)) cards.unshift(emptyState('已选生图模型暂不可用', `保留了你的选择：${state.selectedImageEngine}。请重新检测安装状态，或明确选择另一个模型。`, '▧', { href: '#setup', label: '检查所选模型' }));
      $('#image-engines-list').replaceChildren(...(cards.length ? cards : [emptyState('先准备生图模型', '安装好一个生图模型，就可以把想法变成图片。', '▧', { href: '#setup', label: '安装生图模型' })]));
      updateEngineFilter();
    }
    renderWeightVariants();
    updateImageSummary();
  }

  function renderWeightVariants() {
    const engine = (state.api?.imageEngines || []).find(item => item.id === state.selectedImageEngine);
    if (engine && !Array.isArray(engine.weightVariants)) state.selectedWeightVariant='';
    const variants = weightVariants(engine);
    if (!state.selectedWeightVariant && variants.length) {
      state.selectedWeightVariant = engine.defaultWeightVariant || 'Q4_K_M';
      saveDraft();
    }
    const signature = JSON.stringify([state.selectedImageEngine, variants, state.selectedWeightVariant]);
    const select = $('#image-weight-variant');
    if (signature !== state.weightVariantSignature) {
      state.weightVariantSignature = signature;
      const focused = document.activeElement === select;
      const options = variants.map(variant => node('option', { value: variant.id, disabled: !variantAvailable(variant),
        text: `${variant.label || variant.id}${variant.legacy ? ' · 原已验证档位' : variant.id === engine.defaultWeightVariant ? ' · 推荐' : ''} · ${bytes(variant.bytes)} · ${STATUS_LABELS[variant.status] || '暂不可用'}` }));
      if (state.selectedWeightVariant && !variants.some(variant => variant.id === state.selectedWeightVariant)) {
        options.unshift(node('option', { value: state.selectedWeightVariant, disabled: true, text: `${state.selectedWeightVariant} · 当前不可用，请重新选择` }));
      }
      select.replaceChildren(...(options.length ? options : [node('option', { value: '', text: engine && !Array.isArray(engine.weightVariants) ? '使用安装卡的固定档位' : '暂未读取到可用版本' })]));
      select.value = state.selectedWeightVariant;
      if (focused) select.focus({ preventScroll: true });
    }
    select.disabled = !state.connected || !variants.length;
    const selected = variants.find(variant => variant.id === state.selectedWeightVariant);
    $('#image-weight-note').textContent = engine && !Array.isArray(engine.weightVariants) ? '此工具使用安装卡中固定的模型档位，无需另选Qwen量化版本。' : !selected ? '选择已安装并完成校验的量化版本。'
      : !variantAvailable(selected) ? `${selected.label || selected.id} ${STATUS_LABELS[selected.status] || '暂不可用'}，完成安装与校验后即可生成。`
      : selected.gpuVerified ? '此档位已完成本机生图实测。' : '权重已完成校验，此档位的本机生图效果与耗时尚未实测。';
    const missingVariants = variants.filter(variant => !variantAvailable(variant));
    const installs = $('#image-variant-installs');
    installs.hidden = !missingVariants.length;
    installs.replaceChildren(...missingVariants.map(variant => node('a', { class: 'inline-link', href: '#setup',
      text: `安装 ${variant.label || variant.id} →`, dataset: { setupEngine: engine.id, setupVariant: variant.id } })));
  }

  function imageFormValid() {
    const steps = Number($('#image-steps').value);
    const seed = Number($('#image-seed').value);
    return Boolean($('#image-prompt').value.trim()) && Number.isInteger(steps) && steps >= 1 && steps <= 100
      && ($('#image-random-seed').checked || (Number.isSafeInteger(seed) && seed >= 0 && seed <= MAX_IMAGE_SEED && $('#image-seed').value !== ''));
  }

  function updateImageSummary() {
    const engine = (state.api?.imageEngines || []).find(item => item.id === state.selectedImageEngine);
    const selectedVariant = weightVariants(engine).find(variant => variant.id === state.selectedWeightVariant);
    const available = engine && engineAvailable(engine);
    const variantReady=imageVariantReady(engine,selectedVariant);
    if (state.page === 'imagegen' && available && !variantReady) $('#image-advanced').open = true;
    const submit = $('#start-imagegen');
    submit.disabled = !state.connected || !available || !variantReady || !imageFormValid() || state.busy.has('start-imagegen');
    submit.textContent = state.busy.has('start-imagegen') ? '提交中…' : '生成图片';
    $('#imagegen-ready-note').textContent = !state.connected ? '正在连接本机创作引擎…'
      : !engine ? state.selectedImageEngine ? `已选模型 ${state.selectedImageEngine} 暂不可用，请检查安装或重新选择` : '本地生图工具尚未就绪'
      : !available ? `${engine.title} ${STATUS_LABELS[engine.status] || engine.status}，完成后即可生成`
      : !variantReady ? '在高级设置中选择已安装并完成校验的模型版本'
      : !$('#image-prompt').value.trim() ? '写下图片描述，就可以开始'
      : !imageFormValid() ? '步数需为 1–100，种子需为 0–2147483647 的整数'
      : '图片生成后，可直接选作 3D 主图';
  }

  async function startImageGeneration() {
    if ($('#start-imagegen').disabled) return;
    state.busy.add('start-imagegen'); updateImageSummary();
    const imageEngine=(state.api?.imageEngines || []).find(engine=>engine.id===state.selectedImageEngine);
    const request={engineId:state.selectedImageEngine,prompt:$('#image-prompt').value.trim(),
      width:Number($('#image-width').value),height:Number($('#image-height').value),steps:Number($('#image-steps').value),
      seed:$('#image-random-seed').checked ? null : Number($('#image-seed').value)};
    if (Array.isArray(imageEngine?.weightVariants)) request.weightVariant=state.selectedWeightVariant;
    try {
      await api('/api/image-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request), timeout: 20000 });
      saveDraft(); await loadState(); notify('生图任务已加入队列，图片完成后会自动出现。');
      announce('本地生图任务已创建。');
    } catch (error) {
      notify(`${error.message} 提交时若连接中断，请先检查生成记录，避免重复提交。`, true);
      await loadState();
    } finally { state.busy.delete('start-imagegen'); updateImageSummary(); }
  }

  function pipelineSelections() {
    const imageEngine = (state.api?.imageEngines || []).find(engine => engine.id === state.pipelineImageEngine);
    const variant = weightVariants(imageEngine).find(item => item.id === state.pipelineWeightVariant);
    const engines = (state.api?.engines || []).filter(engine => state.pipelineEngines.has(engine.id));
    return { imageEngine, variant, engines };
  }

  function renderPipelineControls() {
    const imageEngines = state.api?.imageEngines || [];
    const engines = state.api?.engines || [];
    if (!state.pipelineImageEngine) state.pipelineImageEngine = (imageEngines.find(engineAvailable) || imageEngines[0])?.id || '';
    const imageEngine = imageEngines.find(engine => engine.id === state.pipelineImageEngine);
    if (imageEngine && !Array.isArray(imageEngine.weightVariants)) state.pipelineWeightVariant='';
    const variants = weightVariants(imageEngine);
    if (!state.pipelineWeightVariant && variants.length) state.pipelineWeightVariant = imageEngine.defaultWeightVariant || variants[0].id;
    if (!state.pipelineChosen) {
      const singleImage = engines.filter(engine => engineAvailable(engine) && !engine.multiView);
      const recommended = singleImage.sort((first, second) => colorRank(first) - colorRank(second)).find(coloredEngine);
      if (recommended) { state.pipelineEngines.add(recommended.id); state.pipelineChosen = true; }
    }
    const signature = JSON.stringify([imageEngines, engines, state.pipelineImageEngine, state.pipelineWeightVariant, [...state.pipelineEngines]]);
    if (signature !== state.pipelineControlSignature) {
      const focusedModel = document.activeElement?.dataset?.pipelineEngineId;
      const focusedControl = ['pipeline-image-engine', 'pipeline-weight-variant'].includes(document.activeElement?.id) ? document.activeElement.id : null;
      state.pipelineControlSignature = signature;
      const imageSelect = $('#pipeline-image-engine');
      const imageOptions = imageEngines.map(engine => node('option', { value: engine.id, text: `${engine.title} · ${STATUS_LABELS[engine.status] || engine.status}` }));
      if (state.pipelineImageEngine && !imageEngine) imageOptions.unshift(node('option', { value: state.pipelineImageEngine, text: '已选生图模型暂不可用，请重新选择' }));
      imageSelect.replaceChildren(...(imageOptions.length ? imageOptions : [node('option', { value: '', text: '先安装一个生图模型' })]));
      imageSelect.value = state.pipelineImageEngine; imageSelect.disabled = !imageOptions.length;
      const variantSelect = $('#pipeline-weight-variant');
      const variantOptions = variants.map(variant => node('option', { value: variant.id, text: `${variant.label || variant.id} · ${STATUS_LABELS[variant.status] || '暂不可用'}` }));
      if (state.pipelineWeightVariant && !variants.some(variant => variant.id === state.pipelineWeightVariant)) variantOptions.unshift(node('option', { value: state.pipelineWeightVariant, text: `${state.pipelineWeightVariant} · 暂不可用` }));
      variantSelect.replaceChildren(...(variantOptions.length ? variantOptions : [node('option', { value: '', text: imageEngine && !Array.isArray(imageEngine.weightVariants) ? '使用此模型的固定档位' : '先准备生图模型版本' })]));
      variantSelect.value = state.pipelineWeightVariant; variantSelect.disabled = !variantOptions.length;
      const engineCards = engines.map(engine => {
        const available = engineAvailable(engine) && !engine.multiView;
        return node('label', { class: `pipeline-engine-option${state.pipelineEngines.has(engine.id) ? ' selected' : ''}${available ? '' : ' unavailable'}` }, [
          node('input', { type: 'checkbox', checked: state.pipelineEngines.has(engine.id), disabled: !available, dataset: { pipelineEngineId: engine.id }, 'aria-label': engine.title }),
          node('span', {}, [node('strong', { text: engine.title }), node('span', { class: 'pipeline-model-facts', text: `${engine.multiView ? '多视角图片' : '一张图'} · ${APPEARANCE_LABELS[engine.appearance] || '外观效果请查看模型说明'}` }),
            engine.multiView ? node('small', { text: '一键流程只生成一张图；多视角请在「图片变 3D」补图使用' })
              : !engineAvailable(engine) ? node('a', { class: 'inline-link', href: '#setup', text: '安装这个模型 →', dataset: { setupModel: engine.id } }) : null,
            state.pipelineEngines.has(engine.id) && !available ? actionButton('移除此选择', 'pipeline-remove-model', { engineId: engine.id }, 'button small ghost') : null])
        ]);
      });
      const suggested = engines.filter(engine => engineAvailable(engine) && !engine.multiView && coloredEngine(engine)).sort((first, second) => colorRank(first) - colorRank(second)).slice(0, 2);
      const prominent = engines.map((engine, index) => ({ engine, card: engineCards[index] })).filter(item => state.pipelineEngines.has(item.engine.id) || suggested.includes(item.engine));
      const other = engineCards.filter(card => !prominent.some(item => item.card === card));
      const children = prominent.map(item => item.card);
      for (const id of state.pipelineEngines) {
        if (!engines.some(engine => engine.id === id)) children.push(node('div', { class: 'pipeline-engine-option unavailable' }, [node('span', {}, [
          node('strong', { text: `已选模型 ${id} 暂不可用` }), actionButton('移除此选择', 'pipeline-remove-model', { engineId: id }, 'button small ghost')])]));
      }
      if (other.length) children.push(node('details', { id: 'pipeline-more', class: 'pipeline-more', open: state.pipelineMoreOpen }, [
        node('summary', { text: `更多建模模型 · ${other.length} 个选择` }), node('div', { class: 'pipeline-engine-grid' }, other)]));
      $('#pipeline-engines').replaceChildren(...(children.length ? children : [node('a', { class: 'inline-link', href: '#setup', text: '先检测并安装建模模型 →' })]));
      if (focusedControl) $(`#${focusedControl}`).focus({ preventScroll: true });
      else if (focusedModel) [...$('#pipeline-engines').querySelectorAll('[data-pipeline-engine-id]')].find(input => input.dataset.pipelineEngineId === focusedModel)?.focus({ preventScroll: true });
      saveDraft();
    }
    updatePipelineSummary();
  }

  function updatePipelineSummary() {
    const { imageEngine, variant, engines } = pipelineSelections();
    const prompt = $('#pipeline-prompt').value.trim();
    const invalidModel = engines.some(engine => !engineAvailable(engine) || engine.multiView) || engines.length !== state.pipelineEngines.size;
    const ready = state.connected && imageEngine && engineAvailable(imageEngine) && imageVariantReady(imageEngine,variant) && engines.length && !invalidModel;
    const button = $('#start-pipeline');
    button.disabled = !ready || !prompt || state.busy.has('start-pipeline');
    button.textContent = state.busy.has('start-pipeline') ? '正在创建任务…' : '一键生成图片和 3D';
    const messages = [];
    if (!state.connected) messages.push(node('p', { text: '正在连接本机创作引擎…' }));
    else if (!imageEngine || !engineAvailable(imageEngine)) messages.push(node('p', {}, ['先准备生图模型。', node('a', { class: 'inline-link', href: '#setup', text: '检测与安装 →', dataset: imageEngine ? { setupModel: imageEngine.id } : {} })]));
    else if (!imageVariantReady(imageEngine,variant)) messages.push(node('p', {}, [`已选版本 ${state.pipelineWeightVariant || ''} 还未完成安装与校验。`, node('a', { class: 'inline-link', href: '#setup', text: '安装此版本 →', dataset: { setupEngine: imageEngine.id, setupVariant: state.pipelineWeightVariant } })]));
    if (state.connected && (!engines.length || invalidModel)) messages.push(node('p', {}, [invalidModel ? '已选建模模型暂不可用，请补齐安装或移除该选择。' : '请选一个可用的单图建模模型。', node('a', { class: 'inline-link', href: '#setup', text: '准备建模模型 →' })]));
    $('#pipeline-readiness').hidden = !messages.length;
    $('#pipeline-readiness').replaceChildren(...messages);
    $('#pipeline-summary').textContent = !ready ? '准备好生图与建模模型，即可一键创作' : !prompt ? '写下想法，就能开始' : `1 张图片 → ${engines.length} 个 3D 模型`;
  }

  async function startPipeline() {
    if ($('#start-pipeline').disabled) return;
    const request = { prompt: $('#pipeline-prompt').value.trim(), imageEngineId: state.pipelineImageEngine, weightVariant: state.pipelineWeightVariant,
      modelEngineIds: [...state.pipelineEngines] };
    const imageEngine=(state.api?.imageEngines || []).find(engine=>engine.id===state.pipelineImageEngine);
    Object.assign(request,{width:imageEngine?.defaults?.width || 512,height:imageEngine?.defaults?.height || 512,steps:imageEngine?.defaults?.steps || 20});
    if (!imageEngine?.weightVariants?.length) delete request.weightVariant;
    const signature = JSON.stringify(request);
    if (!state.pipelineRequest || state.pipelineRequest.signature !== signature) state.pipelineRequest = { id: window.crypto.randomUUID(), signature };
    const clientRequestId = state.pipelineRequest.id;
    saveDraft();
    state.busy.add('start-pipeline'); updatePipelineSummary();
    try {
      await api('/api/pipelines', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...request, clientRequestId }), timeout: 20000 });
      state.pipelineRequest = null;
      saveDraft(); await loadState(); renderPipelines(); notify('已开始一键创作，图片完成后会自动生成 3D。');
    } catch (error) {
      await loadState();
      if ((state.api?.pipelines || []).some(pipeline => pipeline.clientRequestId === clientRequestId)) {
        state.pipelineRequest = null; saveDraft(); notify('已在本机确认创作任务，进度显示在下方。');
      } else notify(`${error.message} 请先查看下方记录或队列；相同内容重新提交会核对原任务，避免重复创建。`, true);
    } finally { state.busy.delete('start-pipeline'); updatePipelineSummary(); }
  }

  function pipelineStage(label, job, fallback, finished = false) {
    const stage = node('div', { class: `pipeline-stage${finished ? ' complete' : ''}` }, [node('span', { class: 'pipeline-stage-number', text: label }),
      node('div', {}, [node('strong', { text: finished ? fallback : job?.phase || job?.stage || fallback }),
        job ? node('small', { text: STATUS_LABELS[job.status] || job.status || '' }) : null])]);
    const progress = Number(job?.progress);
    if (job?.status === 'running' && job.progress !== null && job.progress !== undefined && Number.isFinite(progress)) stage.append(node('span', { class: 'pipeline-stage-progress', text: `${Math.max(0, Math.min(100, Math.round(progress)))}%` }));
    return stage;
  }

  function renderPipelines() {
    const pipelines = Array.isArray(state.api?.pipelines) ? state.api.pipelines : [];
    const signature = JSON.stringify([pipelines, state.api?.imageJobs, state.api?.batches, [...state.busy]]);
    if (signature === state.pipelineJobsSignature) return; state.pipelineJobsSignature = signature;
    const newestFirst = pipelines.slice().sort((first, second) => (Date.parse(second.createdAt) || 0) - (Date.parse(first.createdAt) || 0));
    $('#pipeline-jobs').replaceChildren(...newestFirst.slice(0, 10).map(pipeline => {
      const imageJob = imageJobs().find(job => job.id === pipeline.imageJobId);
      const batch = (state.api?.batches || []).find(item => item.id === pipeline.modelBatchId);
      const modelJobs = batch?.jobs || [];
      const activeModel = modelJobs.find(job => job.status === 'running') || modelJobs.find(job => job.status === 'queued') || modelJobs[0];
      const imageDone = imageJob?.status === 'generated-unreviewed' || Boolean(pipeline.modelBatchId);
      const completed = pipeline.status === 'completed' || pipeline.status === 'generated-unreviewed';
      const failed = ['failed', 'interrupted', 'cancelled'].includes(pipeline.status);
      const card = node('article', { class: 'panel pipeline-job', dataset: { pipelineId: pipeline.id } }, [node('div', { class: 'pipeline-job-heading' }, [
        node('div', {}, [node('h2', { text: completed ? '创作完成' : failed ? STATUS_LABELS[pipeline.status] || pipeline.status : '一键创作进行中' }), node('p', { text: pipeline.prompt || '' })]),
        node('div', { class: 'pipeline-job-actions' }, [node('a', { class: 'button small', href: completed ? '#results' : '#queue', text: completed ? '查看与下载模型 →' : '查看队列与日志 →' }),
          ['queued', 'running'].includes(pipeline.status) ? actionButton('取消创作', 'cancel-pipeline', { pipelineId: pipeline.id }, 'button small ghost') : null,
          RETRYABLE.has(pipeline.status) ? node('button', { class: 'button small', type: 'button', text: '重新创作', title: '重新执行生图与建模，已有作品保留', disabled: state.busy.has(pipeline.id), dataset: { action: 'retry-pipeline', pipelineId: pipeline.id } }) : null])]),
        node('div', { class: 'pipeline-stages' }, [pipelineStage('1 · 图片', imageJob, imageDone ? '图片已完成' : failed && !imageDone ? '图片生成已停止' : imageJob?.status === 'running' ? '正在生成图片' : '等待生成图片', imageDone),
          pipelineStage('2 · 3D', activeModel, completed ? '模型已完成' : activeModel ? '正在生成模型' : failed ? '建模已停止' : '等待图片完成', completed)])]);
      const imageAsset = validAssets(imageJob?.assets).find(isImage);
      if (imageAsset) card.append(node('img', { class: 'pipeline-image-result', src: safeUrl(imageAsset.url), alt: '一键创作生成的主图', loading: 'lazy' }));
      const error = pipeline.error || (failed ? imageJob?.error || activeModel?.error : '');
      if (error) card.append(errorExplanation(error, `pipeline:${pipeline.id}`));
      return card;
    }));
  }

  function updateSummary() {
    const objects = state.objects.filter(object => object.main);
    const engines = (state.api?.engines || []).filter(engine => engineAvailable(engine) && state.selectedEngines.has(engine.id));
    const unavailableCount = state.selectedEngines.size - engines.length;
    const auxiliaryCount = state.objects.reduce((count, object) => count + Object.values(object.auxiliary).filter(Boolean).length, 0);
    const missingCount = state.objects.length - objects.length;
    const tasks = objects.length * engines.length;
    const uploadingCount = state.uploading.size;
    $('#production-summary').textContent = uploadingCount ? `${uploadingCount} 张图片正在导入…` : tasks ? `${objects.length} 个物体 × ${engines.length} 个工具 = ${tasks} 个生产任务` : '准备好图片与工具，就可以开始';
    let detail = auxiliaryCount ? `${auxiliaryCount} 张辅助图已准备 · GPU 将依次生产` : '辅助图可选，单张主图也能生产';
    if (missingCount) detail = `${missingCount} 个物体还没有主图，请先导入或移除空物体`;
    else if (unavailableCount) detail = `${unavailableCount} 个已选模型暂不可用，请检查安装状态或移除该选择`;
    else if (!engines.length) detail = '请至少选择一个已就绪的工具';
    else if (!state.connected) detail = '正在重新连接本机创作引擎，请稍候';
    $('#production-detail').textContent = detail;
    const start = $('#start-production');
    start.disabled = !state.connected || !tasks || unavailableCount > 0 || missingCount > 0 || uploadingCount > 0 || state.busy.has('start');
    start.replaceChildren(node('span', { text: state.busy.has('start') ? '…' : '▷', 'aria-hidden': 'true' }), state.busy.has('start') ? '提交中…' : '开始生产');
  }

  let pickerTarget = null;
  function pickImages(target, multiple = false) {
    pickerTarget = target;
    const input = $('#image-picker');
    input.value = '';
    input.multiple = multiple;
    input.click();
  }

  async function uploadImage(file) {
    if (!file || !/\.(png|jpe?g|webp)$/i.test(file.name)) throw new Error('请选择 PNG、JPEG 或 WebP 图片。');
    const result = await api('/api/images', { method: 'POST', body: file,
      headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name) }, timeout: 90000 });
    const image = imageRecord(result);
    if (!image) throw new Error('工厂未返回可用图片，请重新导入。');
    state.samplesChecked = false; loadSamples();
    return rememberReference(image);
  }

  function updateImportControls() {
    const importing = state.busy.has('import-model');
    for (const button of document.querySelectorAll('[data-action="import-model"]')) {
      button.disabled = importing || !state.connected;
      button.title = importing ? '模型正在导入，请稍候' : !state.connected ? '正在连接本机创作引擎，请稍候' : '导入本机 GLB 模型，最大 512 MB';
      button.setAttribute('aria-busy', String(importing)); button.classList.toggle('importing-model', importing);
      if (!button.querySelector('strong')) { if (!button.dataset.importLabel) button.dataset.importLabel = button.textContent; button.textContent = importing ? '正在导入模型…' : button.dataset.importLabel; }
    }
  }

  async function importModel(file) {
    if (!file || state.busy.has('import-model')) return;
    if (!/\.glb$/i.test(file.name)) { notify('请选择 GLB 格式的 3D 模型。', true); return; }
    if (!file.size || file.size > MAX_MODEL_BYTES) { notify('请选择非空且不超过 512 MB 的 GLB 模型。', true); return; }
    state.busy.add('import-model'); updateImportControls(); announce('正在导入本机模型，请稍候。');
    try {
      await api('/api/models', { method: 'POST', body: file, headers: { 'Content-Type': 'model/gltf-binary', 'X-File-Name': encodeURIComponent(file.name) }, timeout: 120000 });
      await loadState(); window.location.hash = 'results'; renderResults();
      notify('模型已导入。在成品中预览，或继续上色、绑骨和做动作。');
      announce('本机模型导入完成。');
    } catch (error) { notify(`${error.message || '模型导入失败。'} 若连接中断，请先查看成品与历史，避免重复导入。`, true); }
    finally { state.busy.delete('import-model'); updateImportControls(); }
  }

  async function importToSlot(file, objectId, view) {
    const key = `${objectId}:${view}`;
    if (state.uploading.has(key)) return;
    state.uploading.set(key, true);
    renderObjects();
    try {
      const image = await uploadImage(file);
      const object = state.objects.find(item => item.id === objectId);
      if (object) {
        if (view === 'main') {
          object.main = image;
          if (/^物体 \d+$/.test(object.name)) object.name = image.name.replace(/\.[^.]+$/, '').slice(0, 80);
        } else if (Object.hasOwn(VIEW_LABELS, view)) object.auxiliary[view] = image;
        saveDraft();
        announce(`${object.name}的${view === 'main' ? '主图' : VIEW_LABELS[view]}已导入。`);
      }
    } catch (error) { notify(`${file.name}：${error.message}`, true); }
    finally { state.uploading.delete(key); renderObjects(); }
  }

  async function importMainFiles(files) {
    const images = [...files];
    if (!images.length) return;
    for (const [index, file] of images.entries()) {
      let object = state.objects.find(item => !item.main && !state.uploading.has(`${item.id}:main`));
      if (!object) {
        if (state.objects.length >= MAX_OBJECTS) {
          notify(`每批最多 ${MAX_OBJECTS} 个物体，还有 ${images.length - index} 张主图未导入。可以先生产本批，再另建一批。`);
          break;
        }
        object = newObject(); state.objects.push(object);
      }
      await importToSlot(file, object.id, 'main');
    }
    saveDraft();
  }

  async function loadSamples() {
    if (state.samplesChecked || state.samplesLoading) return;
    state.samplesLoading = true;
    try {
      const result = await api('/api/samples');
      $('[data-action="sample"]').hidden = !Array.isArray(result.images) || !result.images.some(imageRecord);
      state.samplesChecked = true;
    } catch { /* Keep the optional entry hidden until local samples can be confirmed. */ }
    finally { state.samplesLoading = false; }
  }

  async function useSample() {
    if (state.busy.has('sample')) return;
    state.busy.add('sample');
    try {
      const result = await api('/api/samples');
      const image = imageRecord(result.images?.[0]);
      if (!image) throw new Error('本机暂未提供示例图片。');
      let object = state.objects.find(item => !item.main);
      if (!object) {
        if (state.objects.length >= MAX_OBJECTS) throw new Error(`每批最多 ${MAX_OBJECTS} 个物体，请先移除一个物体或生产当前批次。`);
        object = newObject(); state.objects.push(object);
      }
      object.main = image;
      object.name = image.name.replace(/\.[^.]+$/, '').slice(0, 80);
      saveDraft(); renderObjects(); notify('示例已导入，选择工具即可开始生产。');
    } catch (error) { notify(error.message, true); }
    finally { state.busy.delete('sample'); }
  }

  async function startProduction() {
    if ($('#start-production').disabled) return;
    const objects = state.objects.map(object => ({ name: object.name.trim() || '未命名物体', mainImageId: object.main.id,
      auxiliaryImages: Object.entries(object.auxiliary).filter(([, image]) => image).map(([view, image]) => ({ imageId: image.id, view })) }));
    const engineIds = (state.api?.engines || []).filter(engine => engineAvailable(engine) && state.selectedEngines.has(engine.id)).map(engine => engine.id);
    state.busy.add('start'); updateSummary();
    try {
      await api('/api/batches', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: $('#batch-name').value.trim(), objects, engineIds }), timeout: 20000 });
      window.location.hash = 'queue';
      await loadState();
      notify('生产任务已加入队列，完成的产物会自动显示。');
      announce('生产批次已创建。');
    } catch (error) {
      notify(`${error.message} 如提交时连接中断，请先查看生产队列，避免重复提交。`, true);
      await loadState();
    } finally { state.busy.delete('start'); updateSummary(); }
  }

  function statusBadge(status) { return node('span', { class: `status-badge ${String(status || '').replace(/[^a-z-]/g, '')}`, text: STATUS_LABELS[status] || status || '未知状态' }); }

  function progressBar(value, indeterminate = false) {
    const progress = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
    const track = node('div', { class: `progress-track${indeterminate && progress === null ? ' indeterminate' : ''}`,
      role: 'progressbar', 'aria-label': '任务进度', 'aria-valuemin': '0', 'aria-valuemax': '100',
      'aria-valuenow': progress, 'aria-valuetext': progress === null ? '正在运行，当前阶段无百分比' : `${Math.round(progress)}%` }, [node('div', { class: 'progress-fill' })]);
    if (progress !== null) track.firstElementChild.style.width = `${progress}%`;
    return track;
  }

  function jobLogText(job) {
    const value = Array.isArray(job.logTail) ? job.logTail.join('\n') : String(job.logTail || '');
    return value || (job.status === 'queued' ? '等待轮到本任务，尚未产生运行日志。' : '当前尚未收到日志。');
  }

  function jobStage(job) {
    return job.phase || ({ queued: '等待前面的任务完成，轮到后自动开始', running: '正在处理，进度由本机引擎更新',
      'generated-unreviewed': '已生成，请预览检查效果', failed: '生成失败，请查看原因后重试', interrupted: '任务已中断，可以重新加入队列', cancelled: '已取消，已有作品保留' }[job.status] || STATUS_LABELS[job.status] || '状态待更新');
  }
  function jobProgress(job) {
    const value = typeof job.progress === 'number' && Number.isFinite(job.progress) ? Math.max(0, Math.min(100, job.progress)) : null;
    return node('div', { class: 'job-progress' }, [node('div', { class: 'progress-label' }, [
      node('span', { text: '当前阶段进度' }), node('span', { text: value === null ? '运行中 · 暂无百分比' : `${Math.round(value)}%` })
    ]), progressBar(value, true)]);
  }

  function renderJob(job) {
    const glyph = job.status === 'generated-unreviewed' ? '✓' : ['failed', 'interrupted'].includes(job.status) ? '!' : job.status === 'running' ? '↻' : job.status === 'cancelled' ? '−' : '◇';
    const actions = node('div', { class: 'job-actions' });
    const assets = validAssets(job.assets);
    if (assets.length) actions.append(actionButton('查看产物', 'preview-job', { jobId: job.id }));
    if (['queued', 'running'].includes(job.status)) actions.append(actionButton('取消', 'cancel-job', { jobId: job.id, imageJob: isImageJob(job) ? '1' : '0' }, 'button small ghost'));
    if (RETRYABLE.has(job.status) && !retiredJob(job)) actions.append(actionButton('重试', 'retry-job', { jobId: job.id, imageJob: isImageJob(job) ? '1' : '0' }));
    if (TERMINAL.has(job.status)) actions.append(actionButton('删除', 'delete-result', { deleteId: job.id, deleteKind: isImageJob(job) ? 'image' : 'job', deleteName: job.objectName || '本次生成', deleteEngine: engineTitle(job.engine) }, 'button small ghost danger'));
    const details = [node('span', { text: jobStage(job) })];
    const variant = jobWeightVariant(job);
    if (variant) details.unshift(node('span', { class: 'job-variant', text: `量化版本 ${variant}` }));
    if (Number(job.elapsedSeconds) > 0) details.push(node('span', { text: `已用时 ${duration(job.elapsedSeconds)}` }));
    const row = node('article', { class: 'job-row', dataset: { jobId: job.id } }, [node('div', { class: 'job-main' }, [
      node('span', { class: `job-glyph ${job.status}`, text: glyph, 'aria-hidden': 'true' }),
      node('div', { class: 'job-copy' }, [node('div', { class: 'job-title' }, [node('strong', { text: job.objectName || '物体' }), statusBadge(job.status)]),
        node('div', { class: 'job-detail' }, [node('span', { class: 'job-engine', text: engineTitle(job.engine) }), ...details])]), actions
    ])]);
    if (job.status === 'running') row.append(jobProgress(job));
    if (job.error) row.append(errorExplanation(job.error, job.id, retiredJob(job)));
    const logs = node('details', { class: 'job-logs', open: state.openLogs.has(job.id), dataset: { logJob: job.id } }, [node('summary', { text: '查看运行日志' }), node('pre', { text: jobLogText(job) })]);
    logs.addEventListener('toggle', () => { if (logs.open) state.openLogs.add(job.id); else state.openLogs.delete(job.id); });
    row.append(logs);
    if (assets.length) row.append(node('div', { class: 'job-assets' }, assets.map((asset, index) => node('button', {
      type: 'button', class: 'file-chip', dataset: { action: 'preview-job', jobId: job.id, assetIndex: index }
    }, [node('span', { text: asset.name }), node('small', { text: bytes(asset.bytes) })]))));
    assets.forEach((asset, index) => { const actions = outputImageActions(asset, 'job', job.id, index); if (actions) row.append(actions); });
    return row;
  }

  function renderQueue() {
    if (!state.api) {
      $('#batches-list').replaceChildren(emptyState('正在连接本机创作引擎', '连接成功后，正在生产的任务会显示在这里。'));
      return;
    }
    const mediaIds = new Set(mediaJobs().map(job => job.id));
    const batches = [...(state.api.batches || [])].reverse().filter(batch => !(batch.jobs || []).some(job => mediaIds.has(job.id))).filter(batch => {
      const jobs = Array.isArray(batch.jobs) ? batch.jobs : [];
      if (state.queueFilter === 'active') return jobs.some(job => ['queued', 'running'].includes(job.status));
      if (state.queueFilter === 'finished') return jobs.length && jobs.every(job => TERMINAL.has(job.status));
      return true;
    });
    const active = document.activeElement;
    const focusKey = active?.dataset?.jobId ? [active.dataset.action, active.dataset.jobId] : null;
    const scrollPositions = new Map([...$('#batches-list').querySelectorAll('[data-log-job]')].map(details => [details.dataset.logJob, details.querySelector('pre')?.scrollTop || 0]));
    const cards = batches.map(batch => {
      const jobs = Array.isArray(batch.jobs) ? batch.jobs : [];
      const finished = jobs.filter(job => TERMINAL.has(job.status)).length;
      const succeeded = jobs.filter(job => job.status === 'generated-unreviewed').length;
      const failed = jobs.filter(job => ['failed', 'interrupted'].includes(job.status)).length;
      const activeBatch = jobs.some(job => ['queued', 'running'].includes(job.status));
      const actions = node('div', { class: 'batch-actions' });
      if (succeeded) actions.append(node('a', { class: 'button small', href: `/api/batches/${encodeURIComponent(batch.id)}/download`, download: '', text: '整批下载' }));
      if (activeBatch) actions.append(actionButton('取消本批', 'cancel-batch', { batchId: batch.id }, 'button small ghost'));
      const card = node('section', { class: 'batch-card' }, [
        node('div', { class: 'batch-heading' }, [node('div', {}, [node('div', { class: 'batch-title' }, [node('h2', { text: batch.name || '未命名批次' }), statusBadge(activeBatch ? (jobs.some(job => job.status === 'running') ? 'running' : 'queued') : failed ? 'failed' : succeeded ? 'generated-unreviewed' : 'cancelled')]),
          node('p', { class: 'batch-meta', text: `${timeLabel(batch.createdAt)} · ${(batch.objects || []).length} 个物体 · ${jobs.length} 个任务` })]), actions]),
        node('div', { class: 'batch-progress-summary' }, [node('div', { class: 'progress-label' }, [node('strong', { text: `已结束 ${finished} / ${jobs.length} 个任务` }), node('span', { text: `${succeeded} 个已生成${failed ? ` · ${failed} 个失败` : ''}` })]), progressBar(jobs.length ? finished / jobs.length * 100 : 0)]),
        ...jobs.map(renderJob)
      ]);
      return card;
    });
    const images = [...imageJobs()].reverse().filter(job => state.queueFilter === 'active' ? ['queued', 'running'].includes(job.status)
      : state.queueFilter === 'finished' ? TERMINAL.has(job.status) : true);
    if (images.length) cards.unshift(node('section', { class: 'batch-card' }, [node('div', { class: 'batch-heading' }, [
      node('div', {}, [node('h2', { text: '本地生图' }), node('p', { class: 'batch-meta', text: `${images.length} 个任务 · 与 3D 生产依次排队` })]),
      node('a', { class: 'button small', href: '#imagegen', text: '查看生成图片' })
    ]), ...images.map(renderJob)]));
    const media = mediaJobs().filter(job => state.queueFilter === 'active' ? ['queued', 'running'].includes(job.status) : state.queueFilter === 'finished' ? TERMINAL.has(job.status) : true);
    if (media.length) cards.unshift(node('section', { class: 'batch-card' }, [node('div', { class: 'batch-heading' }, [node('div', {}, [node('h2', { text: '图片编辑与视角 · 含历史任务' }), node('p', { class: 'batch-meta', text: `${media.length} 个任务 · 按顺序使用本机显卡` })])]), ...media.slice().reverse().map(renderJob)]));
    $('#batches-list').replaceChildren(...(cards.length ? cards : [emptyState('这里还没有生产任务', state.queueFilter === 'all' ? '导入图片并选择工具，第一批任务就会出现在这里。' : '当前筛选下没有任务。', '≡', { href: '#workspace', label: '开始生产' })]));
    for (const details of $('#batches-list').querySelectorAll('[data-log-job]')) {
      const scroll = scrollPositions.get(details.dataset.logJob);
      if (scroll !== undefined) details.querySelector('pre').scrollTop = scroll;
    }
    if (focusKey) [...$('#batches-list').querySelectorAll('[data-action]')].find(button => button.dataset.action === focusKey[0] && button.dataset.jobId === focusKey[1])?.focus({ preventScroll: true });
  }

  function modelIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'm12 2 9 5v10l-9 5-9-5V7Zm0 0v10m9-5-9 5-9-5m9 5v10');
    svg.append(path); return svg;
  }

  function assetCard(asset, source, id, index, inputUrl = '', resultJobIndex = 0) {
    const previewData = source === 'task' ? { action: 'preview-result', taskId: id, resultJobIndex, assetIndex: index }
      : { action: source === 'job' ? 'preview-job' : 'preview-history', [source === 'job' ? 'jobId' : 'historyId']: id, assetIndex: index };
    const model = isModel(asset);
    const imageUrl = isImage(asset) ? safeUrl(asset.url) : safeUrl(asset.previewUrl);
    const hasImage = imageUrl && /\.(png|jpe?g|webp)(?:[?#]|$)/i.test(imageUrl);
    const cover = node('button', { type: 'button', class: `asset-cover${model && !hasImage ? ' model-file-stage' : ''}`, dataset: previewData, 'aria-label': `预览 ${asset.name}` });
    if (hasImage) cover.append(node('img', { src: imageUrl, alt: asset.name, loading: 'lazy' }));
    else if (model) cover.append(node('span', { class: 'model-file-illustration', 'aria-hidden': 'true' }, [
      node('span', { class: 'model-file-orbit' }), node('span', { class: 'asset-model-icon' }, [modelIcon()]),
      node('span', { class: 'model-file-caption', text: '3D 模型' })]));
    else cover.append(node('span', { class: 'empty-symbol', text: isImage(asset) ? '▧' : '≡', 'aria-hidden': 'true' }));
    cover.append(node('span', { class: 'asset-type', text: extension(asset).toUpperCase() }), node('span', { class: 'asset-preview-label', text: model ? '点击预览真实模型 ↗' : isVideo(asset) ? '播放视频 ↗' : isImage(asset) ? '查看原图 ↗' : '查看文件 ↗' }));
    const info = node('div', { class: 'asset-card-info' }, [
      node('h3', { text: asset.name, title: asset.name }), node('div', { class: 'asset-card-meta' }, [node('span', { text: model ? '三维模型' : isImage(asset) ? '图片 / 贴图' : isVideo(asset) ? '视频' : '生产记录' }), node('span', { text: bytes(asset.bytes) })]),
      node('div', { class: 'asset-card-actions asset-primary-actions' }, [node('button', { type: 'button', class: 'button small', text: '预览', dataset: previewData }), node('a', { class: 'button small ghost', href: safeUrl(asset.url), download: asset.name, text: '下载 ↓' })])
    ]);
    const nextActions = node('div', { class: 'asset-next-actions' });
    if (source === 'job' && isImage(asset) && imageJobs().some(job => job.id === id)) {
      nextActions.append(node('button', { type: 'button', class: 'button small image-use-button', text: '用作主图 / 辅助图 →', dataset: { action: 'use-generated-image', jobId: id, assetIndex: index } }));
    }
    if (source === 'task' && isImage(asset) && resultTasks().some(task => task.id === id && task.kind === 'image')) {
      nextActions.append(node('button', { type: 'button', class: 'button small image-use-button', text: '用作主图 / 辅助图 →', dataset: { action: 'use-result-image', taskId: id, resultJobIndex, assetIndex: index } }));
    }
    if (model && extension(asset) === 'glb') {
      if (!asset.modelInfo?.skins && !asset.modelInfo?.skinnedVertices && !asset.modelInfo?.animationCount) {
        nextActions.append(node('button', { type: 'button', class: 'button small image-use-button', text: '继续上色 →',
          dataset: { action: 'use-texture-model', modelUrl: safeUrl(asset.url) } }));
      }
    }
    if (nextActions.childElementCount) info.append(nextActions);
    const continuation = outputImageActions(asset, source, id, index, resultJobIndex);
    if (continuation) info.append(continuation);
    return node('article', { class: 'asset-card' }, [cover, info]);
  }

  function renderImageJobs() {
    if (!state.api) {
      $('#image-jobs-list').replaceChildren(emptyState('正在连接本机创作引擎', '模型状态与生成记录会在连接成功后显示。', '▧'));
      return;
    }
    const cards = [...imageJobs()].reverse().map(job => {
      const assets = validAssets(job.assets);
      const pictures = assets.map((asset, index) => ({ asset, index })).filter(item => isImage(item.asset));
      const card = node('section', { class: 'batch-card' }, [renderJob(job)]);
      if (pictures.length) card.append(node('div', { class: 'image-job-assets' }, pictures.map(({ asset, index }) => assetCard(asset, 'job', job.id, index))));
      return card;
    });
    $('#image-jobs-list').replaceChildren(...(cards.length ? cards : [emptyState('你的第一张图片，从这里开始', '输入描述并选择已就绪的工具，生成的真实图片会出现在这里。', '▧')]));
  }

  function resultTasks() { return Array.isArray(state.api?.resultTasks) ? state.api.resultTasks : []; }

  function taskJobs(task) { return Array.isArray(task.jobs) ? task.jobs : []; }

  function matchesResultTask(task) {
    const query = $('#result-search').value.trim().toLocaleLowerCase();
    const engineFilter = $('#result-engine-filter').value;
    const jobs = taskJobs(task);
    const names = [task.name, task.batchName, task.prompt, task.weightVariant, ...(Array.isArray(task.inputs) ? task.inputs : []).map(image => image.name),
      ...jobs.flatMap(job => [engineTitle(job.engineId || job.engine), jobWeightVariant(job), ...validAssets(job.assets).map(asset => asset.name)])];
    return (!engineFilter || jobs.some(job => (job.engineId || job.engine) === engineFilter))
      && (!query || names.filter(Boolean).join(' ').toLocaleLowerCase().includes(query));
  }

  function resultDetails(key, label, children, className = '') {
    return node('details', { class: `result-details ${className}`.trim(), open: state.openResultDetails.has(key), dataset: { resultDetails: key } },
      [node('summary', { text: label }), ...children]);
  }

  function renderTaskInputs(task) {
    if (task.kind === 'imported-model' || taskJobs(task).some(job => (job.engineId || job.engine) === 'imported-model')) return node('div', { class: 'task-input-panel' }, [
      node('div', { class: 'task-panel-label', text: '本机导入' }), node('p', { class: 'field-hint', text: '这是你导入的 GLB 模型。预览后可继续上色、绑骨或制作动作；模型已有骨骼时，可直接检查动作。' })]);
    if (task.inputMode === 'model' && task.sourceModel && safeUrl(task.sourceModel.url)) {
      const source = task.sourceModel;
      const panel = node('aside', { class: 'task-input-panel' }, [node('div', { class: 'task-panel-label', text: '输入模型' }),
        node('p', { text: { rig: '这次任务的骨骼绑定记录。', texture: '用参考视图为这个模型生成贴图。', animation: '这次任务的动作制作记录。' }[task.pipelineStage] || '这个模型的处理记录。' }),
        actionButton('预览输入模型', 'preview-source-model', { sourceUrl: source.url, sourceBytes: source.bytes, sourceName: source.path?.split('/').pop() || 'input.glb' }),
        node('a', { class: 'button small ghost', href: safeUrl(source.url), download: true, text: '下载输入模型 ↓' })]);
      if (task.pipelineStage === 'texture') {
        const references = (task.inputs || []).filter(image => safeUrl(image.url));
        panel.append(node('p', {class:'task-input-hint',text:`${references.length} 张上色参考图`}),
          node('div', {class:'task-auxiliary-sources'}, references.map(image => node('a', {class:'task-source-image',href:safeUrl(image.url),target:'_blank',rel:'noopener'}, [
            node('img', {src:safeUrl(image.url),alt:`${VIEW_LABELS[image.view] || '正面'}上色参考图`,loading:'lazy'}), node('span', {text:VIEW_LABELS[image.view] || '正面'})]))));
      }
      return panel;
    }
    if (task.kind === 'image') {
      return resultDetails(`${task.id}:prompt`, '查看创作描述', [
        node('p', { class: 'task-prompt', text: task.prompt || taskJobs(task)[0]?.request?.prompt || task.name || '未记录描述' })], 'task-input-panel task-prompt-panel');
    }
    const inputs = (Array.isArray(task.inputs) ? task.inputs : []).filter(image => safeUrl(image.url));
    const main = inputs.find(image => ['front', 'main'].includes(image.view)) || inputs[0];
    const auxiliaries = inputs.filter(image => image !== main);
    const sourceCard = (image, primary = false) => node('a', { class: `task-source-image${primary ? ' main-source' : ''}`, href: safeUrl(image.url), target: '_blank', rel: 'noopener', 'aria-label': `查看原图 ${image.name || ''}` }, [
      node('img', { src: safeUrl(image.url), alt: `${primary ? '主图' : `${VIEW_LABELS[image.view] || '辅助'}图`}：${image.name || task.name}`, loading: 'lazy' }),
      node('span', { text: primary ? '主图' : `${VIEW_LABELS[image.view] || '辅助'}图` })]);
    return node('aside', { class: 'task-input-panel task-source-strip' }, [
      main ? sourceCard(main, true) : node('div', { class: 'task-input-empty', text: '此历史任务未保存可查看的原图' }),
      node('div', { class: 'task-source-copy' }, [node('div', { class: 'task-panel-label', text: '来源原图' }),
        node('p', { class: 'task-input-hint', text: inputs.length ? `${inputs.length} 张输入图片 · 点击查看原图` : '历史产物仍可预览与下载' })]),
      auxiliaries.length ? resultDetails(`${task.id}:source-images`, `辅助图 ${auxiliaries.length}`, [node('div', { class: 'task-aux-images' }, auxiliaries.map(image => sourceCard(image)))], 'task-source-more') : null]);
  }

  function renderOtherFiles(task, job, jobIndex, files) {
    if (!files.length) return null;
    const rows = files.map(({ asset, index }) => node('div', { class: 'other-file-row' }, [node('div', { class: 'other-file-name' }, [
      node('span', { text: extension(asset).toUpperCase(), class: 'other-file-type' }), node('span', { text: asset.name, title: asset.name })]),
      node('span', { class: 'other-file-size', text: bytes(asset.bytes) }),
      node('button', { type: 'button', class: 'button small ghost', text: '查看', dataset: { action: 'preview-result', taskId: task.id, resultJobIndex: jobIndex, assetIndex: index } }),
      node('a', { class: 'button small ghost', href: safeUrl(asset.url), download: asset.name, text: '下载 ↓' })]));
    return resultDetails(`${task.id}:${job.id || job.runId}:files`, `其他文件（${files.length}）`, rows, 'other-files');
  }

  function renderTaskJob(task, job, jobIndex, previous = false) {
    const assets = validAssets(job.assets);
    const indexed = assets.map((asset, index) => ({ asset, index }));
    let mainFiles;
    if (task.kind === 'image') mainFiles = indexed.filter(item => isImage(item.asset));
    else if (task.kind === 'video') mainFiles = indexed.filter(item => isVideo(item.asset));
    else {
      const models = indexed.filter(item => isModel(item.asset));
      const glbs = models.filter(item => ['glb', 'gltf'].includes(extension(item.asset)));
      mainFiles = glbs.length ? glbs : models;
      const primary = mainFiles.findIndex(item => /(?:^|\/)(?:scene|combined|assembly|model|mesh)\.glb$/i.test(item.asset.name));
      if (primary > 0) mainFiles = [mainFiles[primary], ...mainFiles.filter((_, index) => index !== primary)];
    }
    const primarySet = new Set(mainFiles.map(item => item.index));
    const otherFiles = indexed.filter(item => !primarySet.has(item.index));
    const actions = node('div', { class: 'task-tool-actions' });
    if (task.source === 'queue' && allJobs().some(item => item.id === job.id)) {
      if (['queued', 'running'].includes(job.status)) actions.append(actionButton('取消', 'cancel-job', { jobId: job.id, imageJob: task.kind === 'image' ? '1' : '0' }, 'button small ghost'));
      if (RETRYABLE.has(job.status) && !retiredJob(job, task)) actions.append(actionButton('重试', 'retry-job', { jobId: job.id, imageJob: task.kind === 'image' ? '1' : '0' }));
    }
    if (TERMINAL.has(job.status)) actions.append(actionButton('删除', 'delete-result', { deleteId: task.source === 'history' ? job.historyId || job.id : job.id,
      deleteKind: task.source === 'history' ? 'history' : task.kind === 'image' ? 'image' : 'job', deleteName: task.name || '本次生成', deleteEngine: engineTitle(job.engineId || job.engine) }, 'button small ghost danger'));
    const variant = task.kind === 'image' ? jobWeightVariant(job, task.weightVariant) : '';
    const appearance = task.kind !== 'image' ? engineAppearance(job) : '';
    const panel = node('section', { class: `task-tool-panel${previous ? ' previous-attempt' : ''}` }, [
      node('div', { class: 'task-tool-heading' }, [node('div', {}, [node('h3', { text: engineTitle(job.engineId || job.engine), title: engineTitle(job.engineId || job.engine) }),
        variant ? node('span', { class: 'task-variant', text: `量化版本 ${variant}` }) : null,
        APPEARANCE_LABELS[appearance] ? node('span', { class: `task-appearance ${appearance}`, text: appearance === 'geometry' ? '无贴图几何 · 白模' : APPEARANCE_LABELS[appearance] }) : null]), statusBadge((job.engineId || job.engine) === 'imported-model' && job.status === 'generated-unreviewed' ? 'imported' : job.status), actions])]);
    if (mainFiles.length) {
      const first = mainFiles[0];
      panel.append(node('div', { class: `task-primary-assets${task.kind === 'image' ? ' generated-image-assets' : ''}` }, [assetCard(first.asset, 'task', task.id, first.index, '', jobIndex)]));
      if (mainFiles.length > 1) panel.append(resultDetails(`${task.id}:${job.id || job.runId}:models`, `${task.kind === 'image' ? '更多图片' : '更多模型'}（${mainFiles.length - 1}）`, [
        node('div', { class: 'task-more-assets' }, mainFiles.slice(1).map(({ asset, index }) => assetCard(asset, 'task', task.id, index, '', jobIndex)))], 'more-models'));
    } else {
      const message = job.status === 'queued' ? '已加入队列，轮到此工具后开始生产。'
        : job.status === 'running' ? job.phase || '工具正在生产，完成后将在这里显示产物。'
        : job.status === 'failed' ? retiredJob(job, task) ? '历史任务失败，可查看原因与日志；此功能不再提供重试。' : '本次生产失败，可查看原因并重试。'
        : job.status === 'cancelled' ? '本次任务已取消。'
        : job.status === 'interrupted' ? retiredJob(job, task) ? '历史任务已中断；此功能不再提供重试。' : '本次任务已中断，可重新加入队列。' : '尚未发现可预览的产物。';
      panel.append(node('div', { class: `task-output-empty ${job.status}` }, [node('span', { class: 'empty-symbol', text: job.status === 'running' ? '◌' : '◇', 'aria-hidden': 'true' }), node('p', { text: message })]));
    }
    if (job.status === 'running') panel.append(jobProgress(job));
    if (job.error) panel.append(errorExplanation(job.error, job.id, retiredJob(job, task)));
    const details = node('div', { class: 'task-output-details' });
    if (Number(job.elapsedSeconds) > 0) details.append(node('p', { class: 'task-tool-time', text: `已用时 ${duration(job.elapsedSeconds)}` }));
    const other = renderOtherFiles(task, job, jobIndex, otherFiles);
    if (other) details.append(other);
    if (job.logTail || ['queued', 'running'].includes(job.status)) details.append(resultDetails(`${task.id}:${job.id || job.runId}:log`, '运行日志', [node('pre', { class: 'task-log', text: jobLogText(job) })], 'task-log-details'));
    if (details.childElementCount) panel.append(details);
    return panel;
  }

  function renderResultTask(task) {
    const jobs = taskJobs(task);
    const imported = task.kind === 'imported-model' || jobs.some(job => (job.engineId || job.engine) === 'imported-model');
    const engines = new Map();
    jobs.forEach((job, index) => {
      const engine = job.engineId || job.engine || 'unknown';
      if (!engines.has(engine)) engines.set(engine, []);
      engines.get(engine).push({ job, index });
    });
    const completed = [...engines.values()].filter(attempts => attempts.at(-1).job.status === 'generated-unreviewed').length;
    const hasAssets = jobs.some(job => validAssets(job.assets).length);
    const downloadUrl = safeUrl(task.downloadUrl);
    const headingActions = node('div', { class: 'task-heading-actions' });
    if (hasAssets && downloadUrl) headingActions.append(node('a', { class: 'button small', href: downloadUrl, download: '', text: '任务全部下载 ↓' }));
    if (hasAssets && task.source === 'queue' && task.batchId && task.kind !== 'image') headingActions.append(node('a', { class: 'button small ghost', href: `/api/batches/${encodeURIComponent(task.batchId)}/download`, download: '', text: '整批下载 ↓' }));
    const taskTitle = task.name || (task.kind === 'image' ? '生成图片' : '未命名物体');
    const card = node('section', { class: `result-task-card${task.kind === 'image' ? ' image-task-card' : ''}${engines.size > 1 ? ' comparison-task-card' : ''}`, dataset: { resultTaskId: task.id } }, [
      node('div', { class: 'result-task-heading' }, [node('div', { class: 'task-title' }, [node('div', { class: 'task-title-line' }, [
        node('span', { class: 'task-kind-label', text: imported ? '导入模型' : task.kind === 'image' ? '图片作品' : task.kind === 'video' ? '视频作品' : engines.size > 1 ? '多模型对比' : '3D 作品' }),
        node('h2', { text: taskTitle, title: taskTitle })]),
        node('p', { text: [timeLabel(task.createdAt), imported ? '从本机导入' : `${completed} / ${engines.size} 个工具已生成`, task.source === 'history' ? '历史记录' : ''].filter(Boolean).join(' · ') })])])]);
    const outputs = node('div', { class: 'task-output-panel', 'aria-label': imported ? '导入的模型' : task.kind === 'image' ? '生成图片' : task.kind === 'video' ? '生成视频' : '所选工具的 3D 结果' });
    const toolGrid = node('div', { class: `task-tools-grid${engines.size === 1 ? ' single-tool' : ''}` });
    for (const [engine, attempts] of engines) {
      const latest = attempts.at(-1);
      const tool = node('div', { class: 'task-tool-group' }, [renderTaskJob(task, latest.job, latest.index)]);
      if (attempts.length > 1) tool.append(resultDetails(`${task.id}:${engine}:attempts`, `此前尝试（${attempts.length - 1}）`, attempts.slice(0, -1).reverse().map(({ job, index }) => renderTaskJob(task, job, index, true)), 'previous-attempts'));
      toolGrid.append(tool);
    }
    outputs.append(toolGrid);
    const metadata = node('dl', { class: 'task-metadata-list' }, [
      node('div', {}, [node('dt', { text: '任务编号' }), node('dd', { text: task.id, title: task.id })]),
      task.batchName ? node('div', {}, [node('dt', { text: '批次' }), node('dd', { text: task.batchName, title: task.batchName })]) : null,
      task.createdAt ? node('div', {}, [node('dt', { text: '创建时间' }), node('dd', { text: timeLabel(task.createdAt), title: String(task.createdAt) })]) : null
    ]);
    card.append(node('div', { class: 'result-task-body' }, [outputs]),
      node('div', { class: 'task-provenance' }, [renderTaskInputs(task)]),
      node('div', { class: 'task-card-footer' }, [resultDetails(`${task.id}:metadata`, '作品详情', [metadata], 'task-metadata'), headingActions]));
    return card;
  }

  function renderResults() {
    if (!state.api) {
      $('#results-list').replaceChildren(emptyState('正在读取任务', '连接本机创作引擎后，原图、工具状态与历史产物会自动显示。'));
      return;
    }
    const tasks = [...resultTasks()].filter(matchesResultTask).sort((first, second) => (Date.parse(second.createdAt) || 0) - (Date.parse(first.createdAt) || 0));
    const groups = tasks.map(renderResultTask);
    $('#results-list').replaceChildren(...(groups.length ? groups : [emptyState('等待你的第一个任务', $('#result-search').value || $('#result-engine-filter').value
      ? '没有匹配的任务，请调整搜索或工具筛选。' : '开始生产后，每个物体的原图、工具进度和最终产物都会放在同一张任务卡里。', '▦', { href: '#workspace', label: '导入图片' })]));
  }

  async function mutateJob(path, key, label) {
    if (state.busy.has(key)) return;
    state.busy.add(key); if (state.page === 'queue') renderQueue(); if (state.page === 'imagegen') renderImageJobs(); if (state.page === 'home') renderPipelines();
    try { await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', timeout: 20000 }); notify(label); await loadState(); }
    catch (error) { notify(error.message, true); await loadState(); }
    finally { state.busy.delete(key); if (state.page === 'queue') renderQueue(); if (state.page === 'imagegen') renderImageJobs(); if (state.page === 'home') renderPipelines(); }
  }

  function requestDeleteResult(button) {
    const { deleteId, deleteKind, deleteName, deleteEngine } = button.dataset;
    if (!deleteId || !['job', 'image', 'history'].includes(deleteKind)) return;
    state.deletion = { id: deleteId, kind: deleteKind };
    $('#delete-result-name').textContent = `${deleteName || '本次生成'} · ${deleteEngine || '生产工具'}`;
    $('#confirm-delete-result').disabled = false;
    $('#delete-result-dialog').showModal();
  }

  async function confirmDeleteResult() {
    if (!state.deletion || state.busy.has('delete-result')) return;
    const target = state.deletion;
    const prefix = target.kind === 'history' ? 'history' : target.kind === 'image' ? 'image-jobs' : 'jobs';
    state.busy.add('delete-result');
    $('#confirm-delete-result').disabled = true;
    $('#confirm-delete-result').textContent = '删除中…';
    try {
      await api(`/api/${prefix}/${encodeURIComponent(target.id)}/delete`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', timeout: 30000 });
      $('#delete-result-dialog').close(); state.deletion = null;
      await loadState();
      notify('本次生成及附带文件已删除，其他工具结果不受影响。');
      announce('本次生成已删除。');
    } catch (error) { notify(`删除未完成：${error.message}`, true); await loadState(); }
    finally {
      state.busy.delete('delete-result');
      $('#confirm-delete-result').disabled = false;
      $('#confirm-delete-result').textContent = '删除本次生成';
      if (state.page === 'results') renderResults();
      if (state.page === 'imagegen') renderImageJobs();
    }
  }

  function useGeneratedImage(jobId, index) {
    const job = imageJobs().find(item => item.id === jobId);
    const asset = validAssets(job?.assets)[Number(index)];
    if (!job || !asset || !isImage(asset)) { notify('生成图片尚未刷新，请稍后重试。'); return; }
    showImageUse(asset, job.objectName || String(job.prompt || '').slice(0, 60) || '生成图片');
  }

  function resultSelection(taskId, jobIndex, assetIndex) {
    const task = resultTasks().find(item => item.id === taskId);
    const job = task && taskJobs(task)[Number(jobIndex)];
    const assets = validAssets(job?.assets);
    return { task, job, assets, asset: assets[Number(assetIndex)] };
  }

  function useResultImage(taskId, jobIndex, assetIndex) {
    const { task, asset } = resultSelection(taskId, jobIndex, assetIndex);
    if (!task || task.kind !== 'image' || !asset || !isImage(asset)) { notify('生成图片尚未刷新，请稍后重试。'); return; }
    showImageUse(asset, task.name || '生成图片');
  }

  function showImageUse(asset, name) {
    state.imageUse = { asset, name };
    $('#image-use-preview').src = safeUrl(asset.url);
    const atLimit = state.objects.length >= MAX_OBJECTS;
    $('#image-use-object').replaceChildren(node('option', { value: '__new__', text: atLimit ? `已达 ${MAX_OBJECTS} 个物体上限` : '新增物体（默认）', disabled: atLimit }), ...state.objects.map(object => node('option', { value: object.id, text: object.name || '未命名物体' })));
    if (atLimit) $('#image-use-object').value = state.objects[0].id;
    $('#image-use-view').value = 'main';
    updateImageUseTarget();
    $('#image-use-dialog').showModal();
  }

  function updateImageUseTarget() {
    const target = $('#image-use-object').value;
    const view = $('#image-use-view');
    if (target === '__new__') { view.value = 'main'; view.disabled = true; }
    else view.disabled = false;
    const object = state.objects.find(item => item.id === target);
    const existing = object && (view.value === 'main' ? object.main : object.auxiliary[view.value]);
    $('#image-use-note').textContent = target === '__new__' ? '新增一个物体，并将这张图片用作主图。'
      : existing ? `将替换“${object?.name || '物体'}”当前的${view.value === 'main' ? '主图' : `${VIEW_LABELS[view.value]}辅助图`}。`
      : `加入“${object?.name || '物体'}”的${view.value === 'main' ? '主图' : `${VIEW_LABELS[view.value]}辅助图`}。辅助图应描述同一个物体的对应角度。`;
  }

  async function confirmImageUse() {
    if (!state.imageUse || state.busy.has('image-use')) return;
    const importRecord = state.imageUse;
    const target = $('#image-use-object').value;
    const view = $('#image-use-view').value;
    if (target === '__new__' && state.objects.length >= MAX_OBJECTS) { notify(`每批最多 ${MAX_OBJECTS} 个物体，请选择已有物体或先移除一个物体。`, true); return; }
    if (target !== '__new__' && !state.objects.some(object => object.id === target)) { notify('目标物体已移除，请重新选择。', true); return; }
    state.busy.add('image-use');
    $('#confirm-image-use').disabled = true;
    $('#confirm-image-use').textContent = '加入中…';
    try {
      const result = await api('/api/images/from-output', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: importRecord.asset.url }), timeout: 20000 });
      const image = imageRecord(result);
      if (!image) throw new Error('生成图片导入失败，请重试。');
      let object = state.objects.find(item => item.id === target);
      if (target === '__new__') {
        if (state.objects.length >= MAX_OBJECTS) throw new Error(`准备区已达 ${MAX_OBJECTS} 个物体，请选择已有物体或先移除一个物体。`);
        if (state.objects.length === 1 && !state.objects[0].main && !Object.values(state.objects[0].auxiliary).some(Boolean)
          && /^物体 \d+$/.test(state.objects[0].name)) state.objects = [];
        object = newObject(image); object.name = importRecord.name.slice(0, 80); state.objects.push(object);
      }
      else if (!object) throw new Error('目标物体已移除，请重新选择。');
      else if (view === 'main') object.main = image;
      else object.auxiliary[view] = image;
      saveDraft(); renderObjects();
      $('#image-use-dialog').close(); state.imageUse = null;
      window.location.hash = 'workspace';
      notify('生成图片已加入准备区，选择 3D 工具即可开始生产。');
    } catch (error) { notify(error.message, true); }
    finally {
      state.busy.delete('image-use'); $('#confirm-image-use').disabled = false; $('#confirm-image-use').textContent = '加入准备区';
    }
  }

  // Pixal3D emits Z-up GLBs, which violates the glTF convention that +Y is up. A
  // Y-up viewer therefore shows every prop lying on its back. <model-viewer>'s
  // `orientation` is expressed as "roll pitch yaw" (roll about Z, pitch about X,
  // yaw about Y), so the corrective +90 deg rotation about X goes in the MIDDLE
  // slot -- verified against the real preview: "90deg 0deg 0deg" rolls the prop
  // onto its side instead. Engines that already emit Y-up glTF are left untouched.
  const PREVIEW_ORIENTATION = { pixal3d: '0deg 90deg 0deg' };

  function openPreview(assets, title, selectedIndex = 0, engineId = null) {
    const files = validAssets(assets);
    if (!files.length) { notify('当前还没有可预览的产物。'); return; }
    state.preview = { assets: files, title, index: 0, token: 0, engineId };
    const dialog = $('#asset-dialog');
    if (!dialog.open) dialog.showModal();
    selectPreview(Math.max(0, Math.min(files.length - 1, selectedIndex)));
  }

  function previewJob(jobId, assetIndex) {
    const job = allJobs().find(item => item.id === jobId);
    if (!job) { notify('任务尚未刷新，请稍后重试。'); return; }
    const assets = validAssets(job.assets);
    const index = assetIndex === undefined ? Math.max(0, assets.findIndex(asset => extension(asset) === 'glb')) : Number(assetIndex);
    openPreview(assets, `${job.objectName || '物体'} · ${engineTitle(job.engine)}`, index, job.engine);
  }

  function previewHistory(historyId, assetIndex) {
    const history = (state.api?.history || []).find(item => item.id === historyId);
    if (!history) { notify('历史产物尚未刷新，请稍后重试。'); return; }
    const assets = validAssets(history.assets);
    const index = assetIndex === undefined ? Math.max(0, assets.findIndex(asset => extension(asset) === 'glb')) : Number(assetIndex);
    openPreview(assets, `${engineTitle(history.engine)} · ${timeLabel(history.createdAt)}`, index, history.engine);
  }

  function previewResultTask(taskId, jobIndex, assetIndex) {
    const { task, job, assets } = resultSelection(taskId, jobIndex, assetIndex);
    if (!task || !job) { notify('任务结果尚未刷新，请稍后重试。'); return; }
    const index = assetIndex === undefined ? Math.max(0, assets.findIndex(asset => extension(asset) === 'glb')) : Number(assetIndex);
    openPreview(assets, `${task.name || '任务'} · ${engineTitle(job.engineId || job.engine)}`, index, job.engineId || job.engine);
  }

  async function selectPreview(index) {
    if (!state.preview || !state.preview.assets[index]) return;
    const preview = state.preview;
    preview.index = index;
    const token = ++preview.token;
    const asset = preview.assets[index];
    $('#preview-title').textContent = `${preview.title} / ${asset.name}`;
    $('#preview-file-list').replaceChildren(...preview.assets.map((file, fileIndex) => node('button', {
      class: `preview-file${fileIndex === index ? ' selected' : ''}`, type: 'button', dataset: { action: 'preview-file', assetIndex: fileIndex }, 'aria-pressed': fileIndex === index
    }, [node('span', { text: file.name, title: file.name }), node('small', { text: bytes(file.bytes) })])));
    const download = $('#preview-download');
    download.href = safeUrl(asset.url); download.download = asset.name;
    const content = $('#preview-content');
    content.replaceChildren();
    $('#preview-message').textContent = '';
    $('#model-controls').hidden = true;
    $('#auto-rotate').checked = false;
    const modelUrl = extension(asset) === 'glb' || extension(asset) === 'gltf' ? safeUrl(asset.url) : safeUrl(asset.previewUrl);
    if (isModel(asset) && modelUrl && (['glb', 'gltf'].includes(extension(asset)) || /\.(glb|gltf)(?:[?#]|$)/i.test(modelUrl))) {
      const orientation = PREVIEW_ORIENTATION[preview.engineId];
      const viewer = node('model-viewer', { src: modelUrl, alt: `${preview.title}的三维产物`, 'camera-controls': true,
        'touch-action': 'pan-y', 'shadow-intensity': '1', exposure: '1', 'interaction-prompt': 'none', 'loading': 'eager', 'aria-label': '三维模型预览，可拖动旋转并缩放',
        ...(orientation ? { orientation } : {}) });
      $('#preview-message').textContent = '正在载入 3D 模型…';
      viewer.addEventListener('load', () => { if (state.preview === preview && preview.token === token) $('#preview-message').textContent = ''; });
      viewer.addEventListener('error', () => { if (state.preview === preview && preview.token === token) $('#preview-message').textContent = '模型预览失败，仍可下载文件检查。'; });
      viewer.addEventListener('progress', event => {
        if (state.preview === preview && preview.token === token && event.detail?.totalProgress < 1) $('#preview-message').textContent = `正在载入模型 ${Math.round(event.detail.totalProgress * 100)}%`;
      });
      content.append(viewer); $('#model-controls').hidden = false;
      customElements.whenDefined('model-viewer').catch(() => {});
      setTimeout(() => {
        if (state.preview === preview && preview.token === token && !customElements.get('model-viewer')) $('#preview-message').textContent = '3D 预览组件尚未载入，请重新打开应用。文件仍可下载。';
      }, 10000);
    } else if (isImage(asset)) {
      const img = node('img', { src: safeUrl(asset.url), alt: asset.name });
      img.addEventListener('error', () => { if (state.preview === preview && preview.token === token) $('#preview-message').textContent = '图片预览失败，仍可下载文件。'; });
      content.append(img);
    } else if (isVideo(asset)) {
      const video = node('video', { src: safeUrl(asset.url), controls: true, playsinline: true, preload: 'metadata', 'aria-label': asset.name });
      video.addEventListener('error', () => { if (state.preview === preview && preview.token === token) $('#preview-message').textContent = '视频预览失败，仍可下载文件检查。'; });
      content.append(video);
    } else if (isText(asset)) {
      $('#preview-message').textContent = '正在读取文件…';
      try {
        const response = await fetch(safeUrl(asset.url), { cache: 'no-store' });
        if (!response.ok) throw new Error('文件读取失败');
        const text = await response.text();
        if (state.preview !== preview || preview.token !== token) return;
        content.append(node('pre', { text: text.slice(0, 250000) + (text.length > 250000 ? '\n\n…文件较大，请下载查看完整内容。' : '') }));
        $('#preview-message').textContent = '';
      } catch {
        if (state.preview === preview && preview.token === token) $('#preview-message').textContent = '文件预览失败，请下载查看。';
      }
    } else {
      content.append(node('div', { class: 'preview-content-empty' }, [modelIcon(), node('h3', { text: `${extension(asset).toUpperCase()} 文件已生成` }),
        node('p', { text: isModel(asset) ? '当前格式可以下载使用。如需在这里旋转预览，请从右侧选择同次生成的 GLB 文件。' : '当前文件类型支持下载。' })]));
    }
  }

  function closePreview() {
    $('#asset-dialog').close();
    state.preview = null;
    $('#preview-content').replaceChildren();
  }

  function updateConnection(connected) {
    state.connected = connected;
    const label = $('#connection-status');
    label.className = `connection ${connected ? 'connected' : 'disconnected'}`;
    label.textContent = connected ? '已连接本机' : '连接中断 · 自动重连';
    label.title = connected ? '任务与作品来自本机创作引擎' : '保留最后读取的任务状态，连接恢复后自动更新';
    updateSummary();
    updateImageSummary();
    updatePipelineSummary();
    updateImportControls();
  }

  async function loadState() {
    if (state.polling) return;
    state.polling = true;
    const referenceRevision = state.referenceRevision;
    try {
      const result = await api('/api/state');
      if (!result || !Array.isArray(result.engines) || !Array.isArray(result.batches)) throw new Error('工厂状态格式不完整');
      state.api = result;
      for (const [key, reference] of state.recentReferences) if (reference.revision <= referenceRevision) state.recentReferences.delete(key);
      document.dispatchEvent(new CustomEvent('factory-state', { detail: result }));
      updateConnection(true);
      loadSamples();
      if (!state.selectedBefore) {
        const defaultEngine = result.engines.filter(engine => engineAvailable(engine) && !engine.multiView).sort((first, second) => colorRank(first) - colorRank(second)).find(coloredEngine);
        if (defaultEngine) { state.selectedEngines.add(defaultEngine.id); state.selectedBefore = true; saveDraft(); }
      }
      const version = String(result.version || '0.3.0');
      const versionLabel = version.startsWith('v') ? version : `v${version}`;
      $('#product-version').textContent = versionLabel; $('#footer-version').textContent = versionLabel;
      document.title = `build something · 造点啥 · ${versionLabel}`;
      $('#machine-info').textContent = `${result.machine?.gpu || '本机 GPU'} · ${result.machine?.queue || '串行生产'}`;
      const jobs = allJobs();
      $('#queue-count').textContent = String(jobs.filter(job => ['queued', 'running'].includes(job.status)).length);
      $('#result-count').textContent = String(resultTasks().length);
      for (const job of jobs) {
        const previous = state.previousJobs.get(job.id);
        if (previous && previous !== job.status && TERMINAL.has(job.status)) announce(`${job.objectName || '物体'}，${engineTitle(job.engine)}，${STATUS_LABELS[job.status] || job.status}。`);
        state.previousJobs.set(job.id, job.status);
      }
      renderEngines();
      renderImageEngines();
      renderPipelineControls();
      for (const page of Object.keys(state.mediaForms)) renderMediaForm(page);
      if (state.page === 'home') renderPipelines();
      if (state.page === 'queue') renderQueue();
      if (state.page === 'imagegen') renderImageJobs();
      const signature = JSON.stringify(result.resultTasks);
      if (signature !== state.resultsSignature) { state.resultsSignature = signature; if (state.page === 'results') renderResults(); }
    } catch {
      updateConnection(false);
      if (!state.api && state.page === 'queue') renderQueue();
      if (!state.api && state.page === 'results') renderResults();
      if (!state.api && state.page === 'imagegen') renderImageJobs();
    } finally {
      state.polling = false;
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(loadState, allJobs().some(job => ['queued', 'running'].includes(job.status)) || (state.api?.pipelines || []).some(pipeline => ['queued', 'running'].includes(pipeline.status)) ? 1800 : 3500);
    }
  }

  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    const { action, objectId, view, jobId, batchId, historyId, assetIndex } = button.dataset;
    if (action === 'dismiss-notification') { state.activeError = false; $('#notification').hidden = true; clearTimeout(state.noticeTimer); }
    else if (action === 'continue-image') await continueWithImage(button);
    else if (action === 'preview-source-model') {
      const url = safeUrl(button.dataset.sourceUrl);
      if (url) openPreview([{ kind: 'model', name: button.dataset.sourceName || 'input.glb', url, bytes: Number(button.dataset.sourceBytes) || 0 }], '输入模型');
    }
    else if (action === 'add-object') {
      if (state.objects.length >= MAX_OBJECTS) { notify(`每批最多 ${MAX_OBJECTS} 个物体，请先生产本批或移除一个物体。`); return; }
      state.objects.push(newObject()); saveDraft(); renderObjects();
    }
    else if (action === 'remove-object') {
      state.objects = state.objects.filter(object => object.id !== objectId);
      if (!state.objects.length) state.objects.push(newObject());
      saveDraft(); renderObjects();
    } else if (action === 'pick-slot') pickImages({ objectId, view });
    else if (action === 'import-mains') pickImages({ mains: true }, true);
    else if (action === 'remove-image') {
      const object = state.objects.find(item => item.id === objectId);
      if (object) { if (view === 'main') object.main = null; else delete object.auxiliary[view]; saveDraft(); renderObjects(); }
    } else if (action === 'sample') await useSample();
    else if (action === 'start') await startProduction();
    else if (action === 'start-imagegen') await startImageGeneration();
    else if (action === 'start-media') await startMediaGeneration(button.dataset.mediaPage);
    else if (action === 'remove-media-reference') {
      const page = button.dataset.mediaPage, draft = state.mediaForms[page];
      if (draft && !draft.busy && !draft.uploading) {
        if (['mask', 'control', 'ipAdapter'].includes(button.dataset.referenceName)) { draft[button.dataset.referenceName] = null; $(`#${page}-${button.dataset.referenceName}`).value = ''; }
        else if (Number.isInteger(Number(button.dataset.referenceIndex))) { draft.inputs.splice(Number(button.dataset.referenceIndex), 1); $(`#${page}-input`).value = ''; }
        saveDraft(); renderMediaForm(page);
      }
    }
    else if (action === 'start-pipeline') await startPipeline();
    else if (action === 'pipeline-remove-model') { state.pipelineEngines.delete(button.dataset.engineId); state.pipelineChosen = true; saveDraft(); renderPipelineControls(); }
    else if (action === 'remove-engine-selection') { state.selectedEngines.delete(button.dataset.engineId); state.selectedBefore = true; saveDraft(); renderEngines(); }
    else if (action === 'cancel-pipeline') await mutateJob(`/api/pipelines/${encodeURIComponent(button.dataset.pipelineId)}/cancel`, button.dataset.pipelineId, '已请求停止本次创作，已有作品保留。');
    else if (action === 'retry-pipeline') await mutateJob(`/api/pipelines/${encodeURIComponent(button.dataset.pipelineId)}/retry`, button.dataset.pipelineId, '已重新创建生图与建模流程，原有作品保留。');
    else if (action === 'import-model') { const input = $('#import-model-file'); input.value = ''; input.click(); }
    else if (action === 'refresh') { await loadState(); notify(state.connected ? '本机状态已刷新。' : '暂未连接本机创作引擎，正在自动重连。', !state.connected); }
    else if (action === 'queue-filter') {
      state.queueFilter = button.dataset.filter;
      for (const filter of document.querySelectorAll('[data-action="queue-filter"]')) { const selected = filter === button; filter.classList.toggle('active', selected); filter.setAttribute('aria-pressed', String(selected)); }
      renderQueue();
    } else if (action === 'cancel-job') await mutateJob(`/api/${button.dataset.imageJob === '1' ? 'image-jobs' : 'jobs'}/${encodeURIComponent(jobId)}/cancel`, jobId, '已提交取消请求。');
    else if (action === 'use-model') scopeNotice();
    else if (action === 'retry-job') {
      const job = allJobs().find(item => item.id === jobId);
      const task = resultTasks().find(item => item.jobs?.some(record => record.id === jobId));
      if (retiredJob(job, task)) { scopeNotice(); return; }
      await mutateJob(`/api/${button.dataset.imageJob === '1' ? 'image-jobs' : 'jobs'}/${encodeURIComponent(jobId)}/retry`, jobId, '任务已重新加入队列。');
    }
    else if (action === 'cancel-batch') await mutateJob(`/api/batches/${encodeURIComponent(batchId)}/cancel`, batchId, '已提交本批取消请求。');
    else if (action === 'preview-job') previewJob(jobId, assetIndex);
    else if (action === 'preview-history') previewHistory(historyId, assetIndex);
    else if (action === 'preview-result') previewResultTask(button.dataset.taskId, button.dataset.resultJobIndex, assetIndex);
    else if (action === 'preview-file') await selectPreview(Number(assetIndex));
    else if (action === 'use-generated-image') useGeneratedImage(jobId, assetIndex);
    else if (action === 'use-result-image') useResultImage(button.dataset.taskId, button.dataset.resultJobIndex, assetIndex);
    else if (action === 'delete-result') requestDeleteResult(button);
    else if (action === 'confirm-delete-result') await confirmDeleteResult();
    else if (action === 'close-delete-result' && !state.busy.has('delete-result')) { $('#delete-result-dialog').close(); state.deletion = null; }
    else if (action === 'confirm-image-use') await confirmImageUse();
    else if (action === 'close-image-use') { $('#image-use-dialog').close(); state.imageUse = null; }
    else if (action === 'close-preview') closePreview();
    else if (action === 'reset-camera') {
      const viewer = $('#preview-content model-viewer');
      if (viewer) { viewer.cameraOrbit = '0deg 75deg auto'; viewer.cameraTarget = 'auto auto auto'; viewer.fieldOfView = 'auto'; viewer.jumpCameraToGoal?.(); }
    }
  });

  document.addEventListener('input', event => {
    if (event.target.dataset.objectName) {
      const object = state.objects.find(item => item.id === event.target.dataset.objectName);
      if (object) { object.name = event.target.value; saveDraft(); }
    } else if (event.target.id === 'batch-name') saveDraft();
    else if (event.target.id === 'result-search') renderResults();
    else if (['image-prompt', 'image-steps', 'image-seed'].includes(event.target.id)) { saveDraft(); updateImageSummary(); }
    else if (event.target.id === 'pipeline-prompt') { saveDraft(); updatePipelineSummary(); }
  });

  document.addEventListener('change', async event => {
    const target = event.target;
    if (target.dataset.pipelineEngineId) {
      if (target.checked) state.pipelineEngines.add(target.dataset.pipelineEngineId); else state.pipelineEngines.delete(target.dataset.pipelineEngineId);
      state.pipelineChosen = true; saveDraft(); renderPipelineControls();
    } else if (target.id === 'pipeline-image-engine') {
      state.pipelineImageEngine = target.value; state.pipelineWeightVariant = ''; saveDraft(); renderPipelineControls();
    } else if (target.id === 'pipeline-weight-variant') {
      state.pipelineWeightVariant = target.value; saveDraft(); renderPipelineControls();
    } else if (target.dataset.engineId) {
      if (target.checked) state.selectedEngines.add(target.dataset.engineId); else state.selectedEngines.delete(target.dataset.engineId);
      state.selectedBefore = true; saveDraft(); renderEngines();
    } else if (target.dataset.imageEngineId) {
      state.selectedImageEngine = target.dataset.imageEngineId; state.selectedWeightVariant = ''; applyImageDefaults((state.api?.imageEngines || []).find(engine=>engine.id===state.selectedImageEngine)); saveDraft(); renderImageEngines();
    } else if (target.id === 'image-weight-variant') {
      state.selectedWeightVariant = target.value; saveDraft(); renderWeightVariants(); updateImageSummary();
    } else if (['image-width', 'image-height', 'image-random-seed'].includes(target.id)) {
      $('#image-seed').disabled = $('#image-random-seed').checked; saveDraft(); updateImageSummary();
    } else if (['image-use-object', 'image-use-view'].includes(target.id)) updateImageUseTarget();
    else if (target.id === 'image-picker') {
      const files = [...(target.files || [])];
      const destination = pickerTarget;
      pickerTarget = null;
      if (!files.length || !destination) return;
      if (destination.mains) await importMainFiles(files);
      else await importToSlot(files[0], destination.objectId, destination.view);
    } else if (target.id === 'import-model-file') await importModel(target.files?.[0]);
    else if (target.id === 'result-engine-filter') renderResults();
    else if (target.id === 'auto-rotate') {
      const viewer = $('#preview-content model-viewer');
      if (viewer) viewer.autoRotate = target.checked;
    }
  });

  document.addEventListener('toggle', event => {
    if (event.target.id === 'pipeline-more') state.pipelineMoreOpen = event.target.open;
    const key = event.target.dataset?.resultDetails;
    if (key) { if (event.target.open) state.openResultDetails.add(key); else state.openResultDetails.delete(key); }
    const objectId = event.target.dataset?.auxiliaryObject;
    if (objectId) { if (event.target.open) state.openAuxiliaries.add(objectId); else state.openAuxiliaries.delete(objectId); }
  }, true);

  $('#delete-result-dialog').addEventListener('cancel', event => {
    if (state.busy.has('delete-result')) event.preventDefault();
    else state.deletion = null;
  });

  document.addEventListener('dragover', event => {
    const slot = event.target.closest('[data-drop-slot]');
    if (!slot) {
      if ([...(event.dataTransfer?.types || [])].includes('Files')) event.preventDefault();
      return;
    }
    event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; slot.classList.add('drop-active');
  });
  document.addEventListener('dragleave', event => {
    const slot = event.target.closest('[data-drop-slot]');
    if (slot && !slot.contains(event.relatedTarget)) slot.classList.remove('drop-active');
  });
  document.addEventListener('drop', async event => {
    const slot = event.target.closest('[data-drop-slot]');
    if (!slot) {
      if (event.dataTransfer?.files?.length) { event.preventDefault(); notify('请把图片拖入主图或辅助图区域。'); }
      return;
    }
    event.preventDefault(); slot.classList.remove('drop-active');
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    const { objectId, view } = slot.dataset;
    await importToSlot(files[0], objectId, view);
    if (files.length > 1) {
      if (view === 'main') await importMainFiles(files.slice(1));
      else notify('每个辅助方向只接受一张图，其余图片请放入其他方向。');
    }
  });

  $('#asset-dialog').addEventListener('cancel', () => { state.preview = null; $('#preview-content').replaceChildren(); });
  $('#image-use-dialog').addEventListener('cancel', () => { state.imageUse = null; });
  $('#asset-dialog').addEventListener('click', event => {
    if (event.target !== $('#asset-dialog')) return;
    const bounds = event.target.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closePreview();
  });
  window.addEventListener('hashchange', () => setPage(window.location.hash.slice(1)));
  document.addEventListener('setup-updated', loadState);
  document.addEventListener('factory-refresh', loadState);
  document.addEventListener('factory-model-choice', event => selectCreationModel(event.detail));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadState(); });

  initializeMediaForms(); restoreDraft(); renderObjects(); setPage(window.location.hash.slice(1)); loadState();
})();
