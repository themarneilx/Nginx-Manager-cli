#!/usr/bin/env python3
"""Resolve the same config/prefix as the service we will reload; reject ambiguity."""
import json
from pathlib import Path
import re
import shlex
import sys
sys.dont_write_bytecode = True
from config import safe_path

try:
    explicit, binary, version = sys.argv[1:]
    text = sys.stdin.read().strip()
    if not text or 'argv[]=' not in text:
        raise ValueError('Cannot identify service ExecStart; use a real Nginx systemd unit')
    # Nginx config/prefix paths are restricted to shell/Nginx-safe absolute paths.
    executable = re.search(r'\bpath=([^ ;]+)', text)
    if not executable or Path(executable[1]).resolve() != Path(binary).resolve():
        raise ValueError('Selected binary differs from the service ExecStart executable')
    compiled = shlex.split(version.split('configure arguments:', 1)[-1])
    conf = next((x.split('=', 1)[1] for x in compiled if x.startswith('--conf-path=')), '')
    compiled_prefix = next((x.split('=', 1)[1] for x in compiled if x.startswith('--prefix=')), '/usr/local/nginx')
    args = text.split('argv[]=', 1)[1].split(' ; ignore_errors=', 1)[0]
    configs = re.findall(r'(?:^|\s)-c\s*(?:"([^"\s]+)"|\'([^\'\s]+)\'|([^\s;]+))', args)
    prefixes = re.findall(r'(?:^|\s)-p\s*(?:"([^"\s]+)"|\'([^\'\s]+)\'|([^\s;]+))', args)
    if len(configs) > 1 or len(prefixes) > 1:
        raise ValueError('Ambiguous service config arguments')
    prefix = next((x for x in prefixes[0] if x), '') if prefixes else ''
    if configs:
        conf = next(x for x in configs[0] if x)
    if not conf.startswith('/'):
        conf = str(Path(prefix or compiled_prefix) / conf)
    if explicit and Path(explicit) != Path(conf):
        raise ValueError('Selected --nginx-conf differs from the service configuration; select the matching --service')
    if not safe_path(conf) or (prefix and not safe_path(prefix.rstrip('/'))):
        raise ValueError('Unsafe or unsupported service config/prefix path')
    # Global includes can create a completely different loaded configuration. Refuse those.
    if re.search(r'(?:^|\s)-g', args):
        global_args = args.split('-g', 1)[1].split(' ; ignore_errors=', 1)[0]
        if re.search(r'\b(include|http|server|events|load_module)\b', global_args):
            raise ValueError('Service -g defines configuration structure; normalize it before using this tool')
    print(json.dumps(dict(config=conf, prefix=prefix)))
except (ValueError, IndexError) as error:
    print('Service safety error: ' + str(error), file=sys.stderr)
    sys.exit(1)
