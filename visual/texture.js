/* Multi-view mesh texturing in the shared queue. v0.8.0 / Unclecow. */
(() => {
  'use strict';
  const page = document.querySelector('#texture-page');
  if (!page) return;
  const $ = selector => page.querySelector(selector);
  const views = ['front', 'left', 'back', 'right'];
  const titles = { front: '正面', left: '左侧', back: '背面', right: '右侧' };
  let snapshot = null, busy = false;
  const manual = new Map(), uploading = new Set();
  const DRAFT_KEY = '2d3d-factory-texture-draft-v1';
  let savedModel = '';
  function local(url) { return typeof url === 'string' && /^\/files\/(inputs|outputs)\//.test(url) && !/[?#]/.test(url) ? url : ''; }
  function element(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'text') node.textContent = value;
      else if (key === 'class') node.className = value;
      else node.setAttribute(key, value);
    }
    node.append(...children); return node;
  }
  page.append(element('div', {class:'page-heading'}, [element('div', {}, [element('span', {class:'eyebrow',text:'MULTI-VIEW TEXTURING'}), element('h1', {text:'模型上色 · 效果待验收'}), element('p', {text:'旧版上色效果未达到预期，目前没有新的满意样例。建议优先使用自带贴图的建模模型；此处可继续试用多图上色。'})])]),
    element('div', {class:'texture-layout'}, [
      element('section', {class:'texture-panel'}, [element('h2', {text:'03 · 模型上色'}),
        element('label', {for:'texture-model',text:'选择未绑骨模型'}), element('select', {id:'texture-model','aria-label':'选择上色模型',class:'sr-only',tabindex:'-1'}),
        element('button', {id:'texture-model-picker',class:'selected-model-button',type:'button','aria-haspopup':'dialog','aria-controls':'model-picker-dialog',text:'看缩略图选择模型'}),
        element('button', {id:'texture-model-preview',type:'button',class:'button small model-selection-preview','data-action':'preview-source-model',hidden:'',text:'预览这个 3D 模型'}),
        element('div', {id:'texture-selected',class:'texture-selected'}),
        element('label', {for:'texture-reference-mode',text:'参考图片'}), element('select', {id:'texture-reference-mode'}, [element('option', {value:'original',text:'沿用模型原来的多张视图'}),element('option', {value:'manual',text:'手动上传正面与辅助视图'})]),
        element('p', {class:'field-hint',text:'至少2张、最多4张，必须包含正面；请使用同一个物体，按实际方向放图。'}),
        element('div', {id:'texture-references',class:'texture-references'}),
        element('p', {id:'texture-reference-note',class:'field-hint'}),
        element('button', {id:'start-texturing',type:'button',class:'button primary',text:'开始上色'}),
        element('p', {id:'texture-feedback',class:'feedback','aria-live':'polite'})]),
      element('aside', {class:'texture-panel texture-summary'}, [element('h2', {text:'工具与结果'}),element('p', {id:'texture-engine-note'}),
        element('p', {text:'每次保留原模型和参考图。最终产物为内嵌图片纹理的 GLB，可预览、下载和导出。'}),
        element('p', {class:'field-hint',text:'已含骨骼或动作的模型不支持上色，请选择原来的静态模型。'}),
        element('a', {href:'#results',class:'button',text:'查看模型成品 →'})]) ]));
  function saveDraft() {
    savedModel = $('#texture-model').value;
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ modelUrl: savedModel,
      mode: $('#texture-reference-mode').value, references: [...manual] })); } catch { /* Optional local persistence. */ }
  }
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
    if (draft && typeof draft === 'object') {
      savedModel = local(draft.modelUrl);
      $('#texture-reference-mode').value = draft.mode === 'manual' ? 'manual' : 'original';
      if (Array.isArray(draft.references)) for (const record of draft.references.slice(0, 4)) {
        if (!Array.isArray(record) || !views.includes(record[0])) continue;
        const [view, image] = record;
        if (image && typeof image.id === 'string' && image.id && local(image.url)) {
          manual.set(view, { id: image.id, name: String(image.name || '参考图'), url: local(image.url) });
        }
      }
    }
  } catch { /* Recover safely when local storage is disabled or damaged. */ }
  function models() {
    const index = new Map();
    for (const task of snapshot?.resultTasks || []) for (const job of task.jobs || []) for (const asset of job.assets || []) {
      if (asset.kind === 'model' && /\.glb$/i.test(asset.name) && local(asset.url)) index.set(asset.url, {...asset, task, job,
        label:`${task.name} · ${job.engineId || job.engine} · ${asset.name}`});
    }
    return index;
  }
  function inherited(model, index, seen = new Set()) {
    if (!model || seen.has(model.url)) return [];
    seen.add(model.url);
    const references = (model.task.inputs || []).filter(item => views.includes(item.view) && local(item.url));
    if (references.length) return references;
    return inherited(index.get(model.job.sourceModel?.url || model.task.sourceModel?.url), index, seen);
  }
  function refresh() {
    const index = models(), choices = [...index.values()].filter(item => !item.modelInfo?.skins && !item.modelInfo?.skinnedVertices && !item.modelInfo?.animationCount && !item.modelInfo?.error);
    const signature = JSON.stringify(choices.map(item => [item.url,item.label]));
    const select = $('#texture-model'), old = select.value || savedModel;
    if (select.dataset.signature !== signature) {
      select.replaceChildren(element('option', {value:'',text:'选择一个模型成品'}), ...choices.map(item=>element('option', {value:item.url,text:item.label})));
      if (choices.some(item=>item.url===old)) select.value=old;
      else if (old) { const missing = element('option', {value:old,text:'原模型暂不可用，请重新选择'}); missing.disabled=true; select.append(missing); select.value=old; }
      select.dataset.signature=signature;
    }
    const model = index.get(select.value), original = inherited(model,index);
    const front = original.find(item=>item.view==='front');
    const card = $('#texture-selected');
    const cardSignature = JSON.stringify([model?.url,front?.url]);
    if (card.dataset.signature !== cardSignature) {
      card.replaceChildren();
      if (model) {
        if (front) card.append(element('img', {src:local(front.url),alt:'所选模型的来源原图',loading:'lazy'}));
        card.append(element('p', {text:`${model.label}${front ? ' · 来源原图' : ' · 未记录原图'}`}));
      }
      card.dataset.signature=cardSignature;
    }
    const mode = $('#texture-reference-mode').value;
    const knownImages = snapshot?.images;
    const missingViews = mode === 'manual' && knownImages && typeof knownImages === 'object'
      ? [...manual].filter(([, image]) => !Object.prototype.hasOwnProperty.call(knownImages, image.id)).map(([view]) => view) : [];
    const referenceSignature = JSON.stringify([mode,busy,original, [...manual], [...uploading], missingViews]);
    const grid = $('#texture-references');
    if (grid.dataset.signature !== referenceSignature) {
      grid.replaceChildren(...views.map(view=>{
        const item = mode==='manual' ? manual.get(view) : original.find(item=>item.view===view);
        const slot = element('div', {class:'texture-reference'});
        slot.append(element('strong', {text:titles[view]}));
        if (missingViews.includes(view)) slot.append(element('span', {class:'error-caption',text:'这张参考图已不可用，请重新上传'}));
        else if (item?.url) slot.append(element('img', {src:local(item.url),alt:`${titles[view]}参考图`,loading:'lazy'}));
        else slot.append(element('span', {class:'texture-image-empty',text:'未选择'}));
        if (mode==='manual') {
          const input = element('input', {type:'file',accept:'image/png,image/jpeg,image/webp','aria-label':`上传${titles[view]}上色参考图`});
          input.disabled=busy || uploading.has(view);
          input.addEventListener('change',()=>upload(view,input.files?.[0]));
          slot.append(input);
          if (item) { const remove = element('button',{type:'button',class:'button small ghost',text:'移除'}); remove.disabled=busy;remove.addEventListener('click',()=>{manual.delete(view);saveDraft();refresh();});slot.append(remove); }
        }
        return slot;
      }));
      grid.dataset.signature=referenceSignature;
    }
    const references = mode==='manual' ? [...manual].map(([view,image])=>({view,...image})) : original;
    const valid = !missingViews.length && references.length>=2 && references.length<=4 && references.some(item=>item.view==='front');
    $('#texture-reference-note').textContent = uploading.size ? '正在上传参考图…' : missingViews.length ? `${missingViews.map(view=>titles[view]).join('、')}参考图已不可用，请重新上传后继续。` : valid ? `已选择 ${references.length} 张参考图。` : '请补充正面及至少一张其他方向的参考图。';
    const engine=(snapshot?.textureEngines || []).find(item=>item.id==='hunyuan-paint');
    const ready=engine && ['installed','ready'].includes(engine.status);
    $('#texture-engine-note').textContent=engine ? `Hunyuan3D 多图上色：${engine.status==='ready'?'已有生成记录，效果待验收':engine.status==='installed'?'已安装，待生成验收':'准备中'}。${engine.notes || ''}` : '正在准备 Hunyuan3D 多图上色工具…';
    $('#start-texturing').disabled=busy || uploading.size>0 || !choices.some(item=>item.url===select.value) || !valid || !ready;
    select.disabled=busy;
    $('#texture-model-picker').disabled=busy;
    $('#texture-reference-mode').disabled=busy;
    document.dispatchEvent(new CustomEvent('factory-model-picker-refresh'));
  }
  async function upload(view,file) {
    if (!file || uploading.has(view) || busy) return;
    if (!file.size || file.size>24*1024*1024) {$('#texture-feedback').textContent='参考图需为非空文件且不超过24MB。';return;}
    uploading.add(view);refresh();
    try {
      const response=await fetch('/api/images',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Factory-Request':'1','X-File-Name':encodeURIComponent(file.name)},body:file});
      const image=await response.json();
      if (!response.ok || !image.id || !local(image.url)) throw new Error(image.error || '参考图上传失败');
      if (snapshot?.images && typeof snapshot.images === 'object') snapshot.images[image.id] = image;
      manual.set(view,image);saveDraft();$('#texture-feedback').textContent=`${titles[view]}参考图已导入。`;
      document.dispatchEvent(new CustomEvent('factory-refresh'));
    } catch(error) {$('#texture-feedback').textContent=error.message || '上传失败，请重新选择。';}
    finally {uploading.delete(view);refresh();}
  }
  async function submit() {
    if ($('#start-texturing').disabled) return;
    const model=models().get($('#texture-model').value);
    if (!model) return;
    const payload={stage:'texture',engineId:'hunyuan-paint',modelUrl:model.url,name:model.label};
    if ($('#texture-reference-mode').value==='manual') payload.images=[...manual].map(([view,image])=>({view,imageId:image.id}));
    busy=true;refresh();$('#texture-feedback').textContent='正在提交到上色队列…';
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),30000);
    try {
      const response=await fetch('/api/model-jobs',{method:'POST',headers:{'Content-Type':'application/json','X-Factory-Request':'1'},body:JSON.stringify(payload),signal:controller.signal});
      const result=await response.json(); if(!response.ok) throw new Error(result.error || '上色任务提交失败');
      $('#texture-feedback').replaceChildren(document.createTextNode('上色任务已加入队列，当前模型和参考图已保留。'), element('a', {href:'#queue',class:'button small',text:'查看任务进度 →'}));
      document.dispatchEvent(new CustomEvent('factory-refresh'));
    } catch(error) {$('#texture-feedback').textContent=error.name==='AbortError'?'提交超时，请先查看队列，避免重复生成。':error.message || '无法连接工厂。';}
    finally {clearTimeout(timer);busy=false;refresh();}
  }
  document.addEventListener('factory-state',event=>{snapshot=event.detail;refresh();});
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action="use-texture-model"]');
    if (!button) return;
    refresh();$('#texture-model').value=button.dataset.modelUrl;saveDraft();refresh();window.location.hash='texture';
  });
  $('#texture-model-picker').addEventListener('click', () => document.dispatchEvent(new CustomEvent('factory-open-model-picker', {detail:{target:'texture'}})));
  $('#texture-model').addEventListener('change',()=>{saveDraft();refresh();});
  $('#texture-reference-mode').addEventListener('change',()=>{saveDraft();refresh();});
  $('#start-texturing').addEventListener('click',submit);
  refresh();
})();
