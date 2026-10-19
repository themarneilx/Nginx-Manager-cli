import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nginx-manager-test-')
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.conf = self.d / 'nginx.conf'
        self.conf.write_text('events {}\nhttp { server { listen 127.0.0.1:8080; server_name localhost; } }\n')
        self.original = self.conf.read_bytes()
        self.bin = self.d / 'bin'; self.bin.mkdir()
        fake = self.bin / 'nginx'
        fake.write_text('''#!/usr/bin/env python3
import glob, os, sys
from pathlib import Path
if '-V' in sys.argv:
    print('configure arguments: --conf-path=' + os.environ['TEST_CONF'], file=sys.stderr); sys.exit(0)
if '-T' in sys.argv:
    if os.environ.get('FAIL_DUMP') == '1': sys.exit(1)
    files = [os.environ['TEST_CONF']] + glob.glob(str(Path(os.environ['TEST_CONF']).parent / 'nginx-manager.d/*.conf'))
    if os.environ.get('EXTRA_CONF_PATH'): files.append(os.environ['EXTRA_CONF_PATH'])
    for path in files:
        print('# configuration file ' + path + ':')
        print(Path(path).read_text())
    sys.exit(0)
if '-t' in sys.argv:
    sys.exit(1 if os.environ.get('FAIL_TEST') == '1' else 0)
sys.exit(9)
'''); fake.chmod(0o755)
        systemctl = self.bin / 'systemctl'
        systemctl.write_text('''#!/bin/bash
if [[ $1 == list-unit-files ]]; then printf 'certbot-renew.timer enabled enabled\\n'; exit 0; fi
if [[ $1 == show && $2 == --property=LoadState ]]; then
    if [[ ${!#} == certbot-renew.timer ]]; then printf loaded; else printf not-found; fi
    exit 0
fi
if [[ $1 == show ]]; then printf '%s' "${SERVICE_EXECSTART:-{ path=$(command -v nginx) ; argv[]=$(command -v nginx) -c $TEST_CONF ; }}"; exit 0; fi
printf '%s\\n' "$*" >> "$TEST_CALLS"
if [[ $1 == reload && ${FAIL_RELOAD:-0} == 1 ]]; then exit 1; fi
exit 0
'''); systemctl.chmod(0o755)
        curl = self.bin / 'curl'
        curl.write_text('#!/bin/bash\nprintf 200\n'); curl.chmod(0o755)
        self.profile = self.d / 'input.conf'
        self.profile.write_text(json.dumps(dict(domain='fresh.example.com', app='proxy', host='127.0.0.1', port=8000)))
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        TEST_CONF=str(self.conf), TEST_CALLS=str(self.d / 'calls'))

    def run_manager(self, script, input='y\ny\ny\ny\n', **env):
        prelude = '''set -Eeuo pipefail
MANAGER_DIR=$1
NGINX_CONF=$2
STATE_DIR=$3
NGINX_BIN=nginx
for module in common os validation backups nginx certbot cloudflare diagnostics prompts; do source "$MANAGER_DIR/lib/$module.sh"; done
init_defaults
'''
        return subprocess.run(['bash', '-c', prelude + script, 'test', str(ROOT), str(self.conf), str(self.d / 'state')],
                              env={**self.env, **env}, input=input, text=True, capture_output=True)

    def deploy(self, **env):
        return self.run_manager('deploy_profile "$TEST_PROFILE"', TEST_PROFILE=str(self.profile), **env)

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_deploy_is_idempotent_and_preserves_manual_listener(self):
        self.assert_ok(self.deploy())
        site = self.d / 'nginx-manager.d/fresh.example.com.conf'
        self.assertTrue(site.exists())
        self.assertIn('127.0.0.1:8080', self.conf.read_text())
        count = (self.d / 'calls').read_text().count('reload nginx')
        self.assert_ok(self.deploy())
        self.assertEqual(count, (self.d / 'calls').read_text().count('reload nginx'))

    def test_dry_run_has_no_filesystem_or_service_changes(self):
        before = sorted(str(p.relative_to(self.d)) for p in self.d.rglob('*'))
        self.assert_ok(self.deploy(DRY_RUN='true'))
        self.assertEqual(self.original, self.conf.read_bytes())
        self.assertEqual(before, sorted(str(p.relative_to(self.d)) for p in self.d.rglob('*')))

    def test_declined_preview_makes_no_changes(self):
        proc = self.run_manager('deploy_profile "$TEST_PROFILE"', input='n\n', TEST_PROFILE=str(self.profile))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.original, self.conf.read_bytes())
        self.assertFalse((self.d / 'state').exists())

    def test_manual_domain_and_wildcard_are_refused(self):
        for name in ['fresh.example.com', '*.example.com']:
            self.conf.write_text('events {} http { server { server_name ' + name + '; } }')
            proc = self.deploy()
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((self.d / 'nginx-manager.d/fresh.example.com.conf').exists())

    def test_failed_nginx_validation_rolls_back_touched_files(self):
        proc = self.deploy(FAIL_TEST='1')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.original, self.conf.read_bytes())
        self.assertFalse((self.d / 'nginx-manager.d/fresh.example.com.conf').exists())
        self.assertFalse((self.d / 'calls').exists())

    def test_failed_reload_rolls_back_without_deleting_certificates(self):
        proc = self.deploy(FAIL_RELOAD='1')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.original, self.conf.read_bytes())
        self.assertFalse((self.d / 'nginx-manager.d/fresh.example.com.conf').exists())

    def test_disable_enable_remove_and_manual_edit_protection(self):
        self.assert_ok(self.deploy())
        site = self.d / 'nginx-manager.d/fresh.example.com.conf'
        self.assert_ok(self.run_manager('site_action disable fresh.example.com'))
        self.assertFalse(site.exists())
        self.assert_ok(self.run_manager('site_action enable fresh.example.com'))
        self.assertTrue(site.exists())
        site.write_text(site.read_text() + '# edited manually\n')
        proc = self.run_manager('site_action remove fresh.example.com')
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(site.exists())

    def test_backup_restore_returns_previous_configuration(self):
        self.assert_ok(self.deploy())
        self.assert_ok(self.run_manager('backup_configuration'))
        backups = sorted((self.d / 'state/backups').glob('backup-*'))
        self.assertTrue(backups)
        site = self.d / 'nginx-manager.d/fresh.example.com.conf'
        previous = site.read_bytes()
        self.assert_ok(self.run_manager('site_action disable fresh.example.com'))
        self.assert_ok(self.run_manager('restore_backup "$BACKUP"', BACKUP=str(backups[-1])))
        self.assertEqual(previous, site.read_bytes())
        self.assert_ok(self.run_manager('site_action disable fresh.example.com'))

    def test_cli_help_and_render_do_not_require_root(self):
        proc = subprocess.run(['bash', str(ROOT / 'nginx-manager.sh'), '--help'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = subprocess.run(['bash', str(ROOT / 'nginx-manager.sh'), 'render', '--profile', str(self.profile)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('proxy_pass http://127.0.0.1:8000;', proc.stdout)


    def test_wizard_keeps_terminal_input_available_for_confirmation(self):
        dig=self.bin/'dig'; dig.write_text('#!/bin/bash\nprintf "8.8.8.8\\n"\n'); dig.chmod(0o755)
        # Domain, hosting, origin, DNS confirmation, app, host, port, protocol,
        # API, WebSocket, streaming, timeout, buffers, body, CF, HTTPS, deployment.
        answers='fresh.example.com\n1\n8.8.8.8\ny\n7\n127.0.0.1\n8000\nhttp\nn\nn\nn\n60\nauth\n10m\nn\nn\ny\n'
        proc=self.run_manager('site_wizard', input=answers)
        self.assert_ok(proc)
        self.assertTrue((self.d/'nginx-manager.d/fresh.example.com.conf').exists())

    def test_invalid_https_type_is_rejected_without_changes(self):
        p=json.loads(self.profile.read_text()); p['https']='false'; self.profile.write_text(json.dumps(p))
        self.assertNotEqual(self.deploy().returncode,0)
        self.assertFalse((self.d/'state').exists())


    def test_disabled_updates_are_refused_without_reactivation(self):
        self.assert_ok(self.deploy())
        self.assert_ok(self.run_manager('site_action disable fresh.example.com'))
        p=json.loads(self.profile.read_text()); p['port']=8100; self.profile.write_text(json.dumps(p))
        proc=self.run_manager('deploy_profile "$TEST_PROFILE" update',TEST_PROFILE=str(self.profile))
        self.assertNotEqual(proc.returncode,0)
        self.assertFalse((self.d/'nginx-manager.d/fresh.example.com.conf').exists())
        self.assertTrue((self.d/'nginx-manager.d/fresh.example.com.conf.disabled').exists())

    def test_service_configuration_mismatch_is_refused(self):
        proc=self.deploy(SERVICE_EXECSTART='{ path=/usr/bin/nginx ; argv[]=/usr/bin/nginx -c /etc/another/nginx.conf ; }')
        self.assertNotEqual(proc.returncode,0)
        self.assertFalse((self.d/'state').exists())

    def test_restore_can_repair_invalid_configuration(self):
        self.assert_ok(self.deploy())
        self.assert_ok(self.run_manager('backup_configuration'))
        backup=sorted((self.d/'state/backups').glob('backup-*'))[-1]
        self.conf.write_text('invalid config')
        self.assert_ok(self.run_manager('restore_backup "$BACKUP"',BACKUP=str(backup),FAIL_DUMP='1'))
        self.assertIn('http {',self.conf.read_text())


    def test_static_deploy_rejects_unreadable_worker_root(self):
        root=self.d/'www'; root.mkdir(); (root/'index.html').write_text('test')
        self.profile.write_text(json.dumps(dict(domain='fresh.example.com',app='static',root=str(root))))
        runuser=self.bin/'runuser'; runuser.write_text('#!/bin/bash\nexit 1\n'); runuser.chmod(0o755)
        proc=self.deploy()
        self.assertNotEqual(proc.returncode,0)
        self.assertFalse((self.d/'state').exists())


if __name__ == '__main__':
    unittest.main()
