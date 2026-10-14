"""Independent installation failure/cancellation regressions. v0.5.0 / Unclecow.

Uses tiny loopback downloads in temporary roots. No real install, model, GPU,
production queue, browser, or user process is touched.
"""
from __future__ import annotations

import hashlib
import http.client
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from factory_setup import FactorySetup, safe_path

GOOD_BYTES = b"Correct isolated model fixture bytes.\n" * 3000


class Source(BaseHTTPRequestHandler):
    content = GOOD_BYTES
    requests = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        offset = int(self.headers.get("Range", "bytes=0-").split("=")[-1].split("-")[0])
        type(self).requests.append(offset)
        body = type(self).content[offset:]
        self.send_response(206 if offset else 200)
        self.send_header("Content-Length", str(len(body)))
        if offset:
            self.send_header("Content-Range", f"bytes {offset}-{len(type(self).content)-1}/{len(type(self).content)}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class SetupBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-something-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "VERSION.json").write_text('{"version":"0.5.0"}',encoding="utf-8")
        Source.content = GOOD_BYTES
        Source.requests = []
        self.source = ThreadingHTTPServer(("127.0.0.1",0),Source)
        self.source_thread = threading.Thread(target=self.source.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
        self.source_thread.start()
        self.addCleanup(self.close_source)
        self.model = {"id":"boundary-fixture","title":"Isolated boundary fixture","kind":"model",
            "license":{"requiresAcceptance":True},"minimumVramMiB":0,"minimumRamGiB":0,
            "files":[{"path":"engines/boundary-fixture/model.bin","size":len(GOOD_BYTES),"sha256":hashlib.sha256(GOOD_BYTES).hexdigest(),"url":f"http://127.0.0.1:{self.source.server_port}/fixture"}],
            "required":[{"path":"engines/boundary-fixture/model.bin","size":len(GOOD_BYTES)}]}
        self.hardware = {"platform":"Windows","architecture":"AMD64","gpuAvailable":True,"gpus":[{"name":"Fixture GPU","memoryMiB":12288}],"ramGiB":32,"freeDiskBytes":10_000_000_000,"requirements":[]}
        self.manager = FactorySetup(self.root,catalog={"version":"0.5.0","models":[self.model]},hardware=self.hardware,start_worker=False)
        self.addCleanup(self.manager.close)

    def close_source(self):
        self.source.shutdown()
        self.source.server_close()
        self.source_thread.join(timeout=5)

    def start_worker(self):
        self.manager.worker = threading.Thread(target=self.manager._work,daemon=True)
        self.manager.worker.start()

    def install(self):
        return self.manager.install({"modelId":self.model["id"],"acceptLicense":True})

    def wait_job(self, job_id):
        deadline = time.monotonic()+6
        while time.monotonic()<deadline:
            with self.manager.lock:
                job = dict(next(item for item in self.manager.jobs if item["id"]==job_id))
            if job["status"] in {"completed","failed","cancelled","interrupted"}:
                return job
            time.sleep(0.01)
        self.fail("Tiny fixture job did not reach a terminal state")

    @contextmanager
    def local_factory_http(self):
        from factory_queue import Factory
        from factory_web import FactoryServer, Handler
        factory=Factory(self.root,start_worker=False)
        factory.setup.close()
        factory.setup=self.manager
        server=FactoryServer(("127.0.0.1",0),factory)
        thread=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
        with patch.object(Handler,"log_message",lambda *_:None):
            thread.start()
            try:
                yield server
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                factory.close()

    def post(self,server,path,payload,extra_headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
        try:
            headers={"Content-Type":"application/json","X-Factory-Request":"1"}
            headers.update(extra_headers or {})
            connection.request("POST",path,body=json.dumps(payload),headers=headers)
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def test_http_malformed_install_payloads_do_not_mutate_the_queue(self):
        with self.local_factory_http() as server:
            for payload in (None,[],True,1,"fixture",{}, {"modelId":None},
                            {"modelId":"../boundary-fixture","acceptLicense":True},
                            {"modelId":"boundary-fixture","acceptLicense":"true"},
                            {"modelId":"boundary-fixture","acceptLicense":False}):
                with self.subTest(payload=payload):
                    status,body=self.post(server,"/api/setup/install",payload)
                    self.assertEqual(status,400,body)
                    self.assertTrue(body.get("error"))
            self.assertEqual(self.manager.snapshot()["jobs"],[])
            self.assertEqual(Source.requests,[])

    def test_http_foreign_origin_and_unknown_jobs_cannot_mutate_installation(self):
        with self.local_factory_http() as server:
            status,_=self.post(server,"/api/setup/install",{"modelId":"boundary-fixture","acceptLicense":True},{"Origin":"https://attacker.invalid"})
            self.assertEqual(status,403)
            status,_=self.post(server,"/api/setup/install",{"modelId":"boundary-fixture","acceptLicense":True},{"X-Factory-Request":""})
            self.assertEqual(status,403)
            for action in ("cancel","retry"):
                status,_=self.post(server,f"/api/setup/jobs/{'0'*32}/{action}",{})
                self.assertEqual(status,400)
                status,_=self.post(server,f"/api/setup/jobs/not-a-job/{action}",{})
                self.assertEqual(status,404)
            self.assertEqual(self.manager.snapshot()["jobs"],[])
            self.assertEqual(Source.requests,[])

    def test_failed_component_probe_does_not_enable_the_engine(self):
        self.model["probes"]=[{"path":"engines/boundary-fixture/component.exe","args":["--help"]}]
        with patch("factory_setup.subprocess.run",return_value=subprocess.CompletedProcess([],23,"","Fixture probe rejected")):
            job=self.install()
            self.start_worker()
            done=self.wait_job(job["id"])
        self.assertEqual(done["status"],"failed",done)
        readiness=self.manager.readiness(self.model["id"])
        self.assertNotIn(readiness["status"],{"installed","ready"},"A failed component probe must keep the engine unavailable")
        self.assertFalse((self.root/"logs/setup/boundary-fixture-receipt.json").exists())

    def test_retry_after_complete_corrupt_download_gets_fresh_pinned_bytes(self):
        Source.content=b"!"*len(GOOD_BYTES)
        failed=self.install()
        self.start_worker()
        done=self.wait_job(failed["id"])
        self.assertEqual(done["status"],"failed",done)
        self.assertIn("SHA256",done["error"])
        self.assertFalse((self.root/"engines/boundary-fixture/model.bin").exists())
        Source.content=GOOD_BYTES
        retried=self.manager.retry(failed["id"])
        completed=self.wait_job(retried["id"])
        self.assertEqual(completed["status"],"completed","Explicit retry must recover from a corrupt full-size partial while retaining failed evidence: "+json.dumps(completed))
        self.assertEqual((self.root/"engines/boundary-fixture/model.bin").read_bytes(),GOOD_BYTES)
        self.assertTrue(any(path.is_file() and path.stat().st_size==len(GOOD_BYTES) and path.read_bytes()==b"!"*len(GOOD_BYTES) for path in self.root.rglob("*")),"Corrupt download evidence must be retained separately")

    def test_cancel_queued_before_worker_transition_prevents_download(self):
        transition=threading.Event()
        proceed=threading.Event()
        finished=threading.Event()
        original_update=self.manager._update

        def guarded_update(job,**fields):
            if fields.get("status")=="running":
                transition.set()
                if not proceed.wait(timeout=5):
                    raise RuntimeError("Boundary test transition gate was not released")
            result=original_update(job,**fields)
            if fields.get("status") in {"completed","failed","cancelled","interrupted"}:
                finished.set()
            return result

        with patch.object(self.manager,"_update",side_effect=guarded_update):
            queued=self.install()
            self.start_worker()
            self.assertTrue(transition.wait(timeout=3),"Worker must reach the deterministic transition boundary")
            try:
                cancelled=self.manager.cancel(queued["id"])
            finally:
                proceed.set()
            self.assertEqual(cancelled["status"],"cancelled")
            self.assertTrue(finished.wait(timeout=3),"Claimed fixture job must finish handling cancellation")
        final=next(item for item in self.manager.snapshot()["jobs"] if item["id"]==queued["id"])
        self.assertEqual(final["status"],"cancelled","A queued cancellation must never be overwritten by the worker claiming that job")
        self.assertEqual(Source.requests,[],"A cancelled queued job must not begin a model download")

    @unittest.skipUnless(os.name=="nt","Windows extended path normalization")
    def test_windows_extended_resolve_prefix_remains_inside_root(self):
        original=Path.resolve

        def extended_candidate(path, *args, **kwargs):
            resolved=original(path,*args,**kwargs)
            if path.name=="model.bin":
                return Path("\\\\?\\"+str(resolved))
            return resolved

        with patch.object(Path,"resolve",new=extended_candidate):
            try:
                candidate=safe_path(self.root,"engines/boundary-fixture/model.bin")
            except ValueError as error:
                self.fail("An in-root Windows extended path must not be rejected: "+str(error))
        self.assertEqual(str(candidate).removeprefix("\\\\?\\"),str(self.root/"engines/boundary-fixture/model.bin"))


if __name__=="__main__":
    unittest.main(verbosity=2)
