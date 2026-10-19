import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_manager
ROOT = test_manager.ROOT


class ServiceTests(unittest.TestCase):
    setUp = test_manager.ManagerTests.setUp
    run_manager = test_manager.ManagerTests.run_manager
    deploy = test_manager.ManagerTests.deploy
    assert_ok = test_manager.ManagerTests.assert_ok
    def test_dns_mismatch_blocks_certificate_issuance(self):
        self.profile.write_text(json.dumps(dict(domain='fresh.example.com', app='proxy', https=True, origin_ip='8.8.8.8', email='ops@example.com')))
        dig = self.bin / 'dig'; dig.write_text('#!/bin/bash\nprintf "1.1.1.1\\n"\n'); dig.chmod(0o755)
        certbot = self.bin / 'certbot'; certbot.write_text('#!/bin/bash\ntouch "$TEST_CERTBOT_CALLED"\n'); certbot.chmod(0o755)
        proc = self.deploy(TEST_CERTBOT_CALLED=str(self.d / 'certbot-called'))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.d / 'certbot-called').exists())
        self.assertEqual(self.original, self.conf.read_bytes())

    def test_cloudflare_conflicting_cname_is_not_replaced(self):
        curl = self.bin / 'curl'
        curl.write_text('''#!/usr/bin/env python3
import json,sys
sys.stdin.read()
url=sys.argv[-1]
if 'dns_records' in url:
    print(json.dumps({'success':True,'result':[{'id':'1'*32,'name':'fresh.example.com','type':'CNAME','content':'other.example.com','proxied':False,'ttl':1}],'result_info':{'total_count':1}}))
else:
    print(json.dumps({'success':True,'result':{'id':'0'*32,'name':'example.com'}}))
'''); curl.chmod(0o755)
        proc = self.run_manager('load_profile "$TEST_PROFILE"; SITE[origin_ip]=8.8.8.8; SITE[zone_id]=0123456789abcdef0123456789abcdef; CF_TOKEN=SECRETTOKEN; cf_set_origin_record false',
                                TEST_PROFILE=str(self.profile), CF_FIXTURE='conflict')
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn('SECRETTOKEN', proc.stdout + proc.stderr)
        self.assertIn('conflict', (proc.stdout + proc.stderr).lower())

    def test_token_is_passed_to_curl_stdin_not_argv(self):
        curl = self.bin / 'curl'
        curl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if any('SECRETTOKEN' in arg for arg in sys.argv): raise SystemExit(9)
config=sys.stdin.read()
if 'SECRETTOKEN' not in config: raise SystemExit(8)
Path(os.environ['CURL_SAFE']).write_text('safe')
print(json.dumps({'success':True,'result':{'id':'0123456789abcdef0123456789abcdef'}}))
'''); curl.chmod(0o755)
        proc = self.run_manager('CF_TOKEN=SECRETTOKEN; cf_request GET /zones/0123456789abcdef0123456789abcdef >/dev/null', CURL_SAFE=str(self.d / 'safe'))
        self.assert_ok(proc)
        self.assertTrue((self.d / 'safe').exists())

    def test_filesystem_rollback_preserves_concurrent_edits(self):
        proc = self.run_manager('''acquire_lock
transaction_begin
transaction_write "$TEST_CONF" <<< 'changed'
printf '%s' 'manual concurrent edit' > "$TEST_CONF"
transaction_rollback
''')
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual('manual concurrent edit', self.conf.read_text())


    def setup_tls(self, issuer_exit=0):
        self.profile.write_text(json.dumps(dict(domain='fresh.example.com', app='proxy', https=True, origin_ip='8.8.8.8', email='ops@example.com')))
        self.env['SSL_CERT_FILE']=str(self.d/'letsencrypt/live/fresh.example.com/fullchain.pem')
        dig = self.bin / 'dig'
        dig.write_text('#!/bin/bash\nif [[ $* == *" A "* ]]; then printf "8.8.8.8\\n"; fi\n'); dig.chmod(0o755)
        curl = self.bin / 'curl'
        curl.write_text('''#!/usr/bin/env python3
import sys
url=sys.argv[-1]
print(url.rsplit('/',1)[1] if '/acme-challenge/' in url else '200',end='')
'''); curl.chmod(0o755)
        certbot = self.bin / 'certbot'
        certbot.write_text('''#!/usr/bin/env python3
