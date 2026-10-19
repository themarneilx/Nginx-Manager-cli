#!/usr/bin/env python3
"""Data validation and Nginx lexical operations; profiles are never executable."""
import argparse
import fnmatch
import hashlib
import ipaddress
import json
import os
import re
import shlex
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'
APPS = {'nextjs', 'node', 'react', 'angular', 'static', 'api', 'proxy', 'custom'}
DEFAULTS = dict(origin_ip='', app='proxy', hosting='local', host='127.0.0.1', port=8080,
                protocol='http', root='', spa=False, api=False, api_prefix='/api/',
                api_host='127.0.0.1', api_port=3000, api_protocol='http',
                websocket=False, streaming=False, buffer='auth', timeout=60,
                body_size='10m', https=False, email='', cloudflare='none',
                proxy_mode=False, zone_id='', real_ip=False, custom_file='', dns_confirmed=False)


def domain(value):
    if not isinstance(value, str) or len(value) > 253 or '.' not in value:
        return False
    return all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x)
               for x in value.split('.')) and not value.replace('.', '').isdigit()


def host(value):
    if not isinstance(value, str):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return value == 'localhost' or domain(value)


def safe_path(value, allow_empty=False):
    return isinstance(value, str) and ((allow_empty and value == '') or
           (value.startswith('/') and re.fullmatch(r'/[a-zA-Z0-9_./-]+', value)
            and '..' not in value.split('/') and '//' not in value))


def validate(raw):
    if not isinstance(raw, dict) or set(raw) - (set(DEFAULTS) | {'domain'}):
        raise ValueError('Unknown profile fields; credentials do not belong in profiles')
    p = {**DEFAULTS, **raw}
    for name, default in DEFAULTS.items():
        if isinstance(default, str) and not isinstance(p[name], str):
            raise ValueError(f'{name} must be a JSON string')
    if not domain(p.get('domain')):
        raise ValueError('Use a lowercase ASCII domain with at least two labels (punycode for IDNs)')
    if p['app'] not in APPS or p['hosting'] not in {'local', 'remote'}:
        raise ValueError('Invalid application or hosting selection')
    for name in ['https', 'spa', 'api', 'websocket', 'streaming', 'proxy_mode', 'real_ip', 'dns_confirmed']:
        if type(p[name]) is not bool:
            raise ValueError(f'{name} must be a JSON boolean')
    for name in ['port', 'api_port', 'timeout']:
        if type(p[name]) is not int or not 1 <= p[name] <= (86400 if name == 'timeout' else 65535):
            raise ValueError(f'Invalid {name}')
    for name in ['host', 'api_host']:
        if not host(p[name]):
            raise ValueError(f'Invalid {name}; no URLs or Nginx syntax allowed')
    if p['protocol'] not in {'http', 'https'} or p['api_protocol'] not in {'http', 'https'}:
        raise ValueError('Upstream protocol must be http or https')
    if p['buffer'] not in {'standard', 'auth', 'large'}:
        raise ValueError('Buffer profile must be standard, auth or large')
    if not isinstance(p['body_size'], str) or not re.fullmatch(r'[1-9][0-9]{0,5}[km]?', p['body_size']):
        raise ValueError('Body limit must be a positive size, e.g. 10m; unlimited is not supported')
    if not safe_path(p['root'], True) or not safe_path(p['custom_file'], True):
        raise ValueError('Paths must be absolute with only letters, numbers, dot, slash, dash, underscore')
    if p['app'] in {'react', 'angular', 'static'} and not p['root']:
        raise ValueError('Static applications require an absolute web root')
    if p['app'] == 'custom' and not p['custom_file']:
        raise ValueError('Custom application requires custom_file')
    if not isinstance(p['api_prefix'], str) or not re.fullmatch(r'/[a-zA-Z0-9_/-]+/', p['api_prefix']):
        raise ValueError('API prefix must start and end with /, e.g. /api/')
    if p['origin_ip']:
        try:
            addr = ipaddress.ip_address(p['origin_ip'])
        except ValueError as exc:
            raise ValueError('Invalid DNS origin IP') from exc
        if not addr.is_global:
            raise ValueError('DNS origin must be a globally routable IP address')
    if not isinstance(p['email'], str) or (p['email'] and not re.fullmatch(r'[a-zA-Z0-9.!+_-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}', p['email'])):
        raise ValueError('Invalid contact email')
    if p['cloudflare'] not in {'none', 'manual', 'api'}:
        raise ValueError('Invalid Cloudflare mode')
    if not isinstance(p['zone_id'], str) or (p['zone_id'] and not re.fullmatch(r'[a-f0-9]{32}', p['zone_id'])):
        raise ValueError('Invalid Cloudflare zone ID')
    return p


