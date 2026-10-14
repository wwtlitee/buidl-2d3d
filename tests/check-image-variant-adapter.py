"""Pinned variant routing on the shipped template, no GPU. v0.9.1 / Unclecow."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qwen_variant_adapter", ROOT / "scripts/templates/qwen-image-run.py")
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class VariantAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="factory-variant-adapter-")
        self.root = Path(self.directory.name)
        self.engine = self.root / "engines/qwen-image-21"
        (self.engine / "runtime/models").mkdir(parents=True)
        (self.root / "config").mkdir()
        self.source = self.root / "config/image-model-source.json"
        self.variant_source = self.root / "config/image-variants.json"
        files = []
        for role in ("runtime", "diffusion", "textEncoder", "vae"):
            path = self.engine / "runtime/models" / (role + ".fixture")
            path.write_bytes(role.encode())
            files.append({"role": role, "relative": path.relative_to(self.engine).as_posix(),
                          "name": path.name, "size": path.stat().st_size, "sha256": "fixture-sha",
                          "repo": "fixture/shared", "revision": "fixture-pinned"})
        executable = self.engine / "runtime/sd-cli.exe"
        executable.write_bytes(b"not executable")
        baseline = {"status": "verified", "runtimeRelease": ADAPTER.RELEASE, "files": files,
                    "receipts": [{"verified": True}] * 4,
                    "cli": {"executable": "runtime/sd-cli.exe", "requiredFlags": [
                        "--backend", "--params-backend", "--fa", "--diffusion-model", "--llm", "--vae"]}}
        self.source.write_text(json.dumps(baseline), encoding="utf-8")
        self.variants = []
        for name in ("Q4_0", "Q4_K_M", "Q5_K_M", "Q6_K"):
            path = self.engine / "runtime/models" / (name + ".gguf")
            path.write_bytes(b"GGUF fixture " + name.encode())
            self.variants.append({"id": name, "bytes": path.stat().st_size, "status": "installed", "verified": True,
                                  "path": path.relative_to(self.root).as_posix(),
                                  "pin": {"repo": "fixture/" + name, "revision": "fixed-" + name,
                                          "filename": path.name, "size": path.stat().st_size, "sha256": "hash-" + name}})
        self.save_variants()
        self.constants = patch.multiple(ADAPTER, ROOT=self.root, ENGINE=self.engine, SOURCE=self.source,
                                       VARIANT_SOURCE=self.variant_source)
        self.constants.start()

    def save_variants(self):
        self.variant_source.write_text(json.dumps({"engineId": "qwen-image-21", "variants": self.variants}), encoding="utf-8")

    def tearDown(self):
        self.constants.stop()
        self.directory.cleanup()

    def test_missing_selection_remains_actual_legacy_q4_k(self):
        request = ADAPTER.validate_request({"prompt": "a cow"})
        self.assertEqual(request["weightVariant"], "Q4_K")
        _, files, paths, _ = ADAPTER.installed_sources(request["weightVariant"])
        self.assertEqual(files["diffusion"]["name"], "diffusion.fixture")
        self.assertEqual(paths["diffusion"].name, "diffusion.fixture")

    def test_each_explicit_variant_routes_exact_file_and_pin(self):
        for expected in self.variants:
            with self.subTest(variant=expected["id"]):
                request = ADAPTER.validate_request({"prompt": "a cow", "weightVariant": expected["id"]})
                _, files, paths, executable = ADAPTER.installed_sources(request["weightVariant"])
                self.assertEqual(paths["diffusion"].name, expected["pin"]["filename"])
                self.assertEqual(files["diffusion"]["sha256"], expected["pin"]["sha256"])
                command = ADAPTER.build_command(request, paths, executable, self.root / "image.png")
                self.assertEqual(command[command.index("--diffusion-model") + 1], str(paths["diffusion"]))

    def test_unknown_non_string_or_null_selection_is_rejected(self):
        for value in ("Q8_0", "Q4_K_m", "../../Q6_K", None, False, 4, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ADAPTER.validate_request({"prompt": "cow", "weightVariant": value})

    def test_installing_or_unverified_variant_never_falls_back(self):
        for status, verified in (("installing", False), ("installed", False), ("blocked", True)):
            self.variants[0].update(status=status, verified=verified)
            self.save_variants()
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                ADAPTER.installed_sources("Q4_0")

    def test_missing_changed_or_redirected_weight_is_rejected(self):
        original = self.variants[0].copy()
        for changed in ({"path": "../../outside.gguf"}, {"path": "engines/qwen-image-21/runtime/models/missing.gguf"}, {"bytes": 1}):
            self.variants[0] = original | changed
            self.save_variants()
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                ADAPTER.installed_sources("Q4_0")
        self.variants[0] = original
        (self.root / original["path"]).write_bytes(b"changed length")
        self.save_variants()
        with self.assertRaises(RuntimeError):
            ADAPTER.installed_sources("Q4_0")

    def test_prompt_is_one_argv_item(self):
        prompt = '中文 "quoted" $(not_a_shell)\nsecond line'
        request = ADAPTER.validate_request({"prompt": prompt, "weightVariant": "Q6_K"})
        _, _, paths, executable = ADAPTER.installed_sources("Q6_K")
        command = ADAPTER.build_command(request, paths, executable, self.root / "image.png")
        self.assertEqual(command[command.index("--prompt") + 1], prompt)
        self.assertEqual(command.count(prompt), 1)

    def test_selected_variant_does_not_require_unrelated_legacy_weight(self):
        (self.engine / "runtime/models/diffusion.fixture").unlink()
        _, files, paths, _ = ADAPTER.installed_sources("Q6_K")
        self.assertEqual(paths["diffusion"].name, "Q6_K.gguf")
        self.assertEqual(files["diffusion"]["sha256"], "hash-Q6_K")
        with self.assertRaises(RuntimeError):
            ADAPTER.installed_sources("Q4_K")


if __name__ == "__main__":
    unittest.main(verbosity=2)
