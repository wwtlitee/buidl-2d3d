"""Read-only portable source/recipe integrity checks. Public edition v0.9.1 / Unclecow.

Inspects sealed source archives and pinned metadata. Does not extract into the
workspace, install dependencies, download models, or execute GPU probes.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unittest
from urllib.parse import urlparse
import warnings
import zipfile

ROOT=Path(__file__).resolve().parents[1]


class PortableRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all_profiles=json.loads((ROOT/"config/python-profiles.json").read_text(encoding="utf-8"))["profiles"]
        cls.profiles=[profile for profile in cls.all_profiles
                      if all((ROOT/item['path']).is_file() for item in profile['bundles'])]
        cls.models=json.loads((ROOT/"config/python-models.json").read_text(encoding="utf-8"))["models"]
        cls.bundles={}
        cls.contents={}
        for profile in cls.profiles:
            contents={}
            for item in profile["bundles"]:
                archive=ROOT/item["path"]
                data=archive.read_bytes()
                cls.bundles[item["path"]]=(item,data)
                with zipfile.ZipFile(archive) as bundle:
                    contents.update({name:bundle.read(name) for name in bundle.namelist()})
            cls.contents[profile["id"]]=contents

    def test_sealed_archive_sizes_hashes_and_no_environment_or_user_outputs(self):
        self.assertEqual({p['id'] for p in self.profiles},{'triposr'})
        for name,(pin,data) in self.bundles.items():
            with self.subTest(bundle=name):
                self.assertEqual(len(data),pin["size"])
                self.assertEqual(hashlib.sha256(data).hexdigest(),pin["sha256"])
        for profile,names in self.contents.items():
            for name in names:
                with self.subTest(profile=profile,file=name):
                    self.assertNotIn("..",PurePosixPath(name).parts)
                    self.assertFalse(PurePosixPath(name).is_absolute())
                    self.assertFalse(set(PurePosixPath(name).parts)&{".venv","__pycache__","inputs","outputs","checks",".git"})
                    self.assertNotIn(PurePosixPath(name).suffix.lower(),{".safetensors",".ckpt",".gguf",".pt",".pth"})

    def test_python_source_parses_and_relative_import_packages_are_present(self):
        for profile,contents in self.contents.items():
            for name,data in contents.items():
                if not name.endswith(".py"):
                    continue
                with self.subTest(profile=profile,file=name),warnings.catch_warnings():
                    warnings.simplefilter("ignore",SyntaxWarning)
                    tree=ast.parse(data.decode("utf-8-sig"),filename=name)
                    for imported in ast.walk(tree):
                        if not isinstance(imported,ast.ImportFrom) or not imported.level or not imported.module:
                            continue
                        directory=PurePosixPath(name).parent
                        for _ in range(imported.level-1):
                            directory=directory.parent
                        target=str(directory.joinpath(*imported.module.split(".")))
                        available=target+".py" in contents or any(path.startswith(target+"/") for path in contents) or any(path.startswith(target+".") and path.endswith(".pyd") for path in contents)
                        self.assertTrue(available,f"Missing relative package {'.'*imported.level+imported.module} from {name}:{imported.lineno}")

    def test_recipe_requirements_probe_scripts_and_abi_are_shipped(self):
        all_names={name for contents in self.contents.values() for name in contents}
        all_model_files={item["path"] for model in self.models for item in model.get("files",[])}
        profiles={profile["id"]:profile for profile in self.profiles}
        for profile in self.profiles:
            with self.subTest(profile=profile["id"]):
                if profile.get("environment"):
                    self.assertIn(profile["requirements"],self.contents[profile["id"]])
                    text=self.contents[profile["id"]][profile["requirements"]].decode("utf-8")
                    self.assertNotRegex(text,r"(?<![A-Za-z])[A-Za-z]:[\\/]")
                    self.assertNotIn(".venv/",text)
                python_abi="".join(profile.get("python","3.11").split(".")[:2])
                for name in self.contents[profile["id"]]:
                    abi=re.search(r"\.cp(\d+)-.*\.pyd$",name)
                    if abi:
                        self.assertEqual(abi[1],python_abi,f"Bundled extension does not match profile Python: {name}")
        for model in self.models:
            if model['deploymentProfile'] not in profiles:
                self.assertIs(model.get('installable'),False,model['id'])
                self.assertEqual(model.get('publicDistribution'),'source-bundle-not-included',model['id'])
                continue
            with self.subTest(model=model["id"]):
                self.assertIn(model["deploymentProfile"],profiles)
                for item in model.get("required",[]):
                    if ".venv/" in item["path"]:
                        continue
                    self.assertTrue(item["path"] in all_names or item["path"] in all_model_files,"Unshipped required component: "+item["path"])
                for probe in model.get("probes",[]):
                    for argument in probe.get("args",[]):
                        if argument.startswith("@root:"):
                            self.assertIn(argument[6:],all_names,"Unshipped probe entry point")

    def test_fixed_download_urls_are_http_encoded(self):
        catalogs=[self.models,json.loads((ROOT/"config/setup-catalog.json").read_text(encoding="utf-8"))["models"]]
        for models in catalogs:
            for model in models:
                for item in model.get("files",[]):
                    for url in [item["url"],*item.get("mirrors",[])]:
                        with self.subTest(model=model["id"],file=item["path"],url=url):
                            self.assertFalse(re.search(r"\s",url),"A pinned filename containing spaces must be URL-encoded before urllib downloads it")
                            self.assertTrue(url.isascii(),"Non-ASCII download paths must be percent-encoded")
                            self.assertEqual(urlparse(url).scheme,"https")

    def test_triposr_tokenizer_uses_the_downloaded_local_dino_configuration(self):
        name="engines/triposr/src/tsr/models/tokenizers/image.py"
        tree=ast.parse(self.contents["triposr"][name].decode("utf-8-sig"),filename=name)
        configure=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=="configure")
        remote_calls=[node for node in ast.walk(configure) if isinstance(node,ast.Call) and
                      (isinstance(node.func,ast.Name) and node.func.id=="hf_hub_download" or
                       isinstance(node.func,ast.Attribute) and node.func.attr=="hf_hub_download")]
        self.assertEqual(remote_calls,[],"An offline fresh install must use its pinned local Dino config, without a hidden Hugging Face cache dependency")
        self.assertTrue(any(isinstance(node,ast.Constant) and node.value=="models/dino/config.json" for node in ast.walk(configure)),"The configuration must resolve the deployed Dino config within the same engine")


if __name__=="__main__":
    unittest.main(verbosity=2)
