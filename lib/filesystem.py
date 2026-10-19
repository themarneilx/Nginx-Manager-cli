#!/usr/bin/env python3
"""Private transaction manifests with atomic writes and guarded rollback."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def check(path):
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts:
        raise ValueError('Expected absolute path')
    for entry in [p, *p.parents]:
        if entry.is_symlink():
            raise ValueError('Refusing symlink: ' + str(entry))
    if p.exists() and not p.is_file():
        raise ValueError('Expected regular file: ' + str(p))


def atomic(path, data, mode):
    check(path)
    p = Path(path)
    p.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.nm-', dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, p)
        directory = os.open(p.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def snapshot(path):
    check(path)
    p = Path(path)
    if not p.exists():
        return dict(path=path, exists=False, sha=None)
    return dict(path=path, exists=True, data=base64.b64encode(p.read_bytes()).decode(),
                mode=stat.S_IMODE(p.stat().st_mode), sha=digest(path))


def save(path, manifest):
    atomic(path, (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)


def read(path):
    return json.loads(Path(path).read_text())


def change(manifest_path, path, data, mode):
    check(path)
    manifest = read(manifest_path)
    entries = manifest['files']
    item = next((e for e in entries if e['path'] == path), None)
    if item is None:
        item = snapshot(path); entries.append(item)
    elif digest(path) != item['after']:
        raise ValueError('Concurrent change detected: ' + path)
    item['after'] = hashlib.sha256(data).hexdigest() if data is not None else None
    save(manifest_path, manifest)
    if data is None:
        Path(path).unlink(missing_ok=True)
    else:
        atomic(path, data, mode)


def rollback(manifest_path):
    manifest = read(manifest_path)
    errors = []
    for item in reversed(manifest['files']):
        path = item['path']
        try:
            check(path)
            current = digest(path)
            if current == item['sha']:
                continue
            if current != item.get('after'):
                raise ValueError('Concurrent edit; recovery required for ' + path)
            if item['exists']:
                atomic(path, base64.b64decode(item['data']), item['mode'])
            else:
                Path(path).unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    manifest['status'] = 'rollback-incomplete' if errors else 'rolled-back'
    save(manifest_path, manifest)
    if errors:
        raise ValueError('; '.join(errors))


def allowed(path, roots):
    p = Path(path)
    check(path)
    if not any(p.is_relative_to(Path(root)) for root in roots):
        raise ValueError('Backup path outside allowed configuration/state trees: ' + path)
    if any(part in {'live', 'archive', 'accounts', 'renewal'} for part in p.parts) or p.suffix in {'.pem', '.key'}:
        raise ValueError('Certificate stores are excluded from restore')


def main():
    op, *args = sys.argv[1:]
    if op == 'acme-token':
        root, token = args
        import re
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            raise ValueError('Invalid challenge token')
        p=Path(root)
        if not p.is_absolute() or '..' in p.parts:
            raise ValueError('Unsafe ACME root')
        for entry in [p,*p.parents]:
            if entry.is_symlink(): raise ValueError('ACME paths cannot use symlinks')
        for directory in [p,p/'.well-known',p/'.well-known/acme-challenge']:
            if directory.is_symlink(): raise ValueError('ACME paths cannot use symlinks')
            if not directory.exists():
                directory.mkdir(mode=0o755)
                # Correct the private process umask only for directories we just created.
                directory.chmod(0o755)
            st=directory.stat()
            if not directory.is_dir() or st.st_uid != os.geteuid() or st.st_mode & 0o022 or st.st_mode & 0o055 != 0o055:
                raise ValueError('Existing ACME directory must be owner-controlled and world-readable/traversable; it will not be chmodded')
        fd=os.open(p/'.well-known/acme-challenge'/token,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        with os.fdopen(fd,'w') as out:
            os.fchmod(out.fileno(),0o644); out.write(token)
    elif op == 'begin':
        save(args[0], dict(version=1, status='pending', files=[]))
    elif op == 'write':
        change(args[0], args[1], sys.stdin.buffer.read(), int(args[2], 8))
    elif op == 'delete':
        change(args[0], args[1], None, 0o644)
    elif op == 'rollback':
        rollback(args[0])
    elif op == 'commit':
        data = read(args[0]); data['status'] = 'committed'; save(args[0], data)
    elif op == 'backup':
        paths = list(dict.fromkeys(sys.stdin.read().splitlines()))
        roots = args[1:]
        for path in paths:
            allowed(path, roots)
        save(args[0], dict(version=1, status='backup', files=[snapshot(p) for p in paths]))
    elif op == 'restore':
        backup, tx, *roots = args
        manifest = read(backup)
        if manifest.get('version') != 1 or manifest.get('status') != 'backup':
            raise ValueError('Not a configuration backup')
        for item in manifest['files']:
            allowed(item['path'], roots)
            if item['exists']:
                data = base64.b64decode(item['data'], validate=True)
                if hashlib.sha256(data).hexdigest() != item['sha']:
                    raise ValueError('Corrupted backup content')
        for item in manifest['files']:
            change(tx, item['path'], base64.b64decode(item['data']) if item['exists'] else None,
                   item.get('mode', 0o644))
    elif op == 'verify':
        meta = read(args[0]); path = meta['path']
        check(path)
        if digest(path) != meta['sha']:
            raise ValueError('Managed config was manually edited or removed; inspect before proceeding: ' + path)
        print(json.dumps(meta))
    elif op == 'metadata':
        path, enabled, cert, key, cidrs = args
        print(json.dumps(dict(path=path, enabled=enabled == 'true', sha=digest(path), cert=cert, key=key, cidrs=cidrs)))
    elif op == 'sha':
        print(digest(args[0]) or '')
    elif op == 'list':
        for item in read(args[0])['files']:
            print(('restore ' if item['exists'] else 'remove ') + item['path'])
    else:
        raise ValueError('Unknown filesystem operation')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Filesystem safety error: ' + str(error), file=sys.stderr)
        sys.exit(1)
