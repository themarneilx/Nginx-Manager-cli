#!/usr/bin/env python3
"""Read host metadata as data, and select Linux package names and trust stores."""
import json
import os
from pathlib import Path
import re
import shlex
import ssl
import sys

PACKAGES = {
    'arch': ['bash', 'python', 'nginx', 'certbot', 'curl', 'openssl', 'bind',
             'util-linux', 'coreutils', 'findutils', 'ca-certificates'],
    'debian': ['bash', 'python3', 'nginx', 'certbot', 'curl', 'openssl', 'dnsutils',
               'util-linux', 'coreutils', 'findutils', 'ca-certificates'],
    'fedora': ['bash', 'python3', 'nginx', 'certbot', 'curl', 'openssl', 'bind-utils',
               'util-linux', 'coreutils', 'findutils', 'ca-certificates'],
    'rhel': ['bash', 'python3', 'nginx', 'certbot', 'curl', 'openssl', 'bind-utils',
             'util-linux', 'coreutils', 'findutils', 'ca-certificates'],
}
FAMILIES = {'arch': 'arch', 'manjaro': 'arch', 'endeavouros': 'arch',
            'debian': 'debian', 'ubuntu': 'debian', 'linuxmint': 'debian', 'pop': 'debian',
            'fedora': 'fedora', 'rhel': 'rhel', 'centos': 'rhel', 'rocky': 'rhel',
            'almalinux': 'rhel', 'ol': 'rhel'}
MANAGERS = {'arch': 'pacman', 'debian': 'apt-get', 'fedora': 'dnf', 'rhel': 'dnf'}


def detect(primary=None, fallback=None):
    primary = Path(primary or '/etc/os-release')
    fallback = Path(fallback or '/usr/lib/os-release')
    path = primary if primary.exists() else fallback
    data = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if not sep or key not in {'ID', 'ID_LIKE', 'PRETTY_NAME', 'NAME', 'VERSION_ID'}:
                continue
            values = shlex.split(value, comments=True, posix=True)
            if len(values) > 1:
                raise ValueError('Malformed os-release assignment: ' + key)
            data[key] = values[0] if values else ''
    ident = data.get('ID', 'linux')
    relatives = data.get('ID_LIKE', '').split()
    if not all(re.fullmatch(r'[a-z0-9._-]+', x) for x in [ident, *relatives]):
        raise ValueError('Invalid OS identification data')
    family = next((FAMILIES[x] for x in [ident, *relatives] if x in FAMILIES), 'unsupported')
    name = data.get('PRETTY_NAME') or data.get('NAME') or ident
    # Display metadata cannot inject terminal control sequences.
    name = ''.join(c for c in name if c.isprintable())[:200]
    return dict(id=ident, name=name, version=data.get('VERSION_ID', ''), family=family,
                package_manager=MANAGERS.get(family, ''), packages=PACKAGES.get(family, []))


def ca_bundle(explicit='', candidates=None):
    if explicit:
        choices = [explicit]
    elif candidates is not None:
        choices = candidates
    else:
        defaults = ssl.get_default_verify_paths()
        choices = [os.environ.get('SSL_CERT_FILE', ''), defaults.cafile or '',
                   '/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt',
                   '/etc/ssl/cert.pem', '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem']
    for value in choices:
        if not value:
            continue
        path = Path(value)
        if (not path.is_absolute() or '..' in path.parts or
                not re.fullmatch(r'/[a-zA-Z0-9_./-]+', str(path))):
            if explicit:
                raise ValueError('CA bundle path must be absolute and safe for Nginx')
            continue
        if path.is_file() and os.access(path, os.R_OK):
            # Refuse an empty or malformed trust file before referencing it in Nginx.
            try:
                ssl.create_default_context(cafile=str(path))
            except (ssl.SSLError, OSError):
                if explicit:
                    raise ValueError('CA bundle does not contain usable trusted certificates')
                continue
            return str(path)
    raise ValueError('No usable CA bundle found; install ca-certificates or pass --ca-bundle /path')


def main():
    op, *args = sys.argv[1:]
    if op == 'detect':
        print(json.dumps(detect(args[0] if args else None)))
    elif op == 'ca-bundle':
        print(ca_bundle(args[0] if args else ''))
    else:
        raise ValueError('Unknown host operation')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('Host detection error: ' + str(error), file=sys.stderr)
        sys.exit(1)
