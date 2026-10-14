"""Isolated visual factory contract checks. v0.1.0 / Unclecow.

All input, manifests, state, and outputs live under TemporaryDirectory.
No real engine, production receipt, GPU, or user browser is used.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import http.client
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("factory_web", ROOT / "scripts/factory_web.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load factory_web.py")
WEB = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WEB
SPEC.loader.exec_module(WEB)


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (24, 20), (33, 90, 140, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class FactoryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="factory-web-check-")
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/factory.ps1").write_text(
            "throw 'Fixture engines must never execute'\n", encoding="utf-8"
        )
        (self.root / "VERSION.json").write_text(
            json.dumps({"product": "Fixture", "version": "0.1.0"}), encoding="utf-8"
        )
        for engine_id, mode, status in (
            ("single", "single", "ready"),
            ("multi", "multiview", "ready"),
            ("blocked", "single", "blocked"),
        ):
            folder = self.root / "engines" / engine_id
            folder.mkdir(parents=True)
            manifest = {
                "id": engine_id,
                "title": f"Fixture {engine_id}",
                "version": "fixture-1",
                "factoryVersion": "0.1.0",
                "status": status,
                "entrypoint": "run.ps1",
                "capabilities": {
                    "multiView": mode == "multiview",
                    "views": ["front", "left", "back", "right"] if mode == "multiview" else ["front"],
                },
                "notes": "Isolated fixture only",
            }
            (folder / "engine.json").write_text(json.dumps(manifest), encoding="utf-8")
            (folder / "run.ps1").write_text("throw 'No GPU in unit tests'\n", encoding="utf-8")
        self.factory = WEB.Factory(self.root, start_worker=False)

    def tearDown(self) -> None:
        self.factory.close()
        self.temporary.cleanup()

    def upload(self, filename: str = "main.png") -> dict:
        image = self.factory.upload_image(png_bytes(), filename)
        self.assertIsInstance(image, dict)
        self.assertTrue(image.get("id"))
        return image

    def install_fixture_launcher(self) -> None:
        launcher = (ROOT / "scripts/factory.ps1").read_text(encoding="utf-8")
        launcher = launcher.replace(
            r"Local\Image2D3DFactoryGPU", f"Local\\Image2D3DFactoryTest-{self.root.name}"
        )
        (self.root / "scripts/factory.ps1").write_text(launcher, encoding="utf-8")

    def payload(self, engines: list[str] | None = None) -> dict:
        main = self.upload("正面.png")
        auxiliary = self.upload("左面.png")
        return {
            "name": "隔离批次",
            "objects": [{
                "name": "物体一",
                "mainImageId": main["id"],
                "auxiliaryImages": [{"imageId": auxiliary["id"], "view": "left"}],
            }],
            "engineIds": engines or ["single", "multi"],
        }

    def jobs(self) -> list[dict]:
        jobs = self.factory.state()["jobs"]
        self.assertIsInstance(jobs, list)
        return jobs

    def rewrite_status(self, job_id: str, status: str) -> None:
        self.factory.close()
        state_path = self.root / "logs/visual-factory/state.json"
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        jobs = [job for batch in saved["batches"] for job in batch["jobs"]]
        next(job for job in jobs if job["id"] == job_id)["status"] = status
        state_path.write_text(json.dumps(saved), encoding="utf-8")
        self.factory = WEB.Factory(self.root, start_worker=False)

    @contextmanager
    def local_http(self):
        with patch.object(WEB.Handler, "log_message", lambda *_: None):
            server = WEB.FactoryServer(("127.0.0.1", 0), self.factory)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                yield connection
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_valid_upload_persists_and_reopens(self) -> None:
        uploaded = self.upload("物体正面.png")
        original_state = self.factory.state()
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=False)
        self.assertEqual(self.factory.state()["images"], original_state["images"])
        self.assertIn(uploaded["id"], json.dumps(self.factory.state()["images"]))

    def test_http_csp_allows_local_model_texture_blobs_without_network_sources(self) -> None:
        with self.local_http() as connection:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            policy = response.getheader("Content-Security-Policy")
            response.read()
        self.assertIsNotNone(policy)
        directives = {}
        for directive in policy.split(";"):
            tokens = directive.strip().split()
            if tokens:
                directives[tokens[0]] = set(tokens[1:])
        self.assertEqual(directives["script-src"], {"'self'", "'wasm-unsafe-eval'"})
        self.assertEqual(directives["connect-src"], {"'self'", "blob:"})
        self.assertEqual(directives["img-src"], {"'self'", "blob:", "data:"})
        self.assertEqual(directives["default-src"], {"'self'"})
        for sources in directives.values():
            self.assertFalse(any(source == "*" or source.startswith(("http:", "https:")) for source in sources))

    def test_near_opaque_output_import_normalizes_alpha_preserving_source(self) -> None:
        image = Image.new("RGBA", (3, 2), (33, 90, 140, 253))
        image.putpixel((0, 0), (11, 22, 33, 250))
        image.putpixel((2, 1), (55, 66, 77, 255))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        original = buffer.getvalue()
        folder = self.root / "outputs/single/fixture-image"
        folder.mkdir(parents=True)
        source = folder / "generated.png"
        source.write_bytes(original)
        imported = self.factory.import_output_image("/files/outputs/single/fixture-image/generated.png")
        self.assertEqual(source.read_bytes(), original)
        with Image.open(self.root / "inputs/uploads" / f"{imported['id']}.png") as normalized:
            self.assertEqual(normalized.getchannel("A").getextrema(), (255, 255))
            self.assertEqual(normalized.convert("RGB").tobytes(), image.convert("RGB").tobytes())

    def test_real_cutout_alpha_is_preserved_during_upload(self) -> None:
        for minimum_alpha in (0, 249):
            with self.subTest(minimum_alpha=minimum_alpha):
                image = Image.new("RGBA", (3, 2), (33, 90, 140, 255))
                image.putpixel((0, 0), (11, 22, 33, minimum_alpha))
                image.putpixel((1, 0), (44, 55, 66, max(minimum_alpha, 128)))
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                imported = self.factory.upload_image(buffer.getvalue(), "真实抠图.png")
                with Image.open(self.root / "inputs/uploads" / f"{imported['id']}.png") as normalized:
                    self.assertEqual(normalized.tobytes(), image.tobytes())

    def test_fully_transparent_upload_remains_rejected(self) -> None:
        buffer = BytesIO()
        Image.new("RGBA", (3, 2), (33, 90, 140, 0)).save(buffer, format="PNG")
        with self.assertRaisesRegex(ValueError, "完全透明"):
            self.factory.upload_image(buffer.getvalue(), "透明图片.png")
        self.assertEqual(self.factory.state()["images"], {})

    def test_invalid_image_is_rejected_without_creating_upload(self) -> None:
        with self.assertRaises((ValueError, RuntimeError)):
            self.factory.upload_image(b"This is not a PNG image.", "forged.png")
        self.assertEqual(self.factory.state()["images"], {})
        uploads = self.root / "inputs/uploads"
        self.assertEqual(list(uploads.rglob("*")) if uploads.exists() else [], [])

    def test_upload_filename_cannot_escape_upload_directory(self) -> None:
        try:
            self.factory.upload_image(png_bytes(), "../../escaped.png")
        except (ValueError, RuntimeError):
            pass
        self.assertFalse((self.root / "escaped.png").exists())
        self.assertFalse((self.root / "inputs/escaped.png").exists())
        for path in self.root.rglob("*.png"):
            self.assertTrue(path.resolve().is_relative_to((self.root / "inputs/uploads").resolve()))

    def test_missing_main_image_rejects_whole_batch(self) -> None:
        payload = self.payload()
        payload["objects"][0].pop("mainImageId")
        with self.assertRaises((ValueError, RuntimeError)):
            self.factory.create_batch(payload)
        self.assertEqual(self.jobs(), [])

    def test_unknown_image_id_is_not_interpreted_as_file_path(self) -> None:
        payload = self.payload()
        payload["objects"][0]["mainImageId"] = "../../outside.png"
        with self.assertRaises((ValueError, RuntimeError)):
            self.factory.create_batch(payload)
        self.assertEqual(self.jobs(), [])

    def test_duplicate_auxiliary_direction_rejects_whole_batch(self) -> None:
        payload = self.payload()
        payload["objects"][0]["auxiliaryImages"].append({
            "imageId": self.upload("另一个左面.png")["id"], "view": "left"
        })
        with self.assertRaises((ValueError, RuntimeError)):
            self.factory.create_batch(payload)
        self.assertEqual(self.jobs(), [])

    def test_invalid_auxiliary_direction_rejects_whole_batch(self) -> None:
        payload = self.payload()
        payload["objects"][0]["auxiliaryImages"][0]["view"] = "../../back"
        with self.assertRaises((ValueError, RuntimeError)):
            self.factory.create_batch(payload)
        self.assertEqual(self.jobs(), [])

    def test_unknown_or_blocked_engine_rejects_whole_batch(self) -> None:
        for engine_id in ("unknown", "blocked", "../../single"):
            with self.subTest(engine=engine_id):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.factory.create_batch(self.payload(["single", engine_id]))
                self.assertEqual(self.jobs(), [])

    def test_two_objects_and_two_tools_create_four_unique_jobs(self) -> None:
        payload = self.payload()
        second = deepcopy(payload["objects"][0])
        second["name"] = "物体二"
        payload["objects"].append(second)
        batch = self.factory.create_batch(payload)
        self.assertTrue(batch.get("id"))
        jobs = self.jobs()
        self.assertEqual(len(jobs), 4)
        self.assertEqual(len({job["id"] for job in jobs}), 4)
        self.assertEqual({job["status"] for job in jobs}, {"queued"})
        self.assertEqual({job["engineId"] for job in jobs}, {"single", "multi"})

    def test_single_uses_main_and_multiview_uses_directional_auxiliary(self) -> None:
        payload = self.payload()
        self.factory.create_batch(payload)
        by_engine = {job["engineId"]: job for job in self.jobs()}
        main_id = payload["objects"][0]["mainImageId"]
        left_id = payload["objects"][0]["auxiliaryImages"][0]["imageId"]
        self.assertEqual(by_engine["single"]["viewImages"], {"front": main_id})
        self.assertEqual(by_engine["multi"]["viewImages"], {"front": main_id, "left": left_id})
        self.assertEqual(by_engine["single"]["inputMode"], "single")
        self.assertEqual(by_engine["multi"]["inputMode"], "multi-view")

    def test_multiview_accepts_main_image_without_auxiliary(self) -> None:
        payload = self.payload(["multi"])
        payload["objects"][0]["auxiliaryImages"] = []
        self.factory.create_batch(payload)
        self.assertEqual(self.jobs()[0]["viewImages"], {"front": payload["objects"][0]["mainImageId"]})

    def test_queued_jobs_survive_restart(self) -> None:
        self.factory.create_batch(self.payload())
        expected_jobs = self.jobs()
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=False)
        self.assertEqual(self.jobs(), expected_jobs)

    def test_running_job_recovers_as_interrupted_without_replay(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        job_id = self.jobs()[0]["id"]
        self.rewrite_status(job_id, "running")
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.jobs()[0]["id"], job_id)
        self.assertEqual(self.jobs()[0]["status"], "interrupted")

    def test_success_receipt_without_model_is_not_restored_as_generated(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        job = self.jobs()[0]
        folder = self.root / "outputs" / job["engineId"] / job["runId"]
        folder.mkdir(parents=True)
        (folder / "receipt.json").write_text(json.dumps({"status": "generated-unreviewed"}), encoding="utf-8")
        (folder / "run.log").write_text("The model file is missing.\n", encoding="utf-8")
        self.rewrite_status(job["id"], "running")
        self.assertEqual(self.jobs()[0]["status"], "interrupted")

    def test_finished_output_is_reconciled_after_interruption(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        job = self.jobs()[0]
        folder = self.root / "outputs" / job["engineId"] / job["runId"]
        folder.mkdir(parents=True)
        (folder / "receipt.json").write_text(json.dumps({"status": "generated-unreviewed"}), encoding="utf-8")
        (folder / "fixture.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
        self.rewrite_status(job["id"], "running")
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.jobs()[0]["status"], "generated-unreviewed")

    def test_failed_retry_creates_new_job_preserving_original(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        original_id = self.jobs()[0]["id"]
        self.rewrite_status(original_id, "failed")
        self.factory.retry_job(original_id)
        jobs = self.jobs()
        self.assertEqual(len(jobs), 2)
        original = next(job for job in jobs if job["id"] == original_id)
        retry = next(job for job in jobs if job["id"] != original_id)
        self.assertEqual(original["status"], "failed")
        self.assertEqual(retry["status"], "queued")
        self.assertEqual(retry["engineId"], original["engineId"])

    def test_retry_of_queued_job_is_rejected(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        with self.assertRaises((ValueError, RuntimeError)):
            self.factory.retry_job(self.jobs()[0]["id"])
        self.assertEqual(len(self.jobs()), 1)

    def test_cancel_queued_job_does_not_create_outputs(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        job_id = self.jobs()[0]["id"]
        self.factory.cancel_job(job_id)
        self.assertEqual(self.jobs()[0]["status"], "cancelled")
        outputs = self.root / "outputs"
        self.assertEqual(list(outputs.rglob("*")) if outputs.exists() else [], [])

    def test_public_state_snapshot_cannot_mutate_queue(self) -> None:
        self.factory.create_batch(self.payload(["single"]))
        snapshot = self.factory.state()
        snapshot["jobs"][0]["status"] = "generated-unreviewed"
        self.assertEqual(self.jobs()[0]["status"], "queued")

    def test_worker_routes_both_modes_using_isolated_fake_engines(self) -> None:
        self.factory.close()
        self.install_fixture_launcher()
        fixture_engine = """param([string]$InputImage,[string]$OutputDirectory,[string]$InputSet)
