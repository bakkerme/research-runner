#!/usr/bin/env python3
"""Standalone standard-library example. No Research Runner installation needed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

PROTOCOL = 'runner-asset-loader/v1'


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}), flush=True)


def inventory(root):
    entries = []
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                entries.append({'path': relative, 'kind': 'directory'})
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                sha = hashlib.sha256()
                with path.open('rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        sha.update(chunk)
                entries.append({'path': relative, 'kind': 'file', 'size': info.st_size, 'sha256': sha.hexdigest(),
                                'executable': bool(info.st_mode & 0o111)})
            else:
                raise ValueError('unsupported file')
            if len(entries) > 10000:
                raise ValueError('too many entries')
    entries.sort(key=lambda entry: entry['path'])
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(encoded).hexdigest(), entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', required=True, type=Path)
    args = parser.parse_args()
    line = sys.stdin.buffer.readline(65537)
    if len(line) > 65536:
        raise ValueError('request too large')
    request = json.loads(line)
    if request['protocol'] != PROTOCOL or request['asset_type'] != 'local-files':
        raise ValueError('unsupported protocol or type')
    operation = request['operation']
    if operation == 'describe':
        emit('result', protocol=PROTOCOL, loader_version='example-1', operations=['resolve', 'materialize'])
        return
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    # IDs select catalogue entries, never arbitrary caller-supplied paths.
    asset = catalog.get(request['asset_id'])
    if asset is None:
        emit('error', code='not_found')
        raise SystemExit(1)
    source = Path(asset['source'])
    if not source.is_absolute() or source.is_symlink() or not source.is_dir():
        raise ValueError('source must be an absolute, regular directory')
    emit('progress', message='Inspecting source asset')
    sha, entries = inventory(source)
    if operation == 'resolve':
        emit('result', protocol=PROTOCOL, resolved_id=asset['resolved_id'], sha256=sha)
    elif operation == 'materialize':
        if request['resolved_id'] != asset['resolved_id'] or request['sha256'] != sha:
            raise ValueError('source revision changed')
        target = Path(request['staging_directory'])
        if not target.is_absolute() or target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise ValueError('staging must be an empty absolute directory')
        total = sum(entry.get('size', 0) for entry in entries)
        completed = 0
        for entry in entries:
            destination = target / entry['path']
            if entry['kind'] == 'directory':
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with (source / entry['path']).open('rb') as src, destination.open('xb') as out:
                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                    out.write(chunk)
                    completed += len(chunk)
                    emit('progress', message='Copying asset', bytes_completed=completed, bytes_total=total)
            destination.chmod(0o755 if entry['executable'] else 0o644)
        emit('progress', message='Copy complete', bytes_completed=completed, bytes_total=total)
        emit('result', protocol=PROTOCOL, materialized=True)
    else:
        raise ValueError('unsupported operation')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Do not return exception text containing private paths, URLs or credentials.
        emit('error', code='unavailable')
        raise SystemExit(1)
