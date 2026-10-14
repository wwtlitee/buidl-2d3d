"""Isolated skin, real joint-keyframe and queue checks. v0.3.0 / Unclecow."""
from __future__ import annotations

import json
import math
import http.client
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from factory_glb import animate_glb, inspect_glb, read_glb, rotate
from factory_model_job import run_job
from legacy_queue_fixture import accept_legacy_job
from factory_postprocess import checked_model
from factory_queue import Factory
from factory_web import FactoryServer


def fixture_glb(path, skinned=True, invalid_weights=False, vertex_count=3):
    triangle = struct.pack("<9f", 0, 1, 0, 1, 1, 0, 0, 2, 0)
    positions = (triangle * ((vertex_count+2)//3))[:vertex_count*12]
    joints = struct.pack("<4B", 1, 0, 0, 0) * vertex_count
    weights = struct.pack("<4f", *([0, 0, 0, 0] if invalid_weights else [1, 0, 0, 0])) * vertex_count
    binary = positions + joints + weights
    attributes = {"POSITION": 0}
    if skinned:
        attributes.update(JOINTS_0=1, WEIGHTS_0=2)
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 2]}],
        "nodes": [{"name": "Root", "children": [1]}, {"name": "RightForeArm"}, {"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attributes}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                        {"buffer": 0, "byteOffset": len(positions), "byteLength": len(joints)},
                        {"buffer": 0, "byteOffset": len(positions) + len(joints), "byteLength": len(weights)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": vertex_count, "min": [0, 1, 0], "max": [1, 2, 0]},
                      {"bufferView": 1, "componentType": 5121, "type": "VEC4", "count": vertex_count},
                      {"bufferView": 2, "componentType": 5126, "type": "VEC4", "count": vertex_count}]}
    if skinned:
        document["skins"] = [{"joints": [0, 1]}]
        document["nodes"][2]["skin"] = 0
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28+len(encoded)+len(binary))
                    + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
                    + struct.pack("<II", len(binary), 0x004E4942) + binary)


class AnimationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="factory-animation-")
        self.root = Path(self.temp.name)
        (self.root / "VERSION.json").write_text('{"version":"0.3.0"}', encoding="utf-8")
        for engine, kind in [("rig-fixture", "rig"), ("basic-motion", "animation")]:
            folder = self.root / "engines" / engine
            folder.mkdir(parents=True)
            (folder / "run.ps1").write_text("throw 'No GPU in fixture'", encoding="utf-8")
            (folder / "engine.json").write_text(json.dumps({"id": engine, "title": engine, "version": "0.3.0", "kind": kind,
                "status": "installed", "entrypoint": "run.ps1", "capabilities": {"multiView": False}}), encoding="utf-8")
        self.path = self.root / "outputs/source/run/model.glb"
        fixture_glb(self.path)
        self.url = "/files/outputs/source/run/model.glb"
        self.factory = Factory(self.root, start_worker=False)

    def tearDown(self):
        self.factory.close()
        self.temp.cleanup()

    def test_skin_and_positive_normalized_weights_are_required(self):
        self.assertEqual(inspect_glb(self.path, require_skin=True)["skinnedVertices"], 3)
        fixture_glb(self.path, skinned=False)
        with self.assertRaisesRegex(ValueError, "蒙皮"):
            inspect_glb(self.path, require_skin=True)
        fixture_glb(self.path, invalid_weights=True)
        with self.assertRaisesRegex(ValueError, "权重"):
            inspect_glb(self.path, require_skin=True)

    def test_every_vertex_is_checked_and_contributes_to_influence(self):
        count = 8192
        fixture_glb(self.path, vertex_count=count)
        self.assertEqual(inspect_glb(self.path, require_skin=True)["influencingNodes"][1], count)
        document, binary = read_glb(self.path)
        original = self.path.read_bytes()
        binary_start = len(original) - len(binary)
        joints_offset = binary_start + document["bufferViews"][1]["byteOffset"]
        weights_offset = binary_start + document["bufferViews"][2]["byteOffset"]
        # Row 1 was skipped by the old stride-2 sampling at 8,192 vertices.
        broken = bytearray(original)
        broken[joints_offset+4] = 7
        self.path.write_bytes(broken)
        with self.assertRaisesRegex(ValueError, "不存在的关节"):
            inspect_glb(self.path, require_skin=True)
        for weight in (float("nan"), -1.0, 0.0):
            with self.subTest(weight=weight):
                broken = bytearray(original)
                struct.pack_into("<f", broken, weights_offset+16, weight)
                self.path.write_bytes(broken)
                with self.assertRaisesRegex(ValueError, "权重"):
                    inspect_glb(self.path, require_skin=True)
        sparse = bytearray(original)
        sparse[joints_offset:joints_offset+count*4] = bytes(count*4)
        sparse[joints_offset+4] = 1
        self.path.write_bytes(sparse)
        influence = inspect_glb(self.path, require_skin=True)["influencingNodes"]
        self.assertEqual(influence, {0: count-1, 1: 1})

    def test_cubic_tangents_count_actual_motion_only(self):
        base_document, base_binary = read_glb(self.path)
        cases = [
            ("translation", [(0, 0, 0), (0, 0, 0), (1, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)], True),
            # Equal tangents make the midpoint stationary, but the curve moves.
            ("translation", [(0, 0, 0), (0, 0, 0), (1, 0, 0), (1, 0, 0), (0, 0, 0), (0, 0, 0)], True),
            # First incoming and last outgoing tangents never affect a segment.
            ("translation", [(1, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), (1, 0, 0)], False),
            ("rotation", [(0, 0, 0, 0), (0, 0, 0, 1), (1, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1), (0, 0, 0, 0)], True),
            # Normalizing a radial quaternion curve preserves its orientation.
            ("rotation", [(0, 0, 0, 0), (0, 0, 0, 1), (0, 0, 0, 1), (0, 0, 0, 0), (0, 0, 0, 1), (0, 0, 0, 0)], False),
        ]
        for index, (target, values, changes) in enumerate(cases):
            with self.subTest(target=target, case=index):
                document = json.loads(json.dumps(base_document))
                binary = bytearray(base_binary)
                for rows, kind in [([(0,), (2,)], "SCALAR"), (values, "VEC4" if target == "rotation" else "VEC3")]:
                    packed = struct.pack("<" + "f" * sum(len(row) for row in rows), *(value for row in rows for value in row))
                    document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(packed)})
                    binary.extend(packed)
                    document["accessors"].append({"bufferView": len(document["bufferViews"])-1,
                        "componentType": 5126, "type": kind, "count": len(rows)})
                document["buffers"][0]["byteLength"] = len(binary)
                document["animations"] = [{"samplers": [{"input": 3, "output": 4, "interpolation": "CUBICSPLINE"}],
                    "channels": [{"sampler": 0, "target": {"node": 1, "path": target}}]}]
                encoded = json.dumps(document).encode()
                encoded += b" " * (-len(encoded) % 4)
                self.path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28+len(encoded)+len(binary))
                    + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
                    + struct.pack("<II", len(binary), 0x004E4942) + binary)
                if changes:
                    summary = inspect_glb(self.path, require_skin=True, require_animation=True)
                    self.assertEqual(summary["animatedJointChannels"], 1)
                else:
                    with self.assertRaisesRegex(ValueError, "实际变化"):
                        inspect_glb(self.path, require_skin=True, require_animation=True)
    def test_animation_changes_a_weighted_child_not_whole_model(self):
        original_doc, original_binary = read_glb(self.path)
        output = self.path.parent / "animated.glb"
        summary = animate_glb(self.path, output)
        document, binary = read_glb(output)
        self.assertEqual(binary[:len(original_binary)], original_binary)
        self.assertEqual(document["meshes"], original_doc["meshes"])
        self.assertEqual(document["skins"], original_doc["skins"])
        animation = document["animations"][0]
        self.assertEqual(animation["channels"][0]["target"]["node"], 1)
        accessor = document["accessors"][animation["samplers"][0]["output"]]
        view = document["bufferViews"][accessor["bufferView"]]
        at_start = struct.unpack_from("<4f", binary, view["byteOffset"])
        at_quarter = struct.unpack_from("<4f", binary, view["byteOffset"] + 15*16)
        self.assertLess(math.dist(at_start, [0, 0, 0, 1]), 1e-6)
        self.assertGreater(math.dist(rotate(at_quarter, [0, 1, 0]), [0, 1, 0]), 0.1)
        self.assertEqual(summary["animatedChannels"], 1)

    def test_motion_does_not_overwrite_or_guess_humanoid_names(self):
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            animate_glb(self.path, self.path)
        with self.assertRaisesRegex(ValueError, "RightArm"):
            animate_glb(self.path, self.path.parent / "wave.glb", "wave")
        with self.assertRaisesRegex(ValueError, "左右大腿"):
            animate_glb(self.path, self.path.parent / "walk.glb", "walk")

    def test_model_address_traversal_and_changed_source_are_rejected(self):
        source = checked_model(self.root, self.url)
        with self.assertRaises(ValueError):
            checked_model(self.root, "/files/outputs/%2e%2e/VERSION.json")
        with self.assertRaises(ValueError):
            checked_model(self.root, "https://example.test/model.glb")
        fixture_glb(self.path, skinned=False)
        with self.assertRaisesRegex(ValueError, "改变"):
            checked_model(self.root, self.url, source["sha256"])

    def test_legacy_animation_record_keeps_fixed_source_but_cannot_create_retry(self):
        source = checked_model(self.root, self.url, require_skin=True)
        request = {"modelUrl": self.url, "sourceSha256": source['sha256'], "motion": "joint-test"}
        job, _ = accept_legacy_job(self.factory, 'basic-motion', request)
        self.assertEqual(job["kind"], "animation")
        self.assertEqual(job["viewImages"], {})
        stored = self.factory._find_job(job["id"])
        stored["status"] = "failed"
        with self.assertRaisesRegex(ValueError, '首版'):
            self.factory.retry_job(job['id'])
        self.assertEqual(len(self.factory._jobs()), 1)
        self.assertEqual(stored['request'], request)
        fixture_glb(self.path, skinned=False)
        with self.assertRaisesRegex(ValueError, '改变'):
            checked_model(self.root, self.url, request['sourceSha256'])

    def test_static_model_cannot_skip_binding_and_runner_creates_real_output(self):
        request = checked_model(self.root, self.url)
        config = {"modelUrl": self.url, "sourceSha256": request["sha256"], "motion": "joint-test", "objectName": "Fixture"}
        run_id = "20261003-160000-abcdef"
        job, request_path = accept_legacy_job(self.factory, 'basic-motion', config, run_id=run_id)
        run_job(self.root, "basic-motion", request_path, run_id)
        receipt = json.loads((self.root / "outputs/basic-motion" / run_id / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "generated-unreviewed")
        self.assertEqual(receipt["modelChecks"]["animated.glb"]["animatedChannels"], 1)
        fixture_glb(self.path, skinned=False)
        with self.assertRaisesRegex(ValueError, "蒙皮"):
            checked_model(self.root, self.url, require_skin=True)
        with self.assertRaisesRegex(ValueError, '首版'):
            self.factory.create_model_job({"stage": "animation", "engineId": "basic-motion", "modelUrl": self.url})

    def test_constant_keyframes_are_not_accepted_as_joint_motion(self):
        output = self.path.parent / "constant.glb"
        animate_glb(self.path, output)
        document, binary = read_glb(output)
        accessor = document["accessors"][document["animations"][0]["samplers"][0]["output"]]
        view = document["bufferViews"][accessor["bufferView"]]
        raw = bytearray(output.read_bytes())
        binary_start = len(raw) - len(binary)
        for frame in range(accessor["count"]):
            struct.pack_into("<4f", raw, binary_start + view["byteOffset"] + frame * 16, 0, 0, 0, 1)
        output.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "实际变化"):
            inspect_glb(output, require_skin=True, require_animation=True)

    def test_http_model_jobs_fix_input_and_reject_wrong_stage(self):
        server = FactoryServer(("127.0.0.1", 0), self.factory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for payload, status in [({"stage": "animation", "engineId": "basic-motion", "modelUrl": self.url}, 400),
                                    ({"stage": "rig", "engineId": "basic-motion", "modelUrl": self.url}, 400)]:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("POST", "/api/model-jobs", json.dumps(payload), {"Content-Type": "application/json", "X-Factory-Request": "1"})
                response = connection.getresponse()
                result = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, status, result)
                self.assertIn('首版', result['error'])
            self.assertEqual(self.factory._jobs(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_animated_unweighted_leaf_cannot_claim_mesh_motion(self):
        output = self.path.parent / "unused.glb"
        animate_glb(self.path, output)
        document, binary = read_glb(output)
        joints_view = document["bufferViews"][1]
        raw = bytearray(output.read_bytes())
        binary_start = len(raw) - len(binary)
        raw[binary_start + joints_view["byteOffset"]:binary_start + joints_view["byteOffset"] + 12] = bytes(12)
        output.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "实际变化"):
            inspect_glb(output, require_skin=True, require_animation=True)

    def test_cli_refuses_retired_creation_before_requests_or_uploads(self):
        spec = importlib.util.spec_from_file_location("factory_cli_animation", ROOT / "scripts/factory-cli.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        factory = self.factory
        class Client:
            def request(self, path, payload):
                self.asserted_path = path
                if path != "/api/model-jobs":
                    raise AssertionError(path)
                return factory.create_model_job(payload)
        for args, kind in [(('rig', '--engine', 'rig-fixture', '--model-url', self.url), 'rig'),
                           (('animate', '--model-url', self.url, '--motion', 'joint-test'), 'animation')]:
            client = Client()
            with self.assertRaisesRegex(ValueError, '首版'):
                cli.execute(cli.parser().parse_args(args), client)
            self.assertFalse(hasattr(client, 'asserted_path'))

    def test_provider_receives_empty_folder_and_only_checked_model_is_promoted(self):
        source = checked_model(self.root, self.url)
        run_id = "20261003-170000-abcd12"
        job, request = accept_legacy_job(self.factory, 'rig-fixture', {"modelUrl":self.url,"sourceSha256":source['sha256']}, run_id=run_id)
        def provider(command, **kwargs):
            folder = Path(command[command.index("-OutputDirectory") + 1])
            self.assertFalse(folder.exists())
            folder.mkdir()
            fixture_glb(folder / "rigged.glb")
            (folder / "result.json").write_text('{"primaryOutput":"rigged.glb"}', encoding="utf-8")
            return SimpleNamespace(returncode=0)
        with patch("factory_model_job.subprocess.run", side_effect=provider):
            run_job(self.root, "rig-fixture", request, run_id)
        output = self.root / "outputs/rig-fixture" / run_id
        self.assertTrue((output / "rigged.glb").is_file())
        receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["inputSha256"], source["sha256"])
        self.assertEqual(receipt["modelChecks"]["rigged.glb"]["skinnedVertices"], 3)
        self.assertEqual([item["name"] for item in self.factory.assets(output) if item["kind"] == "model"], ["rigged.glb"])


if __name__ == "__main__":
    unittest.main()
