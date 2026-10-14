"""Small local HTTP fixtures exercise deployment; no real model download. v0.5.0."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("factory_setup", ROOT / "scripts/factory_setup.py")
SETUP = None
if SPEC and SPEC.loader and Path(SPEC.origin).exists():
    SETUP = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(SETUP)

DATA = b"build something fixture weight\n" * 8192


class FixtureHandler(BaseHTTPRequestHandler):
    ranges = []
    slow = False
    ignore_range = False

    def do_GET(self):
        offset = int(self.headers.get("Range", "bytes=0-").split("=")[-1].split("-")[0])
        type(self).ranges.append(offset)
        if type(self).ignore_range:
            offset = 0
        self.send_response(206 if offset else 200)
        self.send_header("Content-Length", str(len(DATA) - offset))
        if offset:
            self.send_header("Content-Range", f"bytes {offset}-{len(DATA)-1}/{len(DATA)}")
        self.end_headers()
        try:
            for start in range(offset, len(DATA), 4096):
                self.wfile.write(DATA[start:start+4096])
                self.wfile.flush()
                if type(self).slow:
                    time.sleep(0.005)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *_):
        pass


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.assertIsNotNone(SETUP, "deployment manager must exist before first-run installation works")
        self.temp = tempfile.TemporaryDirectory(prefix="build-something-setup-")
        self.root = Path(self.temp.name)
        (self.root / "VERSION.json").write_text('{"version":"0.5.0"}', encoding="utf-8")
        self.hardware = {"platform": "Windows", "architecture": "AMD64", "gpuAvailable": True,
                         "gpus": [{"name": "Fixture RTX", "memoryMiB": 12288}], "ramGiB": 32,
                         "freeDiskBytes": 10000000000, "requirements": []}
        self.model = {"id": "fixture", "title": "Fixture", "kind": "model", "description": "fixture",
                      "recommended": True, "minimumVramMiB": 1024, "minimumRamGiB": 1,
                      "license": {"label": "Fixture license", "url": "https://example.com/license",
                                  "requiresAcceptance": True, "notice": "Fixture"},
                      "files": [{"path": "engines/fixture/model.bin", "size": len(DATA),
                                 "sha256": hashlib.sha256(DATA).hexdigest(),
                                 "url": f"http://127.0.0.1:{self.server.server_port}/weight"}],
                      "required": [{"path": "engines/fixture/model.bin", "size": len(DATA)}]}
        self.catalog = {"version": "0.5.0", "models": [self.model]}
        FixtureHandler.ranges = []
        FixtureHandler.slow = False
        FixtureHandler.ignore_range = False
        self.manager = self.create_manager()

    def tearDown(self):
        if hasattr(self, "manager"):
            self.manager.close()
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def create_manager(self, start_worker=True):
        return SETUP.FactorySetup(self.root, catalog=self.catalog, hardware=self.hardware, start_worker=start_worker)

    def wait_job(self, job_id):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            job = next(j for j in self.manager.snapshot()["jobs"] if j["id"] == job_id)
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(0.015)
        self.fail("fixture install did not finish")

    def test_clean_machine_is_missing_despite_ready_manifest(self):
        folder = self.root / "engines/fixture"
        folder.mkdir(parents=True)
        (folder / "engine.json").write_text('{"id":"fixture","status":"ready"}', encoding="utf-8")
        model = self.manager.snapshot()["models"][0]
        self.assertEqual(model["status"], "missing")
        self.assertEqual(self.manager.readiness("fixture")["status"], "missing")
        self.assertIsNone(self.manager.readiness("unknown"))

    def test_install_verifies_bytes_and_marks_installed_not_gpu_verified(self):
        job = self.manager.install({"modelId": "fixture", "acceptLicense": True})
        done = self.wait_job(job["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual((self.root / "engines/fixture/model.bin").read_bytes(), DATA)
        self.assertEqual(self.manager.snapshot()["models"][0]["status"], "installed")
        self.assertFalse(done.get("gpuInferenceExecuted", False))

    def test_license_must_be_explicit_boolean(self):
        for payload in ({"modelId": "fixture"}, {"modelId": "fixture", "acceptLicense": "true"}):
            with self.assertRaises(ValueError):
                self.manager.install(payload)
        self.assertEqual(self.manager.snapshot()["jobs"], [])

    def test_no_gpu_and_insufficient_space_block_before_download(self):
        for field, value in (("gpuAvailable", False), ("freeDiskBytes", 1)):
            self.manager.hardware_override = dict(self.hardware, **{field: value})
            with self.assertRaises(ValueError):
                self.manager.install({"modelId": "fixture", "acceptLicense": True})
        self.assertEqual(FixtureHandler.ranges, [])

    def test_resume_existing_partial_uses_range(self):
        partial = self.root / "engines/fixture/model.bin.part"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(DATA[:8192])
        done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertIn(8192, FixtureHandler.ranges)

    def test_server_ignoring_range_restarts_without_corruption(self):
        partial = self.root / "engines/fixture/model.bin.part"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(DATA[:8192])
        FixtureHandler.ignore_range = True
        done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual((self.root / "engines/fixture/model.bin").read_bytes(), DATA)

    def test_hash_mismatch_keeps_evidence_and_retry_succeeds(self):
        self.model["files"][0]["sha256"] = "0" * 64
        done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "failed")
        self.assertIn("SHA256", done["error"])
        self.assertFalse((self.root / "engines/fixture/model.bin").exists())
        self.assertTrue((self.root / "engines/fixture/model.bin.part").exists())
        self.manager.catalog["models"][0]["files"][0]["sha256"] = hashlib.sha256(DATA).hexdigest()
        retried = self.wait_job(self.manager.retry(done["id"])["id"])
        self.assertEqual(retried["status"], "completed", retried)

    def test_existing_wrong_file_is_retained_not_overwritten(self):
        target = self.root / "engines/fixture/model.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"wrong content")
        done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "failed")
        self.assertEqual(target.read_bytes(), b"wrong content")

    def test_duplicate_install_returns_active_job(self):
        FixtureHandler.slow = True
        first = self.manager.install({"modelId": "fixture", "acceptLicense": True})
        second = self.manager.install({"modelId": "fixture", "acceptLicense": True})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.manager.snapshot()["jobs"]), 1)

    def test_cancel_retains_partial_and_retry_resumes(self):
        FixtureHandler.slow = True
        job = self.manager.install({"modelId": "fixture", "acceptLicense": True})
        deadline = time.monotonic() + 3
        part = self.root / "engines/fixture/model.bin.part"
        while time.monotonic() < deadline and not part.exists():
            time.sleep(0.01)
        self.manager.cancel(job["id"])
        cancelled = self.wait_job(job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(part.exists())
        FixtureHandler.slow = False
        done = self.wait_job(self.manager.retry(job["id"])["id"])
        self.assertEqual(done["status"], "completed", done)

    def test_restart_marks_unfinished_job_interrupted_without_replay(self):
        self.manager.close()
        self.manager = self.create_manager(start_worker=False)
        job = self.manager.install({"modelId": "fixture", "acceptLicense": True})
        self.manager.close()
        self.manager = self.create_manager(start_worker=False)
        self.assertEqual(self.manager.snapshot()["jobs"][0]["status"], "interrupted")
        self.assertEqual(FixtureHandler.ranges, [])

    def test_catalog_traversal_is_rejected(self):
        self.model["files"][0]["path"] = "../escape.bin"
        with self.assertRaises(ValueError):
            self.create_manager(start_worker=False)

    def test_unsafe_zip_paths_are_rejected(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("../escape.exe", "unsafe")
        archive = self.root / "unsafe.zip"
        archive.write_bytes(buffer.getvalue())
        with self.assertRaises(ValueError):
            SETUP.extract_archive(archive, self.root / "runtime")
        self.assertFalse((self.root / "escape.exe").exists())

    def test_completed_install_rechecks_missing_component(self):
        done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "completed")
        (self.root / "engines/fixture/model.bin").unlink()
        self.assertEqual(self.manager.snapshot()["models"][0]["status"], "missing")

    def test_dependencies_deploy_before_requested_model_and_count_shared_downloads_once(self):
        self.model["minimumVramMiB"] = 0
        second = dict(self.model, id="dependent", title="Dependent", dependencies=["fixture"],
                      files=[], required=[{"path": "engines/dependent/run.ps1"}],
                      adapters=[{"source": "adapter.ps1", "target": "engines/dependent/run.ps1"}])
        (self.root / "adapter.ps1").write_text("fixture adapter", encoding="utf-8")
        self.catalog["models"].append(second)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["models"][1]["downloadBytes"], len(DATA))
        job = self.manager.install({"modelId": "dependent", "acceptLicense": True})
        done = self.wait_job(job["id"])
        self.assertEqual(done["status"], "completed", done)
        self.assertEqual(self.manager.readiness("fixture")["status"], "installed")
        self.assertEqual(self.manager.readiness("dependent")["status"], "installed")
        self.assertEqual(len(FixtureHandler.ranges), 1)

    def test_optional_existing_cuda_support_remains_detected(self):
        self.model["required"] = [{"path": "runtime/cuda12/test.dll", "alternatives": ["previous/cuda/test.dll"], "size": 5}]
        target = self.root / "previous/cuda/test.dll"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"bytes")
        manifest = self.root / "engines/fixture/engine.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"status":"ready"}', encoding="utf-8")
        self.assertEqual(self.manager.readiness("fixture")["status"], "ready")

    def test_python_deployment_profile_is_executable_without_weight_files(self):
        self.assertTrue(callable(getattr(SETUP, "deploy_profile", None)), "Python model deployment must be wired into setup jobs")
        self.model["files"] = []
        self.model["deploymentProfile"] = "fixture-profile"
        self.model["required"] = [{"path": "engines/fixture/python.marker"}]
        def deploy(root, profile_id, on_progress=None, cancel=None, accepted_installation=None):
            self.assertEqual(profile_id, "fixture-profile")
            current = next(job for job in self.manager.jobs if job["modelId"] == "fixture")
            self.assertEqual(accepted_installation, current["id"])
            self.assertEqual(current["acceptedDeploymentProfiles"], ["fixture-profile"])
            marker = root / "engines/fixture/python.marker"
            marker.parent.mkdir(parents=True)
            marker.write_text("installed", encoding="utf-8")
            return {"checks": [{"imports": True}]}
        with patch.object(SETUP, "deploy_profile", side_effect=deploy):
            done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "completed", done)

    def test_supplemental_weight_install_enables_only_requested_variant(self):
        self.model["manifest"] = {"defaultWeightVariant": "Q4_K_M", "variants": [
            {"id": "Q4_K_M", "status": "installed", "verified": True},
            {"id": "Q5_K_M", "status": "missing", "verified": False},
            {"id": "Q6_K", "status": "missing", "verified": False}]}
        supplement = dict(self.model, id="fixture-q5", engineId="fixture", dependencies=["fixture"],
                          weightVariant="Q5_K_M", manifest={}, variantDefinition={"id": "Q5_K_M", "status": "installed", "verified": True})
        self.catalog["models"].append(supplement)
        done = self.wait_job(self.manager.install({"modelId": "fixture-q5", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "completed", done)
        manifest = json.loads((self.root / "engines/fixture/engine.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in manifest["variants"]}
        self.assertEqual(manifest["defaultWeightVariant"], "Q4_K_M")
        self.assertTrue(entries["Q5_K_M"]["verified"])
        self.assertFalse(entries["Q6_K"]["verified"])

    def test_profile_cancellation_uses_cancelled_terminal_state(self):
        self.model["deploymentProfile"] = "fixture-profile"
        def deploy(root, profile_id, on_progress=None, cancel=None, accepted_installation=None):
            current = next(job for job in self.manager.jobs if job["modelId"] == "fixture")
            self.assertEqual(accepted_installation, current["id"])
            self.manager.cancel(current["id"])
            raise RuntimeError("private installer stopped")
        with patch.object(SETUP, "deploy_profile", side_effect=deploy):
            done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "cancelled", done)

    def test_probe_uses_root_arguments_and_explicit_working_directory(self):
        self.model["probes"] = [{"path": "engines/fixture/python.exe", "args": ["@root:engines/fixture/check.py", "--check"],
                                 "cwd": "engines/fixture", "timeout": 90}]
        response = SETUP.subprocess.CompletedProcess([], 0, "", "")
        with patch.object(SETUP, "run_probe", return_value=response) as run:
            done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "completed", done)
        args, kwargs = run.call_args
        self.assertEqual(args[0][1], str(self.root / "engines/fixture/check.py"))
        self.assertEqual(kwargs["cwd"], self.root / "engines/fixture")
        self.assertEqual(kwargs["timeout"], 90)

    def test_failed_reinstall_cannot_reuse_previous_ready_manifest(self):
        target = self.root / "engines/fixture/model.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(DATA)
        manifest = target.parent / "engine.json"
        manifest.write_text('{"id":"fixture","status":"ready"}', encoding="utf-8")
        self.model["probes"] = [{"path": "engines/fixture/component.exe"}]
        response = SETUP.subprocess.CompletedProcess([], 23, "", "broken runtime")
        with patch.object(SETUP, "run_probe", return_value=response):
            done = self.wait_job(self.manager.install({"modelId": "fixture", "acceptLicense": True})["id"])
        self.assertEqual(done["status"], "failed")
        self.assertNotIn(self.manager.readiness("fixture")["status"], {"installed", "ready"})

    def test_existing_complete_support_components_need_no_historical_manifest(self):
        self.model["kind"] = "utility"
        self.model["supportOnly"] = True
        target = self.root / "engines/fixture/model.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(DATA)
        self.assertEqual(self.manager.readiness("fixture")["status"], "installed")
        self.assertTrue(self.manager.snapshot()["models"][0]["supportOnly"])

    def test_hardware_detection_reports_basic_environment_and_capacity(self):
        detected = SETUP.detect_hardware(self.root)
        ids = {entry["id"] for entry in detected["requirements"]}
        self.assertTrue({"platform", "python", "powershell", "gpu", "ram", "disk"}.issubset(ids))
        self.assertGreater(detected["freeDiskBytes"], 0)

    def test_snapshot_remains_available_when_completed_download_renames_partial(self):
        part = self.root / "engines/fixture/model.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(DATA[:8192])
        original_stat = Path.stat
        calls = [0]
        def changing_stat(path, *args, **kwargs):
            if path == part:
                calls[0] += 1
                if calls[0] == 2:
                    raise FileNotFoundError("download just renamed its partial")
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", new=changing_stat):
            try:
                snapshot = self.manager.snapshot()
            except FileNotFoundError:
                self.fail("Polling installation state must tolerate atomic download renames")
        self.assertEqual(snapshot["models"][0]["id"], "fixture")

    def test_cuda_wheel_licenses_are_retained_without_flattening_collisions(self):
        output = self.root / "runtime/cuda12"
        for name, license_text in (("cublas", "cublas license"), ("cudart", "cuda runtime license")):
            archive = self.root / (name + ".whl")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(f"nvidia/{name}/{name}.dll", name)
                bundle.writestr(f"nvidia/{name}/License.txt", license_text)
            try:
                SETUP.extract_archive(archive, output, flatten_dlls=True)
            except ValueError as error:
                self.fail("Valid distinct CUDA component licenses must not collide: " + str(error))
            self.assertTrue((output / (name + ".dll")).is_file())
        saved = [path.read_text() for path in output.rglob("License.txt")]
        self.assertEqual(sorted(saved), ["cublas license", "cuda runtime license"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
