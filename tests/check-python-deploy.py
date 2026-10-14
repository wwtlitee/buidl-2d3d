"""Python deployment safeguards; no package/model downloads. v0.5.0."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import os
import subprocess
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.find_spec('factory_deploy')
deploy=importlib.util.module_from_spec(spec) if spec else None
if spec: spec.loader.exec_module(deploy)


class PythonDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(deploy, 'Python model deployment recipe runner must exist')
        self.temp=tempfile.TemporaryDirectory(prefix='build-something-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def bundle(self, name='engines/fixture/run.py'):
        path=self.root/'bundle.zip'
        with zipfile.ZipFile(path,'w') as archive: archive.writestr(name,'print("fixture")\n')
        return {'path':'bundle.zip','size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_source_bundle_installs_without_python_for_builtin_profile(self):
        profile={'id':'fixture','bundles':[self.bundle()]}
        deploy.deploy_profile(self.root,'fixture',profiles={'profiles':[profile]})
        self.assertTrue((self.root/'engines/fixture/run.py').exists())

    def test_tampered_bundle_never_extracts(self):
        item=self.bundle();item['sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'SHA256'):
            deploy.deploy_profile(self.root,'fixture',profiles={'profiles':[{'id':'fixture','bundles':[item]}]})
        self.assertFalse((self.root/'engines').exists())

    def test_reviewed_source_update_preserves_previous_file(self):
        item=self.bundle()
        target=self.root/'engines/fixture/run.py'
        target.parent.mkdir(parents=True)
        target.write_text('old source',encoding='utf-8')
        deploy.deploy_profile(self.root,'fixture',profiles={'profiles':[{'id':'fixture','bundles':[item]}]})
        self.assertIn('fixture',target.read_text(encoding='utf-8'))
        backups=list(target.parent.glob('run.py.before-0.5.0-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'),'old source')

    def test_archive_traversal_is_rejected(self):
        item=self.bundle('../outside.py')
        with self.assertRaisesRegex(ValueError,'路径'):
            deploy.deploy_profile(self.root,'fixture',profiles={'profiles':[{'id':'fixture','bundles':[item]}]})

    def test_unknown_profile_cannot_execute_commands(self):
        with self.assertRaisesRegex(ValueError,'部署配方'):
            deploy.deploy_profile(self.root,'../../outside',profiles={'profiles':[]})

    def test_cancelled_deployment_does_not_extract(self):
        item=self.bundle()
        with self.assertRaisesRegex(RuntimeError,'取消'):
            deploy.deploy_profile(self.root,'fixture',profiles={'profiles':[{'id':'fixture','bundles':[item]}]},cancel=lambda:True)
        self.assertFalse((self.root/'engines').exists())

    @unittest.skipUnless(os.name=='nt','Windows owned process-tree cleanup')
    def test_callback_failure_stops_owned_child_process(self):
        marker=self.root/'child-finished.txt'
        child_pid=self.root/'child.pid'
        child=self.root/'child.py'
        child.write_text('import os,time\nfrom pathlib import Path\nPath('+repr(str(child_pid))+').write_text(str(os.getpid()))\ntime.sleep(.8)\nPath('+repr(str(marker))+').write_text("should not finish")\n',encoding='utf-8')
        parent=self.root/'parent.py'
        parent.write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,'+repr(str(child))+'])\nprint("fixture child started",flush=True)\ntime.sleep(8)\n',encoding='utf-8')
        def reject_progress(_):raise RuntimeError('fixture progress callback failed')
        try:
            with self.assertRaisesRegex(RuntimeError,'callback failed'):
                deploy.run_command([sys.executable,str(parent)],self.root,os.environ.copy(),reject_progress,lambda:False)
            time.sleep(1)
            self.assertFalse(marker.exists(),'A failed installer callback must not leave its compiler/download child running')
        finally:
            if child_pid.exists():
                subprocess.run(['taskkill','/PID',child_pid.read_text(),'/T','/F'],capture_output=True,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


if __name__=='__main__': unittest.main(verbosity=2)
