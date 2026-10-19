import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_manager

ROOT=test_manager.ROOT
MODULE=ROOT/'lib/host.py'
spec=importlib.util.spec_from_file_location('host_adapter',MODULE)
host=importlib.util.module_from_spec(spec)
if MODULE.exists(): spec.loader.exec_module(host)


class HostTests(unittest.TestCase):
    def test_os_families_follow_id_then_ordered_id_like(self):
        cases=[('arch','','arch','pacman'),('manjaro','arch','arch','pacman'),
               ('debian','','debian','apt-get'),('ubuntu','debian','debian','apt-get'),
               ('linuxmint','ubuntu debian','debian','apt-get'),('fedora','','fedora','dnf'),
               ('rocky','rhel centos fedora','rhel','dnf'),('almalinux','rhel fedora','rhel','dnf'),
               ('custom','debian arch','debian','apt-get'),('alpine','','unsupported','')]
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'os-release'
            for ident,like,family,manager in cases:
                with self.subTest(ident=ident):
                    f.write_text(f'ID={ident}\nID_LIKE="{like}"\nPRETTY_NAME="Example Linux"\n')
                    info=host.detect(f)
                    self.assertEqual(info['family'],family)
                    self.assertEqual(info['package_manager'],manager)

    def test_release_contents_are_never_executed(self):
        with tempfile.TemporaryDirectory() as d:
            marker=Path(d)/'owned'; f=Path(d)/'os-release'
            f.write_text(f'ID=ubuntu\nPRETTY_NAME="$(touch {marker})"\nHOME_URL=`touch {marker}`\n')
            info=host.detect(f)
            self.assertEqual(info['family'],'debian')
            self.assertFalse(marker.exists())
            self.assertNotIn('\x1b',info['name'])

    def test_primary_release_file_precedes_vendor_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            primary=Path(d)/'etc-release'; fallback=Path(d)/'vendor-release'
            fallback.write_text('ID=debian\n')
            self.assertEqual(host.detect(primary,fallback)['family'],'debian')
            primary.write_text('ID=arch\n')
            self.assertEqual(host.detect(primary,fallback)['family'],'arch')

    def test_package_lists_use_each_distros_python_and_dns_packages(self):
        for family,python,dns in [('arch','python','bind'),('debian','python3','dnsutils'),('fedora','python3','bind-utils'),('rhel','python3','bind-utils')]:
            with self.subTest(family=family):
                packages=host.PACKAGES[family]
                self.assertIn(python,packages); self.assertIn(dns,packages)
                self.assertIn('ca-certificates',packages)
                self.assertNotIn('epel-release',packages)


class InstallerTests(unittest.TestCase):
    setUp=test_manager.ManagerTests.setUp
    run_manager=test_manager.ManagerTests.run_manager
    assert_ok=test_manager.ManagerTests.assert_ok

    def install(self,ident,answer='y\n',dry_run='false'):
        release=self.d/'os-release'; release.write_text(f'ID={ident}\n')
        for manager in ['pacman','apt-get','dnf','yum']:
            binary=self.bin/manager
            binary.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$INSTALL_CALLS"\n')
            binary.chmod(0o755)
        return self.run_manager('require_root() { :; }; detect_os; install_dependencies',input=answer,
                                OS_RELEASE_FILE=str(release),INSTALL_CALLS=str(self.d/'installed'),DRY_RUN=dry_run)

    def test_correct_installer_is_selected_without_cross_family_commands(self):
        for ident,flag,dns in [('arch','-S','bind'),('ubuntu','install','dnsutils'),('fedora','install','bind-utils'),('rocky','install','bind-utils')]:
            with self.subTest(ident=ident):
                calls=self.d/'installed'; calls.unlink(missing_ok=True)
                proc=self.install(ident); self.assert_ok(proc)
                args=calls.read_text().splitlines()
                self.assertEqual(args[0],flag); self.assertIn(dns,args)
                self.assertNotIn('epel-release',args)

    def test_decline_dry_run_and_unknown_os_never_install(self):
        for ident,answer,dry in [('arch','n\n','false'),('ubuntu','y\n','true'),('alpine','y\n','false')]:
            with self.subTest(ident=ident,dry=dry):
                proc=self.install(ident,answer,dry)
                self.assertFalse((self.d/'installed').exists())
                if dry=='true': self.assert_ok(proc)
                else: self.assertNotEqual(proc.returncode,0)

    def test_os_info_is_non_root_and_has_no_state_writes(self):
        release=self.d/'os-release'; release.write_text('ID=ubuntu\nPRETTY_NAME="Ubuntu Test"\n')
        proc=subprocess.run(['bash',str(ROOT/'nginx-manager.sh'),'os-info','--state-dir',str(self.d/'state')],
                            env={**self.env,'OS_RELEASE_FILE':str(release)},text=True,capture_output=True)
        self.assert_ok(proc)
        self.assertIn('Ubuntu Test',proc.stdout)
        self.assertFalse((self.d/'state').exists())


    def test_debian_style_site_symlink_is_backed_up_without_replacing_the_link(self):
        available=self.d/'sites-available'; enabled=self.d/'sites-enabled'
        available.mkdir(); enabled.mkdir()
        source=available/'legacy'; source.write_text('server { listen 80; server_name legacy.example.com; }')
        link=enabled/'legacy'; link.symlink_to(source)
        proc=self.run_manager('backup_configuration',EXTRA_CONF_PATH=str(link))
        self.assert_ok(proc)
        backup=next((self.d/'state/backups').glob('backup-*'))
        source.write_text('server { listen 80; server_name edited.example.com; }')
        self.assert_ok(self.run_manager('restore_backup "$BACKUP"',BACKUP=str(backup)))
        self.assertTrue(link.is_symlink())
        self.assertIn('legacy.example.com',source.read_text())
