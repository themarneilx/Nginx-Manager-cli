import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_manager
import test_os
import test_config

ROOT=test_manager.ROOT


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='nginx-manager-ca-'); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.cert=self.root/'ca.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=Test CA','-keyout',str(self.root/'key.pem'),'-out',str(self.cert)],check=True,capture_output=True)

    def test_trust_store_selection_uses_available_bundle_and_validates_override(self):
        self.assertEqual(test_os.host.ca_bundle(candidates=[str(self.root/'missing'),str(self.cert)]),str(self.cert))
        self.assertEqual(test_os.host.ca_bundle(str(self.cert)),str(self.cert))
        for invalid in [str(self.root/'missing'),'/tmp/ca;include.conf']:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError): test_os.host.ca_bundle(invalid)
        invalid=self.root/'empty'; invalid.touch()
        with self.assertRaises(ValueError): test_os.host.ca_bundle(str(invalid))

    def test_verified_https_upstream_renders_selected_bundle(self):
        p=test_config.config.validate(test_config.profile(protocol='https'))
        rendered=test_config.config.render(p,str(self.root/'acme'),ca_bundle=str(self.cert))
        self.assertIn(f'proxy_ssl_trusted_certificate {self.cert};',rendered)
        self.assertIn('proxy_ssl_verify on;',rendered)


class RenewalTests(unittest.TestCase):
    setUp=test_manager.ManagerTests.setUp
    run_manager=test_manager.ManagerTests.run_manager
    assert_ok=test_manager.ManagerTests.assert_ok

    def fake_systemctl(self,units,enabled=(),states=None):
        ctl=self.bin/'systemctl'
        ctl.write_text('''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
a=sys.argv[1:]; units=json.loads(os.environ['TIMER_UNITS']); enabled=json.loads(os.environ['ENABLED_TIMERS']); states=json.loads(os.environ['TIMER_STATES'])
if a[0]=='list-unit-files':
    print(''.join(x+' disabled enabled\\n' for x in units),end='')
elif a[0]=='show': print(states.get(a[-1], 'enabled' if a[-1] in enabled else 'disabled') if '--property=UnitFileState' in a else ('loaded' if a[-1] in units else 'not-found'))
elif a[0]=='is-enabled': raise SystemExit(0 if a[-1] in enabled or states.get(a[-1]) in {'static','indirect'} else 1)
elif a[0]=='is-active': raise SystemExit(1)
elif a[0]=='enable': Path(os.environ['TIMER_MUTATIONS']).write_text(' '.join(a))
else: raise SystemExit(8)
'''); ctl.chmod(0o755)
        return dict(TIMER_UNITS=json.dumps(units),ENABLED_TIMERS=json.dumps(list(enabled)),TIMER_STATES=json.dumps(states or {}),TIMER_MUTATIONS=str(self.d/'timer-change'))

    def test_installed_debian_timer_is_enabled_after_confirmation(self):
        env=self.fake_systemctl(['certbot.timer'])
        self.assert_ok(self.run_manager('configure_renewal_timer',**env))
        self.assertEqual((self.d/'timer-change').read_text(),'enable --now certbot.timer')

    def test_existing_enabled_scheduler_is_preferred_over_another_disabled_timer(self):
        env=self.fake_systemctl(['certbot-renew.timer','certbot.timer'],['certbot.timer'])
        self.assert_ok(self.run_manager('configure_renewal_timer',**env))
        self.assertFalse((self.d/'timer-change').exists())

    def test_missing_declined_and_dry_run_timers_never_mutate(self):
        for units,answer,dry in [([], 'y\n','false'),(['certbot.timer'],'n\n','false'),(['certbot.timer'],'y\n','true')]:
            env=self.fake_systemctl(units)
            self.assert_ok(self.run_manager('configure_renewal_timer',input=answer,DRY_RUN=dry,**env))
            self.assertFalse((self.d/'timer-change').exists())

    def test_explicit_missing_timer_is_refused(self):
        env=self.fake_systemctl([])
        proc=self.run_manager('RENEWAL_TIMER=missing.timer; configure_renewal_timer',**env)
        self.assertNotEqual(proc.returncode,0)
        self.assertFalse((self.d/'timer-change').exists())

    def test_renewal_hook_uses_detected_systemctl_path(self):
        proc=self.run_manager('acquire_lock; install_renewal_hook',CERTBOT_DIR=str(self.d/'letsencrypt'))
        self.assert_ok(proc)
        content=(self.d/'letsencrypt/renewal-hooks/deploy/nginx-manager-reload').read_text()
        self.assertIn(str(self.bin/'systemctl'),content)


    def test_inactive_static_timer_does_not_mask_a_usable_disabled_timer(self):
        env=self.fake_systemctl(['certbot-static.timer','certbot.timer'],states={'certbot-static.timer':'static'})
        self.assert_ok(self.run_manager('configure_renewal_timer',**env))
        self.assertEqual((self.d/'timer-change').read_text(),'enable --now certbot.timer')

    def test_explicit_disabled_timer_cannot_add_a_second_scheduler(self):
        env=self.fake_systemctl(['certbot.timer','snap.certbot.renew.timer'],['snap.certbot.renew.timer'])
        proc=self.run_manager('RENEWAL_TIMER=certbot.timer; configure_renewal_timer',**env)
        self.assertNotEqual(proc.returncode,0)
        self.assertFalse((self.d/'timer-change').exists())
