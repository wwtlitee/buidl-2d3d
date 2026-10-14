"""Consumer workflow acceptance against a temporary real FactoryServer. v0.9.0.

Launches an isolated, headless Chromium context. Every image, model byte,
download, queue, and installation receipt belongs to TemporaryDirectory.
The production service, GPU worker, user browser, and real models are unused.
"""
from __future__ import annotations

import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from factory_queue import Factory
from factory_web import FactoryServer, Handler

PRODUCT_VERSION = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))["version"]
EVIDENCE = Path(os.environ.get("FACTORY_UI_CAPTURE_DIR", ROOT / "output/scope-v090/consumer"))
ARTIFACTS = EVIDENCE / "screenshots"
REPORT = EVIDENCE / "consumer-ui-acceptance.json"
BYTES = b"Fixture model bytes, never an inference model.\n" * 8000


class ModelSource(BaseHTTPRequestHandler):
    """Small pinned download source, loopback only, with HTTP range support."""

    def log_message(self, *_):
        pass

    def do_GET(self):
        start = 0
        requested = self.headers.get("Range")
        if requested:
            start = int(requested.removeprefix("bytes=").removesuffix("-"))
        data = BYTES[start:]
        self.send_response(206 if requested else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{len(BYTES)-1}/{len(BYTES)}")
        self.end_headers()
        try:
            for index in range(0, len(data), 16384):
                self.wfile.write(data[index:index + 16384])
                self.wfile.flush()
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


NODE_ACCEPTANCE = r"""
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
const path = require('node:path');
const { chromium } = require(process.argv[2]);
const origin = process.argv[3];
const image = process.argv[4];
const artifacts = process.argv[5];
const checks = [];
const errors = [];
const requests = [];
const externalRequests = [];
let browser;
async function check(name, callback) {
  await callback(); checks.push({name, passed:true});
}
(async () => {
  try {
    browser = await chromium.launch({headless:true});
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    await context.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(origin + '/') || url.startsWith('blob:') || url.startsWith('data:')) return route.continue();
      externalRequests.push(url); return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.method()==='POST') requests.push({url:new URL(request.url()).pathname,
        data:request.headers()['content-type']?.includes('application/json')?request.postDataJSON():{bytes:request.postDataBuffer()?.length||0}});
    });
    await page.goto(origin + '/');
    await page.waitForSelector('#connection-status.connected');
    await check('new-brand', async () => {
      assert.match(await page.title(), /build something/i);
      assert.match(await page.locator('.brand').innerText(), /造点啥/);
    });
    await check('zero-model-first-run-guidance', async () => {
      await page.waitForSelector('#setup-page:not([hidden])');
      assert.match(await page.locator('#setup-models').innerText(), /隔离测试建模工具/);
      assert.equal(requests.length, 0, 'First opening must not install or enqueue automatically');
      await page.screenshot({path:path.join(artifacts,'01-empty-models-desktop.png'),fullPage:true});
    });
    await check('hardware-detection-and-refresh', async () => {
      assert.match(await page.locator('#setup-hardware').innerText(), /测试显卡|Fixture GPU/);
      await page.locator('#setup-refresh').click();
      await page.waitForSelector('[data-setup-action="install"]');
    });
    await check('explicit-model-license-review', async () => {
      await page.locator('[data-setup-action="install"][data-model-id="triposr"]').click();
      assert.equal(await page.locator('#setup-install-confirm').isDisabled(), true);
      assert.equal(requests.filter(item=>item.url==='/api/setup/install').length, 0, 'Reviewing the dialog must not start a download');
      await page.locator('#setup-license-accept').check();
      assert.equal(await page.locator('#setup-install-confirm').isDisabled(), false);
      await page.screenshot({path:path.join(artifacts,'02-license-review.png'),fullPage:true});
      await page.locator('#setup-install-confirm').click();
      await page.waitForFunction(() => document.querySelector('#setup-jobs').textContent.length>0);
      const installation = requests.find(item=>item.url==='/api/setup/install');
      assert.deepEqual(installation.data,{modelId:'triposr',acceptLicense:true});
    });
    await check('real-fixture-download-completes', async () => {
      await page.waitForFunction(async () => {
        const setup = await (await fetch('/api/setup')).json();
        return setup.jobs.some(job=>job.status==='completed');
      },undefined,{timeout:25000});
      await page.locator('#setup-refresh').click();
      await page.waitForFunction(() => /已安装|已完成|安装完成|可使用/.test(document.querySelector('#setup-jobs').textContent));
      await page.screenshot({path:path.join(artifacts,'03-installed-fixture.png'),fullPage:true});
    });
    await check('workspace-upload-and-draft-restoration', async () => {
      await navigate(page, 'workspace');
      await page.waitForSelector('#workspace-page:not([hidden])');
      const chooser = page.waitForEvent('filechooser');
      await page.locator('#objects-list [data-action="pick-slot"][data-view="main"]').first().click();
      await (await chooser).setFiles(image);
      await page.waitForSelector('#objects-list img');
      await page.locator('#objects-list [data-object-name]').first().fill('隔离机械小牛');
      await page.reload();
      await page.waitForSelector('#connection-status.connected');
      await page.waitForSelector('#objects-list img');
      assert.equal(await page.locator('#objects-list [data-object-name]').first().inputValue(),'隔离机械小牛');
    });
    await check('local-glb-import-retains-origin-and-source-options', async () => {
      const imported=[];
      for(const file of [process.argv[6],process.argv[7]]) {
        const response=page.waitForResponse(result=>new URL(result.url()).pathname==='/api/models'&&result.request().method()==='POST');
        await page.locator('#import-model-file').setInputFiles(file);
        const done=await response;
        assert.equal(done.status(),201);
        await page.waitForSelector('#results-page:not([hidden])');
        const name=path.basename(file,'.glb');
        await page.waitForFunction(name=>document.querySelector('#results-list').textContent.includes(name),name);
        const snapshot=await (await page.request.get(origin+'/api/state')).json();
        const task=snapshot.resultTasks.find(task=>task.name===name);
        assert.ok(task,'Imported model must exist in the real persisted state');
        const asset=task.jobs.flatMap(job=>job.assets).find(asset=>asset.kind==='model');
        assert.ok(asset?.url);imported.push({...asset,taskId:task.id});
      }
      await page.waitForFunction(()=>document.querySelectorAll('#results-list .result-task-card').length===2);
      assert.match(await page.locator('#results-list').innerText(),/本机导入/);
      assert.ok(imported[0].modelInfo?.animationCount>0,'The imported fixture carries real animation channels');
      assert.equal(await page.locator('#results-list [data-action="use-model"], [data-page="animation"], [data-page="video"]').count(),0,'Imported models expose no retired creation actions');
      const historicalDownload=page.locator(`[data-result-task-id="${imported[0].taskId}"] .asset-card-actions a[download]`);
      const download=await page.request.get(new URL(await historicalDownload.getAttribute('href'),origin).href);
      assert.equal(download.status(),200);assert.ok((await download.body()).length>0,'Animated GLB remains downloadable');
      await navigate(page, 'texture');
      await page.waitForFunction(url=>[...document.querySelector('#texture-model').options].some(option=>option.value===url),imported[1].url);
      assert.equal(await page.locator('#texture-model option').count(),2,'Skinned source must remain protected from mesh texturing');
      await navigate(page, 'results');
      await page.locator(`[data-result-task-id="${imported[0].taskId}"] [data-action="preview-result"]`).first().click();
      await page.waitForFunction(()=>document.querySelector('#preview-content model-viewer')?.loaded===true,undefined,{timeout:20000});
      await page.waitForSelector('.animation-controls:not([hidden])');
      assert.ok(await page.locator('.animation-controls select option').count()>0,'Historical GLB animation names remain readable');
      await page.locator('.animation-controls button').click();
      await page.waitForFunction(()=>document.querySelector('#preview-content model-viewer')?.paused===false);
      await page.locator('.animation-controls button').click();
      await page.waitForFunction(()=>document.querySelector('#preview-content model-viewer')?.paused===true);
      await page.screenshot({path:path.join(artifacts,'06-imported-model-preview.png'),fullPage:true});
      await page.locator('[data-action="close-preview"]').click();
    });
    await check('existing-navigation-and-unavailable-generation', async () => {
      for (const id of ['imagegen','edit','multiview','texture','queue','results','help','workspace']) {
        await navigate(page, id);
        await page.waitForSelector('#'+id+'-page:not([hidden])');
      }
      await navigate(page, 'imagegen');
      assert.equal(await page.locator('#start-imagegen').isDisabled(),true);
      assert.equal(await page.locator('#image-advanced').getAttribute('open'),null);
    });
    await check('mobile-navigation-without-horizontal-overflow', async () => {
      await page.setViewportSize({width:390,height:844});
      for (const id of ['setup','workspace','imagegen','edit','multiview','texture','queue','results','help']) {
        await navigate(page, id);
        await page.waitForSelector('#'+id+'-page:not([hidden])');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true, id+' page overflows a narrow viewport');
      }
      await navigate(page, 'setup');
      await page.screenshot({path:path.join(artifacts,'04-mobile-setup.png'),fullPage:true});
    });
    assert.deepEqual(errors,[], 'Application JS modules must not produce page errors');
    assert.deepEqual(externalRequests,[], 'The interface must not contact remote services automatically');
    assert.equal(requests.some(item=>/^\/api\/(batches|image-jobs|model-jobs)$/.test(item.url)),false,'Acceptance must not run a production engine');
    console.log(JSON.stringify({status:'passed',checks,pageErrors:errors,requests,externalRequests}));
  } catch(error) {
    console.log(JSON.stringify({status:'failed',checks,error:error.stack,pageErrors:errors,requests,externalRequests}));
    process.exitCode=1;
  } finally { if(browser)await browser.close(); }
})();
"""

NODE_NO_GPU = r"""
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
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require(process.argv[2]);
const origin=process.argv[3];
let browser;
(async()=>{
  const errors=[],installations=[],remote=[];
  try{
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}});
    await context.route('**/*',route=>{
      const url=route.request().url();
      if(url.startsWith(origin+'/')||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();
      remote.push(url);return route.abort();
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/api/setup/install'))installations.push(request.url());});
    await page.goto(origin+'/');
    await page.waitForSelector('#connection-status.connected');
    await page.waitForSelector('#setup-page:not([hidden])');
    assert.match(await page.locator('#setup-models').innerText(),/NVIDIA|CUDA/,'Unsupported hardware needs an explicit remedy');
    assert.equal(await page.locator('[data-setup-action="install"][data-model-id="triposr"]').isDisabled(),true);
    assert.match(await page.locator('#setup-requirements').innerText(),/NVIDIA.*驱动|驱动.*NVIDIA/);
    assert.equal((await page.locator('#machine-info').innerText()).includes('RTX 3060'),false,'Missing GPUs must never claim a hardcoded GPU');
    await page.screenshot({path:path.join(process.argv[5],'05-no-gpu-guidance.png'),fullPage:true});
    await navigate(page, 'workspace');
    assert.equal(await page.locator('#start-production').isDisabled(),true);
    await navigate(page, 'imagegen');
    assert.equal(await page.locator('#start-imagegen').isDisabled(),true);
    assert.deepEqual(installations,[]);assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
    console.log(JSON.stringify({status:'passed',checks:['no-gpu-guidance','disabled-installation','driver-remedy','honest-machine-status','disabled-generators'],pageErrors:errors,remoteRequests:remote,installations}));
  }catch(error){console.log(JSON.stringify({status:'failed',error:error.stack,pageErrors:errors}));process.exitCode=1;}
  finally{if(browser)await browser.close();}
})();
"""

NODE_SHIPPED_CATALOG = r"""
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
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require(process.argv[2]);
const origin=process.argv[3];
let browser;
(async()=>{
  const errors=[],installations=[],remote=[];
  try{
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}});
    await context.route('**/*',route=>{
      const url=route.request().url();
      if(url.startsWith(origin+'/')||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();
      remote.push(url);return route.abort();
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/api/setup/install'))installations.push(request.url());});
    await page.goto(origin+'/');
    await page.waitForSelector('#connection-status.connected');
    await page.waitForSelector('#setup-page:not([hidden])');
    const detected=await (await page.request.get(origin+'/api/setup')).json();
    assert.ok(detected.models.length>=18,'Retained shipped models must be discoverable on a clean computer');
    assert.ok(detected.models.every(model=>!['video','rig','animation'].includes(model.kind)),'The public installation catalog excludes retired capabilities');
    assert.equal(await page.locator('#setup-filter option[value="video"], #setup-filter option[value="rig"], #setup-filter option[value="animation"]').count(),0);
    await page.waitForSelector('#setup-recommended .setup-model-card');
    assert.equal(await page.locator('#setup-recommended .setup-model-card').count(),2,'Only two primary choices should dominate first use');
    const groups=page.locator('#setup-models details.setup-model-group');
    for(const group of await groups.all()) assert.equal(await group.getAttribute('open'),null);
    await page.screenshot({path:path.join(process.argv[5],'07-full-catalog-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(process.argv[5],'08-full-catalog-mobile.png'),fullPage:true});
    for(const group of await groups.all()){
      await group.locator(':scope > summary').click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'Expanded catalog groups must fit mobile');
      await group.locator(':scope > summary').click();
    }
    assert.deepEqual(installations,[]);assert.deepEqual(errors,[]);assert.deepEqual(remote,[]);
    console.log(JSON.stringify({status:'passed',checks:['retained-shipped-models-detected','retired-installations-absent','two-primary-recommendations','advanced-groups-collapsed','expanded-mobile-groups-fit','no-automatic-downloads'],modelCount:detected.models.length,pageErrors:errors,remoteRequests:remote,installations}));
  }catch(error){console.log(JSON.stringify({status:'failed',error:error.stack,pageErrors:errors}));process.exitCode=1;}
  finally{if(browser)await browser.close();}
})();
"""


class ConsumerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-something-consumer-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "visual", self.root / "visual")
        self.visual_hashes={path.relative_to(self.root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (self.root/"visual").iterdir() if path.is_file()}
        (self.root / "VERSION.json").write_text(json.dumps({"product": "build something / 造点啥", "version": PRODUCT_VERSION}), encoding="utf-8")
        self.engine = self.root / "engines/triposr"
        self.engine.mkdir(parents=True)
        (self.engine / "engine.json").write_text(json.dumps({"id":"triposr", "title":"隔离测试建模工具", "kind":"model", "status":"missing", "version":"0.5.0", "entrypoint":"run.ps1", "capabilities":{"multiView":False}}), encoding="utf-8")
        (self.engine / "run.ps1").write_text("throw 'No real model or GPU worker in consumer acceptance'\n",encoding="utf-8")
        self.image = self.root / "fixture.png"
        Image.new("RGB", (40, 40), (244, 144, 50)).save(self.image)
        fixture_spec=importlib.util.spec_from_file_location("consumer_glb_fixture",ROOT/"tests/check-animation-pipeline.py")
        fixture=importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        self.skinned_model=self.root/"fixture-skinned.glb"
        self.geometry_model=self.root/"fixture-geometry.glb"
        fixture.fixture_glb(self.skinned_model,skinned=True)
        fixture.fixture_glb(self.geometry_model,skinned=False)
        # A tiny existing animated asset tests playback compatibility; no job is created.
        from factory_glb import animate_glb
        animated_model = self.root / "fixture-animated.glb"
        animate_glb(self.skinned_model, animated_model, "joint-test")
        self.skinned_model = animated_model
        self.source = ThreadingHTTPServer(("127.0.0.1", 0), ModelSource)
        self.source_thread = threading.Thread(target=self.source.serve_forever, kwargs={"poll_interval":0.01}, daemon=True)
        self.source_thread.start()
        self.addCleanup(self.close_source)
        self.factory = Factory(self.root, start_worker=False)
        self.addCleanup(self.factory.close)
        self.setup = None
        setup_path = ROOT / "scripts/factory_setup.py"
        if setup_path.is_file():
            from factory_setup import FactorySetup
            catalog = {"version":"0.5.0", "models":[{"id":"triposr", "title":"隔离测试建模工具", "kind":"model", "description":"安装临时小文件，验收完整本地流程", "recommended":True,
                "files":[{"path":"engines/triposr/models/consumer-fixture.bin", "size":len(BYTES), "sha256":hashlib.sha256(BYTES).hexdigest(), "url":f"http://127.0.0.1:{self.source.server_port}/model.bin"}], "required":[{"path":"engines/triposr/models/consumer-fixture.bin", "size":len(BYTES)}], "license":{"label":"Fixture MIT", "url":"https://example.org/license", "requiresAcceptance":True,"notice":"仅隔离验收文件"}, "minimumVramMiB":0,"minimumRamGiB":0}]}
            hardware = {"platform":"Windows", "architecture":"AMD64", "gpuAvailable":True,"gpus":[{"name":"Fixture GPU / 测试显卡", "memoryMiB":12288}], "ramGiB":32,"freeDiskBytes":10000000000,"root":str(self.root),"requirements":[]}
            self.setup = FactorySetup(self.root,catalog=catalog,hardware=hardware,start_worker=True)
            self.factory.setup = self.setup
            self.addCleanup(self.setup.close)
        self.log_patch = patch.object(Handler,"log_message", lambda *_:None)
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.server = FactoryServer(("127.0.0.1",0),self.factory)
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_source(self):
        self.source.shutdown(); self.source.server_close(); self.source_thread.join(timeout=5)

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)

    def test_no_gpu_first_run_explains_the_limit_without_installing(self):
        self.assertIsNotNone(self.setup,"First-run setup manager is required")
        self.setup.hardware_override.update(gpuAvailable=False,gpus=[],requirements=[{"id":"gpu","label":"NVIDIA 显卡与驱动","status":"missing","detail":"未检测到 NVIDIA 显卡","remedy":"安装 NVIDIA 官方显卡驱动后重新检测"}])
        self.setup.catalog["models"][0]["minimumVramMiB"]=1024
        node=shutil.which("node")
        self.assertIsNotNone(node)
        bundled=Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"
        playwright=os.environ.get("FACTORY_PLAYWRIGHT_MODULE") or (str(bundled) if bundled.is_dir() else "playwright")
        script=self.root/"no-gpu.cjs"
        script.write_text(NODE_NO_GPU,encoding="utf-8")
        ARTIFACTS.mkdir(parents=True,exist_ok=True)
        result=subprocess.run([node,str(script),playwright,f"http://127.0.0.1:{self.server.server_port}",str(self.image),str(ARTIFACTS)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=60)
        lines=[line for line in result.stdout.splitlines() if line.startswith("{")]
        report=json.loads(lines[-1]) if lines else {"status":"failed","error":result.stderr or result.stdout}
        report.update(version=PRODUCT_VERSION,mode="isolated-headless-no-gpu",artifacts=str(ARTIFACTS),serviceBuild=self.factory.service_build,visualSha256=self.visual_hashes,realModelsUsed=False,realGpuUsed=False,userBrowserUsed=False,realRemoteDownloads=False)
        REPORT.parent.mkdir(parents=True,exist_ok=True)
        REPORT.with_name("consumer-ui-no-gpu-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        self.assertEqual(result.returncode,0,json.dumps(report,ensure_ascii=False,indent=2))
        self.assertEqual(self.setup.snapshot()["jobs"],[])

    def test_shipped_catalog_is_readable_on_a_completely_clean_computer(self):
        from factory_setup import FactorySetup
        self.setup.close()
        hardware={"platform":"Windows","architecture":"AMD64","gpuAvailable":True,"gpus":[{"name":"Fixture GPU","memoryMiB":12288}],"ramGiB":32,"freeDiskBytes":200000000000,"root":str(self.root),"requirements":[]}
        self.setup=FactorySetup(self.root,hardware=hardware,start_worker=False)
        self.factory.setup=self.setup
        self.addCleanup(self.setup.close)
        node=shutil.which("node")
        bundled=Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"
        playwright=os.environ.get("FACTORY_PLAYWRIGHT_MODULE") or (str(bundled) if bundled.is_dir() else "playwright")
        script=self.root/"full-catalog.cjs"
        script.write_text(NODE_SHIPPED_CATALOG,encoding="utf-8")
        ARTIFACTS.mkdir(parents=True,exist_ok=True)
        result=subprocess.run([node,str(script),playwright,f"http://127.0.0.1:{self.server.server_port}",str(self.image),str(ARTIFACTS)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=65)
        lines=[line for line in result.stdout.splitlines() if line.startswith("{")]
        report=json.loads(lines[-1]) if lines else {"status":"failed","error":result.stderr or result.stdout}
        report.update(version=PRODUCT_VERSION,mode="isolated-headless-full-shipped-catalog",artifacts=str(ARTIFACTS),serviceBuild=self.factory.service_build,visualSha256=self.visual_hashes,realModelsUsed=False,realGpuUsed=False,userBrowserUsed=False,realRemoteDownloads=False)
        REPORT.parent.mkdir(parents=True,exist_ok=True)
        REPORT.with_name("consumer-ui-full-catalog-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        self.assertEqual(result.returncode,0,json.dumps(report,ensure_ascii=False,indent=2))
        self.assertEqual(self.setup.snapshot()["jobs"],[])

    def test_zero_model_to_installed_and_existing_consumer_workflows(self):
        connection = http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        try:
            connection.request("GET","/api/setup")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status,200,"Fresh local users need a working setup detection API: " + body)
        finally:
            connection.close()
        node = shutil.which("node")
        self.assertIsNotNone(node,"Node is required for the isolated Playwright acceptance")
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"
        playwright = os.environ.get("FACTORY_PLAYWRIGHT_MODULE") or (str(bundled) if bundled.is_dir() else "playwright")
        script = self.root / "consumer.cjs"
        script.write_text(NODE_ACCEPTANCE,encoding="utf-8")
        ARTIFACTS.mkdir(parents=True,exist_ok=True)
        result = subprocess.run([node,str(script),playwright,f"http://127.0.0.1:{self.server.server_port}",str(self.image),str(ARTIFACTS),str(self.skinned_model),str(self.geometry_model)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=110)
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        report = json.loads(lines[-1]) if lines else {"status":"failed","error":result.stderr or result.stdout}
        report.update(version=PRODUCT_VERSION,mode="isolated-headless-real-api-fixture-download",artifacts=str(ARTIFACTS),serviceBuild=self.factory.service_build,visualSha256=self.visual_hashes,realModelsUsed=False,realGpuUsed=False,userBrowserUsed=False,realRemoteDownloads=False)
        REPORT.parent.mkdir(parents=True,exist_ok=True)
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        self.assertEqual(result.returncode,0,json.dumps(report,ensure_ascii=False,indent=2))
        downloaded = self.engine / "models/consumer-fixture.bin"
        self.assertTrue(downloaded.is_file(),"Completed UI must correspond to a committed model file")
        self.assertEqual(hashlib.sha256(downloaded.read_bytes()).hexdigest(),hashlib.sha256(BYTES).hexdigest())
        imported=list((self.root/"outputs/imported-model").glob("*/model.glb"))
        self.assertEqual({hashlib.sha256(path.read_bytes()).hexdigest() for path in imported},
                         {hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.skinned_model,self.geometry_model)})
        for path in imported:
            receipt=json.loads((path.parent/"receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["origin"],"local-import")
            self.assertFalse(receipt["gpuInferenceExecuted"])
        self.assertIsNone(self.factory.worker,"Acceptance must never start an inference worker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