def load(path):
    try:
        return validate(json.loads(Path(path).read_text()))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError('Profile must be JSON data, never shell code') from exc


def tokens(text):
    """Yield (decoded token, byte start, byte end); quotes/comments hide braces."""
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == '#':
            j = text.find('\n', i)
            i = len(text) if j < 0 else j + 1
            continue
        start = i
        if text[i] in '{};':
            yield text[i], i, i + 1
            i += 1
            continue
        value = ''
        quote = ''
        while i < len(text):
            c = text[i]
            if c == '\\' and i + 1 < len(text):
                value += text[i + 1]; i += 2; continue
            if quote:
                if c == quote:
                    quote = ''
                else:
                    value += c
                i += 1
            elif c in '\"\'':
                quote = c; i += 1
            elif c.isspace() or c in '{};#':
                break
            else:
                value += c; i += 1
        if quote:
            raise ValueError('Unterminated Nginx quote')
        yield value, start, i


def directives(text):
    context = []
    args = []
    for token, start, end in tokens(text):
        if token == '{' and text[start:end] == '{':
            if not args:
                raise ValueError('Invalid Nginx block')
            yield tuple(context), args, '{', start, end
            context.append(args[0]); args = []
        elif token == '}' and text[start:end] == '}':
            if not context or args:
                raise ValueError('Unbalanced Nginx block')
            context.pop()
        elif token == ';' and text[start:end] == ';':
            if args:
                yield tuple(context), args, ';', start, end
            args = []
        else:
            args.append(token)
    if context or args:
        raise ValueError('Incomplete Nginx configuration')


def dump_files(dump):
    pieces = re.split(r'^# configuration file (.+):\s*\n', dump, flags=re.M)
    if len(pieces) < 3:
        raise ValueError('nginx -T did not contain loaded file markers')
    return {pieces[i]: pieces[i + 1].rstrip() + '\n' for i in range(1, len(pieces), 2)}


def expanded_directives(files, main):
    base = str(Path(main).parent)
    active = set()

    def visit(path, inherited=()):
        if path in active:
            raise ValueError('Recursive Nginx include')
        active.add(path)
        for context, args, kind, start, end in directives(files[path]):
            full = inherited + context
            yield path, full, args, kind, start, end
            if args[0] == 'include' and kind == ';' and len(args) == 2:
                pattern = args[1] if args[1].startswith('/') else base + '/' + args[1]
                for candidate in files:
                    if fnmatch.fnmatchcase(candidate, pattern):
                        yield from visit(candidate, full)
        active.remove(path)
    if main not in files:
        raise ValueError('Main config not found in nginx -T; check service arguments')
    return list(visit(main))


def layout(dump, main, managed_dir):
    files = dump_files(dump)
    nodes = expanded_directives(files, main)
    http_blocks = [(path, end) for path, ctx, args, kind, start, end in nodes
                   if ctx == () and args == ['http'] and kind == '{']
    if len(http_blocks) != 1:
        raise ValueError('Expected exactly one http block; cannot safely introduce an include')
    for path, ctx, args, kind, start, end in nodes:
        if ctx == ('http',) and args[0] == 'include' and len(args) == 2:
            pattern = args[1] if args[1].startswith('/') else str(Path(main).parent / args[1])
            if pattern.endswith('/conf.d/*.conf') or pattern == managed_dir + '/*.conf':
                return dict(file=path, content=files[path], site_dir=str(Path(pattern).parent), changed=False)
    path, end = http_blocks[0]
    if not safe_path(managed_dir):
        raise ValueError('Unsafe managed include directory')
    content = files[path][:end] + '\n    include ' + managed_dir + '/*.conf;' + files[path][end:]
    return dict(file=path, content=content, site_dir=managed_dir, changed=True)


def conflicts(dump, name, exclude=''):
    occupied = []
    for path, content in dump_files(dump).items():
        if path == exclude:
            continue
        for ctx, args, kind, start, end in directives(content):
            if args[0] != 'server_name':
                continue
            for candidate in args[1:]:
                if (candidate.startswith('~') or candidate == name or
                    (candidate.startswith('.') and (name == candidate[1:] or name.endswith(candidate))) or
                    ('*' in candidate and fnmatch.fnmatchcase(name, candidate))):
                    occupied.append(path)
    return sorted(set(occupied))


def fill(name, **values):
    text = (TEMPLATES / name).read_text()
    return re.sub(r'@@([A-Z_]+)@@', lambda m: str(values[m[1]]), text)


