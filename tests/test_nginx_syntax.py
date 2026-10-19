"""Optional real-Nginx parser coverage; all configuration and certificates stay in /tmp."""
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
import unittest
import test_config


@unittest.skipUnless(shutil.which('nginx'), 'real Nginx binary is not installed')
class NginxSyntaxTests(unittest.TestCase):
    def test_real_nginx_accepts_generated_host_variants(self):
        with tempfile.TemporaryDirectory(prefix='nginx-manager-syntax-') as tmp:
            root=Path(tmp); cert=root/'cert.pem'; key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                            '-subj','/CN=app.example.com','-keyout',str(key),'-out',str(cert)],
                           check=True,capture_output=True)
            changes=[{},dict(app='react',root=str(root),api=True),
                     dict(streaming=True,websocket=True,buffer='large'),
                     dict(https=True,origin_ip='2606:4700:4700::1111'),
                     dict(protocol='https',host='upstream.example.com')]
            # Avoid DNS resolution during nginx -t for this standalone syntax fixture.
            changes[-1]['host']='127.0.0.1'
            for change in changes:
                with self.subTest(change=change):
                    site=test_config.config.render(test_config.config.validate(test_config.profile(**change)),str(root/'acme'),str(cert),str(key))
                    # nginx -t can bind listeners; keep the fixture on unprivileged loopback ports.
                    with socket.socket() as listener:
                        listener.bind(('127.0.0.1',0)); http_port=listener.getsockname()[1]
                    with socket.socket() as listener:
                        listener.bind(('127.0.0.1',0)); https_port=listener.getsockname()[1]
                    site=site.replace('listen 80;',f'listen 127.0.0.1:{http_port};').replace('listen 443 ssl;',f'listen 127.0.0.1:{https_port} ssl;')
                    site=site.replace('listen [::]:80;',f'listen [::1]:{http_port};').replace('listen [::]:443 ssl;',f'listen [::1]:{https_port} ssl;')
                    text=f'pid {root}/nginx.pid;\nerror_log stderr;\nevents {{}}\nhttp {{\naccess_log off;\nclient_body_temp_path {root}/body;\nproxy_temp_path {root}/proxy;\nfastcgi_temp_path {root}/fastcgi;\nuwsgi_temp_path {root}/uwsgi;\nscgi_temp_path {root}/scgi;\n'+site+'\n}\n'
                    conf=root/'nginx.conf'; conf.write_text(text)
                    proc=subprocess.run(['nginx','-t','-p',str(root)+'/', '-c',str(conf),'-e','stderr'],text=True,capture_output=True)
                    self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
