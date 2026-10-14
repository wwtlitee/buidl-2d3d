/* Shared static model picker and historical GLB playback. v0.9.0 / Unclecow. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  let snapshot = null;
  let pickerTarget = 'texture';
  let pickerOrigin = null;
  const stages = { model: '建模结果', paint: '已上色', rig: '已绑骨', animation: '已有动作' };
  function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function imageUrl(value) {
    return typeof value === 'string' && /^\/files\/(?:inputs|outputs)\//.test(value)
      && /\.(png|jpe?g|webp)(?:[?#]|$)/i.test(value) ? value : '';
  }
  function stageOf(task, job, asset) {
    const explicit = job.pipelineStage || task.pipelineStage || job.kind;
    if (['paint', 'texture', 'texturing'].includes(explicit)) return 'paint';
    if (explicit === 'animation' || asset.modelInfo?.animationCount) return 'animation';
    if (explicit === 'rig' || asset.modelInfo?.skinnedVertices) return 'rig';
    return 'model';
  }
  function option(value, label, disabled = false) {
    const item = document.createElement('option');
    item.value = value; item.textContent = label; item.disabled = disabled;
    return item;
  }
  function assets() {
    const found = new Map();
    const engineTitles = new Map([...(snapshot?.engines || []), ...(snapshot?.rigEngines || []), ...(snapshot?.animationEngines || []), ...(snapshot?.textureEngines || []), ...(snapshot?.historicalEngines || [])]
      .map(engine => [engine.id, engine.title]));
    for (const task of snapshot?.resultTasks || []) {
      for (const job of task.jobs || []) for (const asset of job.assets || []) {
        if (asset.kind === 'model' && /\.glb$/i.test(asset.name) && /^\/files\/(outputs|inputs)\//.test(asset.url || '')) {
          const engineId = job.engineId || job.engine;
          const inputs = [...(Array.isArray(job.inputs) ? job.inputs : []), ...(Array.isArray(task.inputs) ? task.inputs : [])];
          const main = inputs.find(input => ['front', 'main'].includes(input.view) && imageUrl(input.url))
            || inputs.find(input => imageUrl(input.url));
          const thumbnail = imageUrl(asset.thumbnailUrl) || imageUrl(asset.previewUrl);
          found.set(asset.url, { ...asset, title: task.name || '未命名物体', engine: engineTitles.get(engineId) || engineId || '未知工具',
            stage: stageOf(task, job, asset), createdAt: job.createdAt || task.createdAt, runId: job.runId || job.id || '',
            sourceUrl: (job.sourceModel || task.sourceModel)?.url, sourceImage: imageUrl(main?.url) || imageUrl(job.inputUrl),
            thumbnail, thumbnailKind: thumbnail ? '模型缩略图' : '',
            label: `${task.name || '未命名物体'} · ${engineTitles.get(engineId) || engineId || '未知工具'} · ${asset.name}` });
        }
      }
    }
    // Follow the exact source GLB chain so a cow's later rig/action retains its own image.
    function sourceImage(item, visited = new Set()) {
      if (!item || visited.has(item.url)) return '';
      if (item.sourceImage) return item.sourceImage;
      visited.add(item.url);
      return sourceImage(found.get(item.sourceUrl), visited);
    }
    for (const item of found.values()) {
      if (!item.thumbnail) {
        item.thumbnail = sourceImage(item);
        item.thumbnailKind = item.thumbnail ? '来源原图' : '未保存来源图';
      }
    }
    return [...found.values()];
  }
  function thumbnail(item) {
    const cover = element('span', 'model-choice-cover');
    if (item?.thumbnail) {
      const image = element('img');
      image.src = item.thumbnail; image.alt = `${item.thumbnailKind}：${item.title}`;
      image.loading = 'lazy'; image.decoding = 'async';
      image.addEventListener('error', () => {
        cover.replaceChildren(element('span', 'model-choice-placeholder', '来源图不可用'));
      }, { once: true });
      cover.append(image);
    } else cover.append(element('span', 'model-choice-placeholder', item ? '未保存来源图' : '选择一个模型'));
    return cover;
  }
  function choiceCopy(item) {
    const copy = element('span', 'model-choice-copy');
    const row = element('span', 'model-choice-heading');
    const title = element('strong', '', item.title);
    title.title = item.title;
    row.append(title, element('span', 'model-stage-badge', stages[item.stage]));
    const stamp = item.createdAt ? new Date(item.createdAt) : null;
    const date = stamp && !Number.isNaN(stamp.getTime()) ? stamp.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '';
    copy.append(row, element('span', 'model-choice-engine', item.engine),
      element('span', 'model-choice-file', item.name),
      element('span', 'model-choice-source', [item.thumbnailKind, date, item.runId ? `批次 ${item.runId.slice(-12)}` : ''].filter(Boolean).join(' · ')));
    return copy;
  }
  function selectedModel(prefix, all) {
    const item = all.find(asset => asset.url === $(`#${prefix}-model`).value);
    const button = $(`#${prefix}-model-picker`);
    const signature = JSON.stringify(item ? [item.url, item.label, item.stage, item.thumbnail, item.thumbnailKind] : null);
    if (button.dataset.signature !== signature) {
      const copy = item ? choiceCopy(item) : element('span', 'model-choice-copy', '看缩略图选择模型');
      button.replaceChildren(thumbnail(item), copy, element('span', 'model-picker-open-label', item ? '更换 →' : '选择 →'));
      button.dataset.signature = signature;
    }
    const preview = $(`#${prefix}-model-preview`);
    preview.hidden = !item;
    if (item) Object.assign(preview.dataset, { sourceUrl: item.url, sourceName: item.name, sourceBytes: String(item.bytes || 0) });
    else { delete preview.dataset.sourceUrl; delete preview.dataset.sourceName; delete preview.dataset.sourceBytes; }
  }
  function renderPicker(all = assets()) {
    const dialog = $('#model-picker-dialog');
    if (!dialog.open) return;
    const query = $('#model-picker-search').value.trim().toLocaleLowerCase();
    const stage = $('#model-picker-stage').value;
    const available = all.filter(item => !item.modelInfo?.skins && !item.modelInfo?.skinnedVertices && !item.modelInfo?.animationCount && !item.modelInfo?.error);
    const visible = available.filter(item => (!stage || item.stage === stage)
      && (!query || [item.title, item.engine, item.name, item.runId, stages[item.stage]].join(' ').toLocaleLowerCase().includes(query)));
    $('#model-picker-count').textContent = `${visible.length} / ${available.length} 个可选模型`;
    const selected = $(`#${pickerTarget}-model`).value;
    const list = $('#model-picker-list');
    const signature = JSON.stringify([pickerTarget, selected, visible.map(item => [item.url, item.label, item.thumbnail, item.thumbnailKind, item.stage, item.runId])]);
    if (list.dataset.signature === signature) return;
    const cards = visible.map(item => {
      const card = element('article', `model-picker-card${item.url === selected ? ' selected' : ''}`);
      const choose = element('button', 'model-choice-button'); choose.type = 'button';
      choose.setAttribute('aria-pressed', String(item.url === selected));
      choose.setAttribute('aria-label', `选择 ${item.title}，${stages[item.stage]}，${item.engine}，${item.name}，批次 ${item.runId || '未记录'}`);
      choose.append(thumbnail(item), choiceCopy(item), element('span', 'model-picker-choose-label', item.url === selected ? '当前选择' : '选择这个模型'));
      choose.addEventListener('click', () => {
        $(`#${pickerTarget}-model`).value = item.url;
        $(`#${pickerTarget}-model`).dispatchEvent(new Event('change', { bubbles: true }));
        closePicker(); refresh();
      });
      const preview = element('button', 'button small ghost model-picker-preview', '预览这个 3D 模型'); preview.type = 'button';
      Object.assign(preview.dataset, { action: 'preview-source-model', sourceUrl: item.url, sourceName: item.name, sourceBytes: String(item.bytes || 0) });
      preview.addEventListener('click', closePicker);
      card.append(choose, preview); return card;
    });
    if (!cards.length) {
      const empty = element('div', 'empty model-picker-empty');
      empty.append(element('h3', '', available.length ? '没有匹配的模型' : '先生成一个 3D 模型'),
        element('p', '', available.length ? '试试其他名称或模型阶段。' : '完成后，这里会显示可辨认的来源图和模型阶段。'));
      cards.push(empty);
    }
    list.replaceChildren(...cards); list.dataset.signature = signature;
  }
  function openPicker(prefix) {
    if (prefix !== 'texture') return;
    pickerTarget = prefix; pickerOrigin = $(`#${prefix}-model-picker`);
    $('#model-picker-title').textContent = '选择静态模型 · 上色';
    $('#model-picker-search').value = ''; $('#model-picker-stage').value = '';
    $('#model-picker-dialog').showModal(); renderPicker(); $('#model-picker-search').focus();
  }
  function closePicker() {
    $('#model-picker-dialog').close(); pickerOrigin?.focus();
  }
  function renderStageProgress() {
    const jobs = snapshot?.jobs || (snapshot?.batches || []).flatMap(batch => batch.jobs || []);
    const statusLabels = { queued: '排队中', running: '处理中', 'generated-unreviewed': '已生成，待检查效果', failed: '生成失败', interrupted: '已中断', cancelled: '已取消' };
    for (const [prefix, stage, feedbackId] of [['texture', 'texture', 'texture-feedback']]) {
      const source = $(`#${prefix}-model`)?.value;
      const feedback = $(`#${feedbackId}`);
      if (!feedback) continue;
      let panel = $(`#${prefix}-stage-progress`);
      if (!panel) {
        panel = element('section', 'stage-progress panel'); panel.id = `${prefix}-stage-progress`;
        panel.setAttribute('aria-label', '当前模型的上色进度');
        feedback.after(panel);
      }
      const job = source && jobs.filter(item => (item.pipelineStage || item.kind) === stage && item.sourceModel?.url === source)
        .sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')))[0];
      panel.hidden = !job;
      if (!job) continue;
      const signature = JSON.stringify([job.id, job.engineId, job.engine, job.motion, job.request?.motion, job.status, job.phase, job.progress, job.error, job.assets]);
      if (panel.dataset.signature === signature) continue;
      panel.dataset.signature = signature;
      const heading = element('strong', '', `上色 · ${statusLabels[job.status] || job.status}`);
      const actions = element('div', 'task-tool-actions');
      const queue = element('a', 'button small ghost', '查看全部任务'); queue.href = '#queue'; actions.append(queue);
      const children = [heading];
      const engineId = job.engineId || job.engine;
      const engine = (snapshot?.textureEngines || []).find(item => item.id === engineId);
      children.push(element('p', 'field-hint', `最近一次任务 · ${engine?.title || engineId || '未记录工具'}。对应这次任务的实际结果。`));
      if (job.phase) children.push(element('p', 'field-hint', job.phase));
      if (job.status === 'running' && typeof job.progress === 'number' && Number.isFinite(job.progress)) {
        const progress = element('progress'); progress.max = 100; progress.value = Math.max(0, Math.min(100, job.progress));
        progress.setAttribute('aria-label', '当前阶段进度'); children.push(progress);
      }
      if (job.error) children.push(element('p', 'error-caption', `本次未完成。可在任务进度中查看原因并重试：${job.error}`));
      const asset = (job.assets || []).find(item => item.kind === 'model' && /^\/files\/(inputs|outputs)\//.test(item.url || ''));
      if (asset && job.status === 'generated-unreviewed') {
        const preview = element('button', 'button small', '预览结果'); preview.type = 'button';
        Object.assign(preview.dataset, { action: 'preview-source-model', sourceUrl: asset.url, sourceName: asset.name, sourceBytes: String(asset.bytes || 0) });
        const download = element('a', 'button small', '下载模型'); download.href = asset.url + '?download=1'; download.setAttribute('download', asset.name || 'model.glb');
        actions.append(preview, download);
      }
      panel.replaceChildren(...children, actions);
    }
  }
  function refresh() {
    const all = assets();
    if ($('#texture-model-picker')) selectedModel('texture', all);
    renderPicker(all); renderStageProgress();
  }
  document.addEventListener('factory-state', event => { snapshot = event.detail; refresh(); });
  document.addEventListener('factory-open-model-picker', event => {
    if (event.detail?.target === 'texture' && $('#texture-model-picker')) openPicker('texture');
  });
  document.addEventListener('factory-model-picker-refresh', refresh);
  $('#close-model-picker').addEventListener('click', closePicker);
  $('#model-picker-search').addEventListener('input', () => renderPicker());
  $('#model-picker-stage').addEventListener('change', () => renderPicker());
  $('#model-picker-dialog').addEventListener('click', event => {
    if (event.target !== $('#model-picker-dialog')) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closePicker();
  });

  // Observe only this application's preview element; animation is independent of camera rotation.
  const controls = document.createElement('span'); controls.className = 'animation-controls'; controls.hidden = true;
  const select = document.createElement('select'); select.setAttribute('aria-label', '预览动作');
  const play = document.createElement('button'); play.type = 'button'; play.className = 'button small'; play.textContent = '播放动作';
  controls.append(select, play); $('#model-controls').append(controls);
  let currentViewer = null;
  function bindViewer() {
    const viewer = $('#preview-content model-viewer');
    if (viewer === currentViewer) return;
    currentViewer = viewer; controls.hidden = true;
    if (!viewer) return;
    viewer.addEventListener('load', () => {
      if (viewer !== currentViewer) return;
      const names = viewer.availableAnimations || [];
      const humanoidClip = String(viewer.src || '').includes('/mia-motion/');
      select.replaceChildren(...names.map(name => option(name, humanoidClip ? '标准原地奔跑' : name)));
      controls.hidden = !names.length; play.textContent = '播放动作';
      if (names.length) viewer.animationName = names[0];
    });
  }
  new MutationObserver(bindViewer).observe($('#preview-content'), { childList: true });
  select.addEventListener('change', () => { if (currentViewer) { currentViewer.pause(); currentViewer.animationName = select.value; currentViewer.currentTime = 0; play.textContent = '播放动作'; } });
  play.addEventListener('click', () => {
    if (!currentViewer) return;
    if (currentViewer.paused) { currentViewer.play(); play.textContent = '暂停动作'; }
    else { currentViewer.pause(); play.textContent = '播放动作'; }
  });
})();