$ErrorActionPreference='Stop'
$views=if($InputSet){Get-Content -Raw -LiteralPath $InputSet|ConvertFrom-Json -AsHashtable}else{@{front=$InputImage}}
@{main=$InputImage;views=$views}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath (Join-Path $OutputDirectory 'fixture-inputs.json') -Encoding utf8NoBOM
"v 0 0 0`nv 1 0 0`nv 0 1 0`nf 1 2 3"|Set-Content -LiteralPath (Join-Path $OutputDirectory 'fixture.obj') -Encoding utf8NoBOM
"""
        for engine_id in ("single", "multi"):
            (self.root / "engines" / engine_id / "run.ps1").write_text(fixture_engine, encoding="utf-8")
        self.factory = WEB.Factory(self.root, start_worker=True)
        self.factory.create_batch(self.payload())
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            jobs = self.jobs()
            if all(job["status"] not in {"queued", "running"} for job in jobs):
                break
            time.sleep(0.05)
        self.assertEqual([job["status"] for job in self.jobs()], ["generated-unreviewed"] * 2)
        for engine_id, expected_views in (("single", {"front"}), ("multi", {"front", "left"})):
            captures = list((self.root / "outputs" / engine_id).glob("*/fixture-inputs.json"))
            self.assertEqual(len(captures), 1)
            captured = json.loads(captures[0].read_text(encoding="utf-8"))
            self.assertEqual(set(captured["views"]), expected_views)
            self.assertEqual(captured["main"], captured["views"]["front"])
            for path in captured["views"].values():
                self.assertTrue(Path(path).resolve().is_relative_to((self.root / "outputs").resolve()))

    def test_running_cancellation_stops_fake_engine_and_updates_receipt(self) -> None:
        self.factory.close()
        self.install_fixture_launcher()
        (self.root / "engines/single/run.ps1").write_text(
            "param([string]$InputImage,[string]$OutputDirectory)\n"
            "Start-Sleep -Seconds 30\n"
            "'Should not be written'|Set-Content -LiteralPath (Join-Path $OutputDirectory 'unexpected.obj')\n",
            encoding="utf-8",
        )
        self.factory = WEB.Factory(self.root, start_worker=True)
        self.factory.create_batch(self.payload(["single"]))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.jobs()[0]
            receipt_path = self.root / "outputs" / job["engineId"] / job["runId"] / "receipt.json"
            if receipt_path.is_file():
                break
            time.sleep(0.05)
        self.assertTrue(receipt_path.is_file(), "The isolated fake engine did not start")
        self.factory.cancel_job(job["id"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.jobs()[0]["status"] == "running":
            time.sleep(0.05)
        self.assertEqual(self.jobs()[0]["status"], "cancelled")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "cancelled")
        self.assertFalse((receipt_path.parent / "unexpected.obj").exists())

    def test_http_upload_image_can_be_read_back(self) -> None:
        uploaded = self.upload()
        with self.local_http() as connection:
            connection.request("GET", uploaded["url"])
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            with Image.open(BytesIO(body)) as decoded:
                self.assertEqual(decoded.size, (24, 20))

    def test_http_file_paths_are_limited_to_each_allowed_bucket(self) -> None:
        with self.local_http() as connection:
            for url in (
                "/files/inputs/../VERSION.json",
                "/files/outputs/%2e%2e/VERSION.json",
                "/files/config/secret.json",
            ):
                with self.subTest(url=url):
                    connection.request("GET", url)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 403)

    def test_http_range_reads_exact_requested_bytes(self) -> None:
        folder = self.root / "outputs/single/fixture"
        folder.mkdir(parents=True)
        (folder / "fixture.glb").write_bytes(b"0123456789")
        with self.local_http() as connection:
            for byte_range, expected, content_range in (
                ("bytes=2-5", b"2345", "bytes 2-5/10"),
                ("bytes=-3", b"789", "bytes 7-9/10"),
            ):
                connection.request("GET", "/files/outputs/single/fixture/fixture.glb", headers={"Range": byte_range})
                response = connection.getresponse()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
                self.assertEqual(response.getheader("Content-Range"), content_range)
            connection.request("GET", "/files/outputs/single/fixture/fixture.glb", headers={"Range": "bytes=99-100"})
            response = connection.getresponse()
            self.assertEqual(response.status, 416)
            self.assertEqual(response.read(), b"")

    def test_http_rejects_foreign_origin(self) -> None:
        with self.local_http() as connection:
            connection.request("GET", "/api/health", headers={"Origin": "https://example.invalid"})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 403)

    def test_http_mutation_requires_factory_request_header(self) -> None:
        with self.local_http() as connection:
            connection.request("POST", "/api/batches", body=b"{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 403)
        self.assertEqual(self.jobs(), [])

    def test_http_health_captures_loaded_version_and_build(self) -> None:
        build = self.factory.service_build
        self.assertRegex(build, r"^[a-f0-9]{64}$")
        (self.root / "VERSION.json").write_text(json.dumps({"version": "0.1.1"}), encoding="utf-8")
        with self.local_http() as connection:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            health = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(health["version"], "0.1.0")
            self.assertEqual(health["serviceBuild"], build)
        self.factory.close()
        self.factory = WEB.Factory(self.root, start_worker=False)
        self.assertEqual(self.factory.version(), "0.1.1")

    def test_service_fingerprint_tracks_core_source_contents(self) -> None:
        from factory_queue import service_fingerprint
        directory = self.root / "scripts"
        (directory / "factory_queue.py").write_text("# fixture queue\n", encoding="utf-8")
        (directory / "factory_web.py").write_text("# fixture web\n", encoding="utf-8")
        first = service_fingerprint(directory)
        self.assertEqual(service_fingerprint(directory), first)
        (directory / "factory_web.py").write_text("# changed fixture web\n", encoding="utf-8")
        self.assertNotEqual(service_fingerprint(directory), first)

    def test_http_empty_json_job_actions_match_frontend(self) -> None:
        batch = self.factory.create_batch(self.payload())
        headers = {"Content-Type": "application/json", "X-Factory-Request": "1"}
        with self.local_http() as connection:
            for prefix, job in zip(("jobs", "image-jobs"), batch["jobs"]):
                connection.request("POST", f"/api/{prefix}/{job['id']}/cancel", body=b"{}", headers=headers)
                response = connection.getresponse()
                cancelled = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(cancelled["status"], "cancelled")
                connection.request("POST", f"/api/{prefix}/{job['id']}/retry", body=b"{}", headers=headers)
                response = connection.getresponse()
                retry = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(retry["status"], "queued")
                self.assertEqual(retry["retryOf"], job["id"])
            connection.request("POST", f"/api/batches/{batch['id']}/cancel", body=b"{}", headers=headers)
            response = connection.getresponse()
            cancelled_batch = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertTrue(all(job["status"] == "cancelled" for job in cancelled_batch["jobs"]))

    def test_duplicate_service_start_does_not_open_or_mutate_queue(self) -> None:
        with patch.object(sys, "argv", ["factory_web.py", "--root", str(self.root)]), \
                patch.object(WEB, "FactoryServer", side_effect=OSError("Port is already bound")), \
                patch.object(WEB, "Factory") as factory_constructor:
            with self.assertRaises(OSError):
                WEB.main()
            factory_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
