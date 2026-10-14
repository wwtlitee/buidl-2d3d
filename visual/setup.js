/* build something · 造点啥 v0.8.0 · Consumer setup · Unclecow */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const KINDS = { model: '图片变 3D', image: '文字生成图片', edit: '图片编辑与图生图', multiview: '补充多个视角', texture: '模型上色' };
  const STATUS = { ready: '已就绪', installed: '已安装，可尝试生成', missing: '未安装', incomplete: '需要补齐', blocked: '需要先解决问题', queued: '等待安装', running: '安装中', completed: '安装完成', failed: '安装失败', cancelled: '已取消', interrupted: '安装中断' };
  const initialHash = window.location.hash;
  const state = { snapshot: null, factory: null, loading: false, loadPromise: null, busy: false, selected: null, timer: null, modelSignature: '', jobSignature: '', firstState: true, openLogs: new Set(), openGroups: new Set() };
  function node(tag, attributes = {}, children = []) {
    const item = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value === null || value === undefined) continue;
      if (key === 'text') item.textContent = value;
      else if (key === 'class') item.className = value;
      else if (key === 'dataset') Object.assign(item.dataset, value);
      else if (['disabled', 'hidden', 'open'].includes(key)) item[key] = !!value;
      else item.setAttribute(key, String(value));
    }
    for (const child of children) if (child != null) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return item;
  }
  function size(value) {
    const number = Number(value);
    if (number === 0 && value !== null && value !== undefined) return '0 MB';
    if (!Number.isFinite(number) || number <= 0) return '以安装说明为准';
    return number >= 1073741824 ? `${(number / 1073741824).toFixed(1)} GB` : `${Math.ceil(number / 1048576)} MB`;
  }
  function externalUrl(value) {
    try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch { return ''; }
  }
  function link(label, url) { const href = externalUrl(url); return href ? node('a', { class: 'inline-link', text: label, href, target: '_blank', rel: 'noopener noreferrer' }) : null; }
  function installationError(message, key = '', canRetry = true) {
    const details = node('details', { open: key && state.openLogs.has(`error:${key}`) }, [node('summary', { text: '查看详细原因' }), node('pre', { text: String(message || '未收到详细原因。') })]);
    if (key) details.addEventListener('toggle', () => { if (details.open) state.openLogs.add(`error:${key}`); else state.openLogs.delete(`error:${key}`); });
    return node('div', { class: 'error-explanation' }, [node('strong', { text: '模型准备没有完成' }),
      node('p', { text: canRetry ? '先查看下方原因与安装记录；检查网络、可用空间和模型授权后再重试，已下载文件会尽量复用。' : '这是历史安装记录。此模型当前不再提供安装或重试，已有作品仍可预览、下载。' }),
      details]);
  }
  function feedback(message, error = false) {
    const box = $('#setup-feedback');
    if (!error && box.classList.contains('error') && !box.hidden) {
      let update = $('#setup-feedback-update'); if (!update) { update = node('p', { id: 'setup-feedback-update', role: 'status' }); box.append(update); }
      update.textContent = message; return;
    }
    box.replaceChildren(error ? installationError(message) : document.createTextNode(message),
      node('button', { type: 'button', class: 'button small ghost', text: '关闭提示', dataset: { setupAction: 'dismiss-feedback' } }));
    box.hidden = !message; box.classList.toggle('error', error);
  }
  async function request(url, body) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, { method: body === undefined ? 'GET' : 'POST', signal: controller.signal, cache: 'no-store',
        headers: { 'Accept': 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Factory-Request': '1' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
      return result;
    } catch (error) { if (error.name === 'AbortError') throw new Error('连接超时，请先查看安装进度再重试，避免重复安装。'); throw error; }
    finally { clearTimeout(timer); }
  }
  function readiness(id, engines, label) {
    const box = $(id); if (!box) return;
    const connected = !!state.factory;
    const available = engines.some(engine => ['ready', 'installed'].includes(engine.status));
    box.hidden = !connected || available;
    if (!available && connected) box.replaceChildren(node('div', {}, [node('strong', { text: `先准备一个${label}工具` }), node('p', { text: '电脑里还没有可用模型。检测环境、安装推荐项，再回来开始。' })]), node('a', { href: '#setup', class: 'button primary', text: '检测并安装模型' }));
  }
  function updateReadiness() {
    readiness('#workspace-readiness', state.factory?.engines || [], '建模');
    readiness('#imagegen-readiness', state.factory?.imageEngines || [], '生图');
  }
  function renderHardware() {
    const hardware = state.snapshot.hardware || {};
    const gpus = Array.isArray(hardware.gpus) ? hardware.gpus : [];
    const gpu = gpus.length ? gpus.map(item => `${item.name}${Number(item.memoryMiB) > 0 ? ` · ${(item.memoryMiB / 1024).toFixed(0)} GB 显存` : ''}`).join(' / ') : '未检测到可用 NVIDIA 显卡';
    $('#setup-hardware').replaceChildren(...[
      ['显卡', gpu], ['内存', Number(hardware.ramGiB) > 0 ? `${hardware.ramGiB} GB` : '未能读取'],
      ['可用空间', size(hardware.freeDiskBytes)], ['安装位置', hardware.root || '当前项目目录']
    ].map(([label, value]) => node('div', { class: 'hardware-fact' }, [node('span', { text: label }), node('strong', { text: value })])));
    const requirements = Array.isArray(hardware.requirements) ? hardware.requirements : [];
    $('#setup-hardware-note').textContent = requirements.some(item => item.status === 'missing') ? '按下方提示补齐环境' : '检测完成';
    $('#setup-requirements').replaceChildren(...requirements.map(item => node('div', { class: `requirement-row ${item.status || ''}` }, [
      node('span', { class: 'requirement-symbol', text: item.status === 'ok' ? '✓' : '!', 'aria-hidden': 'true' }),
      node('div', {}, [node('strong', { text: item.label || item.id }), node('p', { text: item.detail || '' }), item.status !== 'ok' && item.remedy ? node('p', { class: 'requirement-remedy', text: item.remedy }) : null])
    ])));
  }
  function modes(model) { return Array.isArray(model.capabilities?.modes) ? model.capabilities.modes : []; }
  function creationSupported(model) {
    if (!model || model.creationEnabled === false || ['video', 'rig', 'animation', 'motion'].includes(model.kind)) return false;
    return Object.hasOwn(KINDS, catalogKind(model))
      && (['model', 'texture'].includes(model.kind) || !modes(model).length
        || modes(model).some(mode => ['t2i', 'txt2img', 'img2img', 'edit', 'inpaint', 'control', 'multiview'].includes(mode)));
  }
  function retiredFeedback() { feedback('首版专注本地生图与 3D 创作。视频、绑骨和动作模型不再提供安装或新建任务；已有作品仍可预览、下载。', true); }
  function catalogKind(model) { return modes(model).includes('multiview') ? 'multiview' : modes(model).some(mode => ['edit', 'inpaint', 'img2img', 'control'].includes(mode)) ? 'edit' : model.kind; }
  function destination(model) { const kind = typeof model === 'string' ? model : catalogKind(model); return { model: 'workspace', image: typeof model === 'object' && modes(model).length ? 'edit' : 'imagegen', edit: 'edit', multiview: 'multiview', texture: 'texture' }[kind] || 'home'; }
  function modelChoice(model) {
    const aliases = { t2i: 'txt2img' };
    const available = modes(model).map(mode => aliases[mode] || mode);
    const page = destination(model);
    const preferences = { edit: ['edit', 'img2img', 'txt2img', 'inpaint', 'control'], multiview: ['multiview'] };
    return { page, engineId: model.engineId || model.id, weightVariant: model.weightVariant || '',
      mode: preferences[page]?.find(mode => available.includes(mode)) || model.kind };
  }
  function creationLink(model, label) {
    return node('a', { class: 'button', href: `#${destination(model)}`, text: label || `开始${KINDS[catalogKind(model)] || '创作'} →`, dataset: { setupAction: 'use-model', modelId: model.id } });
  }
  function supplementaryVariant(model) { return !!model.weightVariant && !model.recommended || /^qwen-image-21-q\d/.test(model.id); }
  function colorRank(model) { return ['texture','painted'].includes(model.appearance) ? 0 : model.appearance === 'vertex' ? 1 : model.appearance === 'geometry' ? 3 : 2; }
  function renderModels() {
    const models = Array.isArray(state.snapshot.models) ? state.snapshot.models : [];
    const filter = $('#setup-filter').value;
    const query = ($('#setup-search')?.value || '').trim().toLocaleLowerCase();
    const signature = JSON.stringify([models, filter, query, state.busy, state.snapshot.jobs?.filter(job => ['queued', 'running'].includes(job.status)).map(job => job.modelId)]);
    if (signature === state.modelSignature) return; state.modelSignature = signature;
    const shown = models.filter(model => creationSupported(model) && !model.supportOnly && model.kind !== 'utility' && (filter === 'all' || catalogKind(model) === filter || filter === 'image' && modes(model).includes('txt2img')) && (!query || [model.title, model.description, model.id, KINDS[catalogKind(model)], ...modes(model)].join(' ').toLocaleLowerCase().includes(query)));
    const modelCard = (model, primaryChoice = false) => {
      const installed = ['ready', 'installed'].includes(model.status);
      const active = state.snapshot.jobs?.some(job => job.modelId === model.id && ['queued', 'running'].includes(job.status));
      const blockers = Array.isArray(model.blockers) ? model.blockers : [];
      const card = node('article', { class: `setup-model-card${primaryChoice ? ' recommended' : ''}`, dataset: { modelId: model.id } });
      card.append(node('div', { class: 'setup-model-top' }, [node('span', { class: 'setup-kind', text: KINDS[catalogKind(model)] || '本地模型' }), primaryChoice ? node('span', { class: 'recommendation-badge', text: model.recommended ? '推荐从这里开始' : '当前电脑可安装' }) : null]),
        node('h3', { text: model.title }), node('p', { class: 'setup-model-description', text: model.description || '' }),
        node('div', { class: 'setup-model-facts' }, [node('span', { class: `setup-status ${model.status}`, text: STATUS[model.status] || model.status || '正在检测' }), node('span', { text: installed ? '模型文件已在本机' : `模型下载约 ${size(model.remainingBytes || model.downloadBytes)}` })]));
      if (!installed) card.append(node('p', { class: 'field-hint', text: '运行环境可能需要额外下载，磁盘需求以安装检查为准。' }));
      if (catalogKind(model) === 'model') card.append(node('p', { class: 'setup-model-appearance', text: { texture: '一张图 → 自带颜色与贴图的模型', painted: '自带颜色与贴图的模型', vertex: '带顶点颜色，没有独立贴图', geometry: '仅生成形状 · 白模，不含颜色与贴图' }[model.appearance] || '颜色与贴图能力请查看此模型说明' }));
      if (catalogKind(model) === 'texture') card.append(node('p', { class: 'field-hint', text: '效果待验收：旧版上色效果未达到用户预期，尚无新的满意样例。优先尝试自带贴图的建模路线。' }));
      const profile = model.resourceProfile || {};
      const tierLabels = { recommended: '12 GB 优先试用', experimental: '实验路线', high: '需要更高配置', 'high-memory': '需要更高配置', unreleased: '尚未发布' };
      const tier = profile.tier || (model.releaseStatus === 'unreleased' ? 'unreleased' : '');
      if (tier || profile.label) card.append(node('span', { class: `setup-resource-tier ${String(tier).replace(/[^a-z-]/g, '')}`, text: profile.label || tierLabels[tier] || '配置要求见说明' }));
      const vram = Number(profile.minVramGiB || profile.minVramGb); const ram = Number(profile.minRamGiB || profile.minRamGb);
      if (vram > 0 || ram > 0) card.append(node('p', { class: 'field-hint', text: [vram > 0 ? `显存参考 ${vram} GB` : '', ram > 0 ? `系统内存参考 ${ram} GB` : ''].filter(Boolean).join(' · ') }));
      if (profile.note) card.append(node('p', { class: 'field-hint', text: profile.note }));
      if (model.validationStage) card.append(node('p', { class: 'field-hint', text: `验证阶段：${{ researched: '资料已核对，待本机试用', 'download-verified': '下载校验完成，待生成', 'installed-unverified': '环境已安装，待生成', 'generated-unreviewed': '本机已生成，效果待检查', 'local-verified': '已有本机生成记录', 'runtime-verified': '运行环境已检查，生成待试用' }[model.validationStage] || model.validationStage}` }));
      if (model.authRequired || model.requiresAuth) card.append(node('p', { class: 'field-hint' }, ['需官方账号授权。', node('a', { class: 'inline-link', href: '#setup-credentials', text: '提供本机读取 Token', dataset: { setupAction: 'show-credentials' } }), link('打开授权来源 ↗', model.authUrl || model.sourceUrl)]));
      if (blockers.length) card.append(node('div', { class: 'setup-blockers' }, blockers.map(message => node('p', { text: message }))));
      if (model.remedy || model.license?.notice || model.sourceUrl) card.append(node('details', { class: 'setup-instructions' }, [node('summary', { text: '安装说明与许可' }),
        model.remedy ? node('p', { text: model.remedy }) : null, model.license?.notice ? node('p', { text: model.license.notice }) : null,
        link(`模型许可${model.license?.label ? ` · ${model.license.label}` : ''} ↗`, model.license?.url), link('官方来源 ↗', model.sourceUrl)]));
      const actions = node('div', { class: 'setup-model-actions' });
      if (installed) actions.append(creationLink(model));
      else actions.append(node('button', { type: 'button', class: 'button primary', text: active ? '正在安装，查看下方进度' : model.canInstall ? '下载并安装' : '按说明准备环境', disabled: state.busy || active || !model.canInstall, dataset: { setupAction: 'install', modelId: model.id } }));
      card.append(actions); return card;
    };
    const sections = [];
    const primary = [];
    for (const kind of ['model', 'image']) {
      const options = shown.filter(model => catalogKind(model) === kind && !supplementaryVariant(model));
      const eligible = options.filter(model => (model.canInstall || ['ready', 'installed'].includes(model.status)) && (kind !== 'model' || model.appearance !== 'geometry'));
      const remaining = model => Number.isFinite(model.remainingBytes) ? model.remainingBytes : Number(model.downloadBytes) || Infinity;
      const ordered = eligible.sort((first, second) => (kind === 'model' ? colorRank(first) - colorRank(second) : 0) || Number(second.recommended) - Number(first.recommended) || remaining(first) - remaining(second));
      const choice = ordered[0] || options.find(model => model.recommended && (kind !== 'model' || model.appearance !== 'geometry'));
      if (choice) primary.push(choice);
    }
    if (primary.length) sections.push(node('section', { id: 'setup-recommended', class: 'setup-recommended' }, [
      node('div', { class: 'setup-group-heading' }, [node('h3', { text: '先选一种开始' }), node('p', { text: '有图片，选建模；只有想法，选生图。安装一个就够开始。' })]),
      node('div', { class: 'setup-card-grid recommended-grid' }, primary.map(model => modelCard(model, true)))]));
    const other = shown.filter(model => !primary.includes(model));
    const groupNames = { model: '自带颜色的建模与其他路线', geometry: '纯几何建模 · 白模', image: '更多生图工具', edit: '图片编辑与图生图', multiview: '补充多个视角', texture: '模型上色 · 效果待验收', variants: '更多生图版本' };
    for (const kind of ['model', 'geometry', 'image', 'edit', 'multiview', 'texture', 'variants']) {
      const items = other.filter(model => kind === 'variants' ? supplementaryVariant(model) : !supplementaryVariant(model) && (kind === 'geometry' ? catalogKind(model) === 'model' && model.appearance === 'geometry' : catalogKind(model) === kind && (kind !== 'model' || model.appearance !== 'geometry')));
      if (!items.length) continue;
      const installed = items.filter(model => ['ready', 'installed'].includes(model.status)).length;
      sections.push(node('details', { id: `setup-group-${kind}`, class: 'setup-catalog-group', open: state.openGroups.has(kind) || filter !== 'all' && (filter === kind || filter === 'model' && kind === 'geometry'), dataset: { setupGroup: kind } }, [
        node('summary', {}, [node('span', { text: groupNames[kind] }), node('small', { text: `${items.length} 个选择${installed ? ` · ${installed} 个已安装` : ''}` })]),
        node('p', { class: 'field-hint', text: kind === 'variants' ? '默认生图版本已经够用。需要比较其他档位时再安装；下载与校验完成后，在生图页的高级设置中手动切换。' : '按需安装。不同工具可以比较效果，也会占用更多磁盘空间。' }),
        node('div', { class: 'setup-card-grid' }, items.map(model => modelCard(model)))]));
    }
    $('#setup-models').replaceChildren(...sections);
    if (!shown.length) $('#setup-models').append(node('div', { class: 'empty' }, [node('h3', { text: '暂无该类模型' }), node('p', { text: '换一个能力分类查看，或稍后重新检测。' })]));
  }
  function renderJobs() {
    const jobs = Array.isArray(state.snapshot.jobs) ? state.snapshot.jobs : [];
    const signature = JSON.stringify([jobs, state.busy]); if (signature === state.jobSignature) return; state.jobSignature = signature;
    $('#setup-jobs-section').hidden = !jobs.length;
    $('#setup-jobs').replaceChildren(...jobs.map(job => {
      const model = state.snapshot.models.find(item => item.id === job.modelId);
      const canCreate = creationSupported(model);
      const title = model?.title || job.modelTitle || job.modelId;
      const progress = Number(job.progress); const measured = job.progress !== null && job.progress !== undefined && Number.isFinite(progress) && progress >= 0;
      const percent = Math.max(0, Math.min(100, measured ? progress : 0));
      const row = node('article', { class: 'panel setup-job', dataset: { jobId: job.id } });
      row.append(node('div', { class: 'setup-job-heading' }, [node('div', {}, [node('h3', { text: title }), node('p', { text: `${STATUS[job.status] || job.status} · ${job.stage || '准备中'}` })]), node('span', { text: measured ? `${Math.round(percent)}%` : '进度待更新' })]));
      const track = node('div', { class: 'progress-track', role: 'progressbar', 'aria-label': `${title}安装进度`, 'aria-valuemin': '0', 'aria-valuemax': '100', ...(measured ? { 'aria-valuenow': String(percent) } : {}) }, [node('div', { class: 'progress-fill', style: `width:${percent}%` })]);
      row.append(track);
      if (job.totalBytes > 0) row.append(node('p', { class: 'field-hint', text: `已下载 ${size(job.downloadedBytes)} / ${size(job.totalBytes)}` }));
      if (job.error) row.append(installationError(job.error, job.id, canCreate));
      const logs = Array.isArray(job.log) ? job.log : [];
      if (logs.length) row.append(node('details', { class: 'setup-job-log', open: state.openLogs.has(job.id), dataset: { setupLog: job.id } }, [node('summary', { text: '查看安装日志' }), node('pre', { text: logs.join('\n') })]));
      const actions = node('div', { class: 'setup-job-actions' });
      if (['queued', 'running'].includes(job.status)) actions.append(node('button', { type: 'button', class: 'button small', text: '取消安装', disabled: state.busy, dataset: { setupAction: 'cancel', jobId: job.id } }));
      if (['failed', 'cancelled', 'interrupted'].includes(job.status) && canCreate) actions.append(node('button', { type: 'button', class: 'button small', text: '重试安装', disabled: state.busy, dataset: { setupAction: 'retry', jobId: job.id } }));
      if (job.status === 'completed' && canCreate) actions.append(creationLink(model, '使用这个模型创作 →'));
      row.append(actions); return row;
    }));
  }
  async function load() {
    if (state.loading) return state.loadPromise;
    state.loading = true;
    let finishLoad; state.loadPromise = new Promise(resolve => { finishLoad = resolve; });
    try {
      const result = await request('/api/setup');
      if (!Array.isArray(result.models) || !Array.isArray(result.jobs)) throw new Error('安装状态格式不完整，请重新打开应用后重新检测。');
      state.snapshot = result; renderHardware(); renderModels(); renderJobs();
      $('#setup-credential-status').textContent = result.credentials?.huggingface?.configured ? '已配置本机 Hugging Face 读取授权' : '尚未配置本机 Hugging Face 读取授权';
    } catch (error) { feedback(`无法读取模型与环境：${error.message || '请重新打开应用，然后重新检测。'}`, true); }
    finally {
      state.loading = false; clearTimeout(state.timer);
      const installing = state.snapshot?.jobs?.some(job => ['queued', 'running'].includes(job.status));
      state.timer = setTimeout(load, installing ? 1800 : 10000);
      finishLoad();
    }
  }
  function openInstall(modelId) {
    const model = state.snapshot?.models.find(item => item.id === modelId);
    if (!model || state.busy) return;
    if (!creationSupported(model)) { retiredFeedback(); return; }
    if (!model.canInstall) return;
    state.selected = model.id;
    $('#setup-install-title').textContent = `安装 ${model.title}`;
    const downloadBytes = Number.isFinite(model.remainingBytes) ? model.remainingBytes : model.downloadBytes;
    $('#setup-install-summary').textContent = `${downloadBytes > 0 ? `将下载约 ${size(downloadBytes)} 模型文件` : '模型文件无需额外下载'}，并安装或检查所需运行环境。${model.environmentReserveBytes > 0 ? `另外需预留约 ${size(model.environmentReserveBytes)} 环境空间。` : ''}文件保存在你的电脑里。`;
    $('#setup-install-license').textContent = model.license?.notice || `请先查看${model.license?.label ? ` ${model.license.label} ` : ''}模型使用许可。`;
    const url = externalUrl(model.license?.url); $('#setup-license-link').hidden = !url;
    if (url) $('#setup-license-link').href = url; else $('#setup-license-link').removeAttribute('href');
    let dependencies = $('#setup-install-dependencies');
    if (!dependencies) { dependencies = node('div', { id: 'setup-install-dependencies', class: 'setup-install-dependencies' }); $('#setup-license-link').after(dependencies); }
    const licenses = Array.isArray(model.dependencyLicenses) ? model.dependencyLicenses : [];
    dependencies.hidden = !licenses.length;
    dependencies.replaceChildren(...licenses.map(item => node('p', {}, [node('span', { text: `同时准备：${item.title}。` }), link(`查看${item.label || '依赖许可'} ↗`, item.url)])));
    $('#setup-license-accept').checked = false; $('#setup-install-confirm').disabled = true;
    $('#setup-install-dialog').showModal();
  }
  async function mutate(url, body, message) {
    if (state.busy) return; state.busy = true; renderModels(); renderJobs(); $('#setup-refresh').disabled = true;
    try { await request(url, body); feedback(message); await load(); document.dispatchEvent(new CustomEvent('setup-updated')); }
    catch (error) { feedback(error.message || '操作失败，请查看原因后重试。', true); }
    finally { state.busy = false; renderModels(); renderJobs(); $('#setup-refresh').disabled = false; }
  }
  async function setCredentials(clear = false) {
    if (state.busy) return;
    const input = $('#setup-hf-token');
    const token = clear ? '' : input.value.trim();
    if (!clear && !token) { feedback('请先填写有模型读取权限的 Hugging Face Token。', true); return; }
    state.busy = true;
    for (const button of document.querySelectorAll('.setup-credential-form button')) button.disabled = true;
    try {
      const result = await request('/api/setup/credentials', { provider: 'huggingface', token });
      $('#setup-credential-status').textContent = result.configured ? '已配置本机 Hugging Face 读取授权' : '尚未配置本机 Hugging Face 读取授权';
      feedback(clear ? '本机授权已清除。' : '本机读取授权已保存。请确认模型访问权限后再安装。');
      await load(); document.dispatchEvent(new CustomEvent('setup-updated'));
    } catch { feedback('授权保存失败，请检查本机授权存储和 Token 读取权限后重试。', true); }
    finally { input.value = ''; state.busy = false; for (const button of document.querySelectorAll('.setup-credential-form button')) button.disabled = false; renderModels(); }
  }
  document.addEventListener('click', async event => {
    const shortcut = event.target.closest('[data-setup-model], [data-setup-variant]');
    if (shortcut) {
      const origin = window.location.hash.slice(1);
      if (['home', 'workspace', 'imagegen', 'edit', 'multiview', 'texture'].includes(origin)) {
        let back = $('#setup-return-to-creation');
        if (!back) { back = node('a', { id: 'setup-return-to-creation', class: 'button', text: '返回刚才的创作 →' }); $('#setup-page .page-heading').append(back); }
        back.href = `#${origin}`;
      }
      event.preventDefault(); $('#setup-filter').value = 'all'; state.modelSignature = ''; window.location.hash = 'setup'; await load();
      const variant = shortcut.dataset.setupVariant;
      const variantModel = variant ? state.snapshot?.models.find(model => model.weightVariant === variant && (model.engineId || model.id) === shortcut.dataset.setupEngine) : null;
      const modelId = shortcut.dataset.setupModel || variantModel?.id || (variant === 'Q4_K_M' ? shortcut.dataset.setupEngine : `${shortcut.dataset.setupEngine}-${String(variant).toLowerCase().replaceAll('_', '-')}`);
      const target = state.snapshot?.models.find(model => model.id === modelId);
      if (target && !creationSupported(target)) { retiredFeedback(); return; }
      const card = $(`#setup-models [data-model-id="${CSS.escape(modelId)}"]`);
      if (card) { const group = card.closest('[data-setup-group]'); if (group) { group.open = true; state.openGroups.add(group.dataset.setupGroup); } card.scrollIntoView({ block: 'center', behavior: 'smooth' }); card.classList.add('setup-model-highlight'); setTimeout(() => card.classList.remove('setup-model-highlight'), 3000); }
      else if (variant) feedback('暂未找到这个生图版本的安装项，请重新检测后再试。', true);
      return;
    }
    const button = event.target.closest('[data-setup-action]'); if (!button || button.disabled) return;
    const action = button.dataset.setupAction;
    if (action === 'dismiss-feedback') { $('#setup-feedback').hidden = true; $('#setup-feedback').classList.remove('error'); }
    else if (action === 'use-model') {
      event.preventDefault();
      const model = state.snapshot?.models.find(item => item.id === button.dataset.modelId);
      if (model && !creationSupported(model)) { retiredFeedback(); return; }
      if (!model || !['ready', 'installed'].includes(model.status)) { feedback('这个模型的安装状态已变化，请重新检测后再开始。', true); return; }
      const choice = modelChoice(model);
      document.dispatchEvent(new CustomEvent('factory-model-choice', { detail: choice }));
      window.location.hash = choice.page;
    }
    else if (action === 'save-credentials') await setCredentials();
    else if (action === 'clear-credentials') await setCredentials(true);
    else if (action === 'show-credentials') { event.preventDefault(); const panel = $('.setup-credentials'); panel.open = true; panel.scrollIntoView({ block: 'center', behavior: 'smooth' }); $('#setup-hf-token').focus(); }
    else if (action === 'install') openInstall(button.dataset.modelId);
    else if (action === 'close-install' && !state.busy) { $('#setup-install-dialog').close(); state.selected = null; }
    else if (action === 'confirm-install' && $('#setup-license-accept').checked && state.selected) {
      const modelId = state.selected; $('#setup-install-dialog').close(); state.selected = null;
      if (!creationSupported(state.snapshot?.models.find(model => model.id === modelId))) { retiredFeedback(); return; }
      await mutate('/api/setup/install', { modelId, acceptLicense: true }, '已开始准备模型。下方可查看进度、日志与失败原因。');
      $('#setup-jobs-section').scrollIntoView({ block: 'start', behavior: 'smooth' });
    } else if (action === 'refresh') await mutate('/api/setup/refresh', {}, '检测已更新。');
    else if (['cancel', 'retry'].includes(action)) {
      const job = state.snapshot?.jobs.find(item => item.id === button.dataset.jobId);
      const model = state.snapshot?.models.find(item => item.id === job?.modelId);
      if (action === 'retry' && !creationSupported(model)) { retiredFeedback(); return; }
      await mutate(`/api/setup/jobs/${encodeURIComponent(button.dataset.jobId)}/${action}`, {}, action === 'cancel' ? '已提交取消请求，等待当前步骤结束。' : '已重新加入安装队列。');
    }
  });
  $('#setup-license-accept').addEventListener('change', () => { $('#setup-install-confirm').disabled = !$('#setup-license-accept').checked || state.busy; });
  $('#setup-filter').addEventListener('change', () => { if (state.snapshot) renderModels(); });
  $('#setup-search').addEventListener('input', () => { if (state.snapshot) renderModels(); });
  $('#setup-install-dialog').addEventListener('cancel', () => { state.selected = null; });
  document.addEventListener('toggle', event => {
    const id = event.target.dataset?.setupLog; if (id) { if (event.target.open) state.openLogs.add(id); else state.openLogs.delete(id); }
    const group = event.target.dataset?.setupGroup; if (group) { if (event.target.open) state.openGroups.add(group); else state.openGroups.delete(group); }
  }, true);
  document.addEventListener('factory-page', event => { if (event.detail === 'setup') load(); });
  document.addEventListener('factory-state', event => {
    state.factory = event.detail; updateReadiness();
    if (state.firstState) {
      state.firstState = false;
      const available = [...(state.factory.engines || []), ...(state.factory.imageEngines || []), ...(state.factory.textureEngines || [])].some(engine => ['ready', 'installed'].includes(engine.status));
      const hasHistory = (state.factory.resultTasks || []).length || (state.factory.batches || []).length || (state.factory.imageJobs || []).length;
      if (!available && !hasHistory && !initialHash) window.location.hash = 'setup';
      load();
    }
  });
  document.addEventListener('click', event => { if (event.target.closest('[data-intent="photo"]')) { event.preventDefault(); window.location.hash = 'workspace'; $('#objects-list')?.scrollIntoView({ block: 'center', behavior: 'smooth' }); } });
  const texturePanel = $('#texture-page .texture-layout');
  if (texturePanel) texturePanel.before(node('div', { class: 'stage-explainer' }, [node('strong', { text: '为白色模型添上颜色。' }),
    ' 选择成品模型 → 沿用或补充同一物体的参考图 → 开始上色。',
    node('a', { class: 'inline-link', href: '#setup', text: '准备上色工具' }), node('a', { class: 'inline-link', href: '#help', text: '查看使用指南' })]));
})();