def address(hostname, port):
    return f'[{hostname}]:{port}' if ':' in hostname else f'{hostname}:{port}'


def proxy(p, hostname, port, protocol, variable):
    size = {'standard': 8, 'auth': 32, 'large': 64}[p['buffer']]
    lines = [f'proxy_pass {protocol}://{address(hostname, port)};', 'proxy_http_version 1.1;',
             'proxy_set_header Host $host;', 'proxy_set_header X-Real-IP $remote_addr;',
             # Replace untrusted inbound XFF rather than forwarding a client-controlled chain.
             'proxy_set_header X-Forwarded-For $remote_addr;', 'proxy_set_header X-Forwarded-Proto $scheme;',
             f'proxy_buffer_size {size}k;', f'proxy_buffers 8 {size}k;',
             f'proxy_busy_buffers_size {size * 2}k;',
             f'proxy_connect_timeout {min(p["timeout"], 60)}s;',
             f'proxy_send_timeout {p["timeout"]}s;', f'proxy_read_timeout {p["timeout"]}s;']
    if p['streaming']:
        lines += ['proxy_buffering off;']
    if p['websocket']:
        lines += ['proxy_set_header Upgrade $http_upgrade;', f'proxy_set_header Connection ${variable};']
    else:
        lines += ['proxy_set_header Connection "";']
    if protocol == 'https':
        lines += ['proxy_ssl_server_name on;', f'proxy_ssl_name {hostname};',
                  'proxy_ssl_verify on;', 'proxy_ssl_trusted_certificate /etc/ssl/cert.pem;']
    return '\n'.join('        ' + line for line in lines)


def render(p, challenge, cert='', key='', cidrs=()):
    p = validate(p)
    if not safe_path(challenge):
        raise ValueError('Unsafe challenge path')
    if p['https'] and not (cert and key and Path(cert).is_file() and Path(key).is_file()):
        raise ValueError('HTTPS requires existing certificate and private key files')
    variable = 'nm_' + hashlib.sha256(p['domain'].encode()).hexdigest()[:16] + '_connection'
    preamble = ''
    if p['websocket']:
        preamble = f'map $http_upgrade ${variable} {{\n    default upgrade;\n    \'\' close;\n}}\n'
    real = ''
    if p['real_ip']:
        if not cidrs:
            raise ValueError('Trusted real-IP mode requires freshly retrieved Cloudflare CIDRs')
        for cidr in cidrs:
            ipaddress.ip_network(cidr, strict=True)
        real = '\n'.join('    set_real_ip_from ' + c + ';' for c in cidrs)
        real += '\n    real_ip_header CF-Connecting-IP;\n    real_ip_recursive on;'
    upstream = proxy(p, p['host'], p['port'], p['protocol'], variable)
    api = proxy(p, p['api_host'], p['api_port'], p['api_protocol'], variable)
    if p['app'] in {'react', 'angular', 'static'}:
        locations = fill('static-site.conf', ROOT=p['root'], FALLBACK='/index.html' if p['spa'] or p['app'] in {'react', 'angular'} else '=404')
    elif p['app'] == 'custom':
        locations = Path(p['custom_file']).read_text()
        # Deliberately limited escape hatch: prevent new listeners, includes and identities.
        allowed = {'location', 'return', 'try_files', 'root', 'index', 'add_header', 'expires',
                   'proxy_pass', 'proxy_set_header', 'proxy_http_version', 'proxy_buffering',
                   'proxy_buffer_size', 'proxy_buffers', 'proxy_busy_buffers_size',
                   'proxy_read_timeout', 'proxy_send_timeout', 'proxy_connect_timeout'}
        for ctx, args, kind, start, end in directives(locations):
            if args[0] not in allowed or (kind == '{' and args[0] != 'location'):
                raise ValueError('Custom file contains unsupported directives; only reviewed location/body settings are allowed')
    else:
        locations = fill('reverse-proxy.conf', PROXY=upstream)
    if p['api']:
        locations = fill('frontend-api.conf', PREFIX=p['api_prefix'], PROXY=api) + locations
    acme = f'    location ^~ /.well-known/acme-challenge/ {{\n        root {challenge};\n        default_type text/plain;\n        try_files $uri =404;\n    }}\n'
    header = f'# nginx-manager owned: {p["domain"]}\n'
    tls = ''
    if p['https']:
        for value in [cert, key]:
            if not safe_path(value):
                raise ValueError('Unsafe certificate path')
        tls = f'    ssl_certificate {cert};\n    ssl_certificate_key {key};\n    ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_session_cache shared:NM_{variable}:1m;\n'
    server = (f'server {{\n    listen {"443 ssl" if p["https"] else "80"};\n'
              + (f'    listen [::]:{"443 ssl" if p["https"] else "80"};\n' if ':' in p['origin_ip'] else '')
              + f'    server_name {p["domain"]};\n    client_max_body_size {p["body_size"]};\n'
              f'    access_log off;\n    error_log stderr warn;\n{tls}{real}\n')
    server += ('' if p['https'] else acme) + locations + '}\n'
    if p['https']:
        server += fill('https-redirect.conf', DOMAIN=p['domain'], ACME=acme, IPV6='    listen [::]:80;' if ':' in p['origin_ip'] else '')
    return header + preamble + server


