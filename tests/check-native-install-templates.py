"""Fresh-install native adapter contract; no actual weights or GPU. v0.5.0."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/qwen-image-run.py"


class NativeTemplateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TEMPLATE.is_file(), "Fresh-install Qwen adapter must ship with the application")
        spec = importlib.util.spec_from_file_location("qwen_clean_template", TEMPLATE)
        self.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.adapter)
        self.temp = tempfile.TemporaryDirectory(prefix="build-something-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = self.root / "engines/qwen-image-21"
        self.engine.mkdir(parents=True)
        config = self.root / "config"
        config.mkdir()
        self.source = config / "image-model-source.json"
        self.variants = config / "image-variants.json"
        self.files = []
        for role in ("runtime", "diffusion", "textEncoder", "vae"):
            relative = f"runtime/models/{role}.bin"
            path = self.engine / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if role != "diffusion":
                path.write_bytes(role.encode())
            self.files.append({"role": role, "relative": relative, "size": len(role), "name": f"{role}.bin", "sha256": "f" * 64})
        executable = self.engine / "runtime/sd-cli.exe"
        executable.write_bytes(b"fixture")
        self.source.write_text(json.dumps({"runtimeRelease": self.adapter.RELEASE, "files": self.files,
                "runtimeCommit": "fixed", "cli": {"executable": "runtime/sd-cli.exe", "requiredFlags": [
                    "--backend", "--params-backend", "--fa", "--diffusion-model", "--llm", "--vae"]}}), encoding="utf-8")
        weight = self.engine / "runtime/models/Q4_K_M.gguf"
        weight.write_bytes(b"gguf fixture")
        self.variants.write_text(json.dumps({"engineId": "qwen-image-21", "variants": [{"id": "Q4_K_M",
            "bytes": weight.stat().st_size, "path": weight.relative_to(self.root).as_posix(), "status": "installed", "verified": True,
            "pin": {"repo": "fixture", "revision": "fixed", "filename": weight.name, "sha256": "f" * 64, "size": weight.stat().st_size}}]}), encoding="utf-8")
        support = self.root / "runtime/cuda12"
        support.mkdir(parents=True)
        for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll"):
            (support / name).write_bytes(b"fixture")
        self.constants = patch.multiple(self.adapter, ROOT=self.root, ENGINE=self.engine, SOURCE=self.source, VARIANT_SOURCE=self.variants)
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def test_clean_qwen_uses_selected_weight_without_legacy_model_or_historical_receipts(self):
        metadata, files, paths, executable = self.adapter.installed_sources("Q4_K_M")
        self.assertEqual(paths["diffusion"].name, "Q4_K_M.gguf")
        self.assertEqual(files["diffusion"]["sha256"], "f" * 64)
        self.assertTrue(executable.is_file())
        self.assertNotIn("receipts", metadata)
        self.assertFalse((self.root / "engines/hunyuan3d").exists())

    def test_qwen_private_cuda_components_do_not_need_torch(self):
        self.assertEqual(self.adapter.cuda_support_directory(), self.root / "runtime/cuda12")

    def test_qwen_missing_shared_model_refuses_generation(self):
        (self.engine / "runtime/models/vae.bin").unlink()
        with self.assertRaises(RuntimeError):
            self.adapter.installed_sources("Q4_K_M")

    def test_qwen_prompt_stays_a_single_argument(self):
        request = self.adapter.validate_request({"prompt": '中文 "quoted" $(data)', "weightVariant": "Q4_K_M"})
        _, _, paths, executable = self.adapter.installed_sources("Q4_K_M")
        command = self.adapter.build_command(request, paths, executable, self.root / "image.png")
        self.assertEqual(command[command.index("--prompt") + 1], request["prompt"])

    def test_native_launchers_are_bundled_and_use_private_runtime(self):
        for name in ("trellis", "pixal3d", "qwen-image"):
            path = ROOT / f"scripts/templates/{name}-run.ps1"
            self.assertTrue(path.is_file(), str(path))
        text = (ROOT / "scripts/templates/qwen-image-run.ps1").read_text(encoding="utf-8")
        self.assertIn("runtime\\core\\.venv", text)
        self.assertNotIn("hunyuan3d", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