import os,sys,subprocess
from pathlib import Path
if 'renew' in sys.argv: raise SystemExit(0)
if os.environ.get('ISSUER_EXIT') == '1': raise SystemExit(1)
a=sys.argv; dest=Path(a[a.index('--config-dir')+1])/'live'/a[a.index('--cert-name')+1]
dest.mkdir(parents=True,exist_ok=True)
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','90','-subj','/CN=fresh.example.com','-addext','subjectAltName=DNS:fresh.example.com','-keyout',str(dest/'privkey.pem'),'-out',str(dest/'fullchain.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
'''); certbot.chmod(0o755)

    def test_https_bootstrap_installs_tls_and_preserves_acme_route(self):
        self.setup_tls()
        proc = self.run_manager('deploy_profile "$TEST_PROFILE"', input='y\n'*15, TEST_PROFILE=str(self.profile), CERTBOT_DIR=str(self.d/'letsencrypt'))
        self.assert_ok(proc)
        text=(self.d/'nginx-manager.d/fresh.example.com.conf').read_text()
        self.assertIn('listen 443 ssl;',text)
        self.assertIn('return 301 https://$host$request_uri;',text)
        self.assertIn('location ^~ /.well-known/acme-challenge/',text)
        self.assertTrue((self.d/'letsencrypt/renewal-hooks/deploy/nginx-manager-reload').exists())

    def test_failed_issuer_restores_prebootstrap_configuration(self):
        self.setup_tls()
        proc = self.run_manager('deploy_profile "$TEST_PROFILE"', input='y\n'*15, TEST_PROFILE=str(self.profile), CERTBOT_DIR=str(self.d/'letsencrypt'), ISSUER_EXIT='1')
        self.assertNotEqual(proc.returncode,0)
        self.assertEqual(self.original,self.conf.read_bytes())
        self.assertFalse((self.d/'nginx-manager.d/fresh.example.com.conf').exists())


    def test_acme_symlink_cannot_change_sensitive_directory_permissions(self):
        self.setup_tls()
        private=self.d/'private'; private.mkdir(mode=0o700)
        (self.d/'acme-link').symlink_to(private,target_is_directory=True)
        proc=self.run_manager('load_profile "$TEST_PROFILE"; challenge_preflight',input='y\n',TEST_PROFILE=str(self.profile),ACME_ROOT=str(self.d/'acme-link'))
        self.assertNotEqual(proc.returncode,0)
        self.assertEqual(private.stat().st_mode & 0o777,0o700)
        self.assertFalse((private/'.well-known').exists())

    def test_recovery_works_when_nginx_dump_is_invalid(self):
        proc=self.run_manager('''acquire_lock
transaction_begin
transaction_write "$TEST_CONF" <<< 'invalid nginx config'
printf '%s' "$TX" > "$MANIFEST_PATH"
TX=''
''',MANIFEST_PATH=str(self.d/'manifest'))
        self.assert_ok(proc)
        proc=self.run_manager('recover_transaction "$MANIFEST"',MANIFEST=(self.d/'manifest').read_text(),FAIL_DUMP='1')
        self.assert_ok(proc)
        self.assertEqual(self.original,self.conf.read_bytes())


    def test_standalone_ssl_restores_requested_cloudflare_proxy_mode(self):
        self.assert_ok(self.deploy())
        # DNS/certificate functions stand in for external API and issuer state.
        script='''configure_dns() { printf false > "$DNS_MODE"; }
configure_ssl() { return 0; }
cf_set_origin_record() { printf '%s' "$1" > "$DNS_MODE"; }
load_profile "$TEST_PROFILE"
SITE[cloudflare]=api; SITE[proxy_mode]=true; SITE[email]=ops@example.com; SITE[origin_ip]=8.8.8.8
site_json > "$STATE_DIR/profiles/fresh.example.com.conf"
ssl_site fresh.example.com
'''
        self.assert_ok(self.run_manager(script,TEST_PROFILE=str(self.profile),DNS_MODE=str(self.d/'dns-mode')))
        self.assertEqual((self.d/'dns-mode').read_text(),'true')


    def test_acme_new_directories_allow_workers_under_private_umask(self):
        proc=self.run_manager('umask 077; fs acme-token "$ACME_ROOT" 0123456789abcdef0123456789abcdef')
        self.assert_ok(proc)
        self.assertEqual((self.d/'nginx-manager-acme').stat().st_mode & 0o777,0o755)


    def test_untrusted_certificate_is_not_treated_as_valid_origin_certificate(self):
        dest=self.d/'letsencrypt/live/fresh.example.com'; dest.mkdir(parents=True)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=fresh.example.com','-addext','subjectAltName=DNS:fresh.example.com','-keyout',str(dest/'privkey.pem'),'-out',str(dest/'fullchain.pem')],check=True,capture_output=True)
        proc=self.run_manager('load_profile "$TEST_PROFILE"; find_certificate',TEST_PROFILE=str(self.profile),CERTBOT_DIR=str(self.d/'letsencrypt'))
        self.assertNotEqual(proc.returncode,0)


    def test_same_profile_update_refreshes_changed_trusted_proxy_ranges(self):
        p=json.loads(self.profile.read_text()); p['real_ip']=True; self.profile.write_text(json.dumps(p))
        curl=self.bin/'curl'
        curl.write_text('''#!/bin/bash
case "${!#}" in
    *ips-v4) printf '%s\\n' "${TEST_CF_RANGE:-173.245.48.0/20}" ;;
    *ips-v6) printf '%s\\n' '2400:cb00::/32' ;;
    *) printf 200 ;;
esac
'''); curl.chmod(0o755)
        self.assert_ok(self.deploy())
        self.assert_ok(self.run_manager('deploy_profile "$TEST_PROFILE" update',TEST_PROFILE=str(self.profile),TEST_CF_RANGE='103.21.244.0/22'))
        self.assertIn('set_real_ip_from 103.21.244.0/22;',(self.d/'nginx-manager.d/fresh.example.com.conf').read_text())


if __name__ == '__main__':
    unittest.main()