def header_summary(raw):
    sizes = {}; total = 0
    for line in raw.splitlines():
        total += len(line.encode()) + 2
        if ':' not in line:
            continue
        name = line.split(':', 1)[0].lower()
        if not re.fullmatch(r'[a-z0-9-]+', name):
            continue
        sizes.setdefault(name, []).append(len(line.encode()) + 2)
    out = [f'Aggregate response header bytes: {total}']
    out += [f'{name}: count={len(values)}, max-line-bytes={max(values)}, total-bytes={sum(values)}'
            for name, values in sorted(sizes.items())]
    return '\n'.join(out)


def log_summary(raw):
    patterns = {'upstream sent too big header': 'oversized upstream response headers',
                'Connection refused': 'upstream connection refused', 'upstream timed out': 'upstream timeout',
                'SSL_do_handshake() failed': 'TLS handshake failure', 'Permission denied': 'filesystem permission denied',
                'no live upstreams': 'no available upstreams'}
    return '\n'.join(f'{label}: {raw.count(needle)}' for needle, label in patterns.items() if needle in raw) or 'No recognized errors in sampled log. Raw log content suppressed.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation')
    parser.add_argument('args', nargs='*')
    ns = parser.parse_args(); a = ns.args
    if ns.operation == 'validate':
        print(json.dumps(load(a[0]), indent=2))
    elif ns.operation == 'load':
        for k, v in load(a[0]).items():
            print(f'{k}\t{str(v).lower() if isinstance(v, bool) else v}')
    elif ns.operation == 'make':
        raw = {}
        for arg in a:
            k, v = arg.split('=', 1)
            if k in DEFAULTS and type(DEFAULTS[k]) is bool:
                if v not in {'true', 'false'}:
                    raise ValueError('Invalid boolean')
                v = v == 'true'
            elif k in {'port', 'api_port', 'timeout'}:
                v = int(v)
            raw[k] = v
        print(json.dumps(validate(raw), indent=2))
    elif ns.operation == 'render':
        print(render(load(a[0]), a[1], a[2] if len(a) > 2 else '', a[3] if len(a) > 3 else '',
                     a[4].split(',') if len(a) > 4 and a[4] else ()), end='')
    elif ns.operation == 'layout':
        print(json.dumps(layout(sys.stdin.read(), a[0], a[1])))
    elif ns.operation == 'conflicts':
        found = conflicts(sys.stdin.read(), a[0], a[1] if len(a) > 1 else '')
        print('\n'.join(found), end='\n' if found else '')
        return 1 if found else 0
    elif ns.operation == 'worker':
        files=dump_files(sys.stdin.read())
        names=[args[1] for path,ctx,args,kind,start,end in expanded_directives(files,a[0]) if ctx==() and args[0]=='user' and len(args)>=2]
        compiled=shlex.split(a[1].split('configure arguments:',1)[-1])
        name=names[0] if names else next((x.split('=',1)[1] for x in compiled if x.startswith('--user=')),'nobody')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',name): raise ValueError('Unsupported worker user')
        print(name)
    elif ns.operation == 'inventory':
        for path, text in dump_files(sys.stdin.read()).items():
            for ctx, args, kind, start, end in directives(text):
                if args[0] in {'server_name', 'listen'}:
                    print(f'{path}: ' + ' '.join(args))
    elif ns.operation == 'files':
        print('\n'.join(dump_files(sys.stdin.read())))
    elif ns.operation == 'header-summary':
        print(header_summary(sys.stdin.read()))
    elif ns.operation == 'log-summary':
        print(log_summary(sys.stdin.read()))
    elif ns.operation == 'ip':
        value = ipaddress.ip_address(a[0])
        if not value.is_global:
            raise ValueError('Expected globally routable public IP')
        print(value)
    elif ns.operation == 'cert-path':
        if not safe_path(a[0]):
            raise ValueError('Unsafe path')
    else:
        raise ValueError('Unknown helper operation')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, IndexError) as error:
        print('Configuration error: ' + str(error), file=sys.stderr)
        sys.exit(2)
