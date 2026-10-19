import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ServiceResolutionTests(unittest.TestCase):
    def resolve(self, options, directory=None):
        script=Path(directory or ROOT/'lib')/'service.py'
        executable=sys.executable
        service=f'{{ path={executable} ; argv[]={executable} {options} ; ignore_errors=no ; }}'
        return subprocess.run([sys.executable,str(script),'',executable,'configure arguments: --conf-path=/etc/nginx/nginx.conf --prefix=/usr'],input=service,text=True,capture_output=True)

    def test_attached_configuration_and_prefix_options_match_actual_service(self):
        proc=self.resolve('-p/srv/nginx -cconf/custom.conf')
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(json.loads(proc.stdout),dict(config='/srv/nginx/conf/custom.conf',prefix='/srv/nginx'))

    def test_attached_global_includes_are_refused(self):
        self.assertNotEqual(self.resolve('-ginclude /other/nginx.conf;').returncode,0)

    def test_discovery_helper_does_not_create_bytecode_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ['service.py','config.py','host.py']:
                shutil.copyfile(ROOT/'lib'/name,Path(tmp)/name)
            proc=self.resolve('',tmp)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertFalse((Path(tmp)/'__pycache__').exists())
