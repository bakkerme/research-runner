"""Operator-installed asset loaders. No server or loader implementation imports."""
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import tomllib

from .client import save_json
from .package import relpath

PROTOCOL = 'runner-asset-loader/v1'
MAX_LINE = 65536


def read_config(path=None):
    if path is None:
        return {}
    data = tomllib.loads(Path(path).read_text(encoding='utf-8'))
    if set(data) != {'asset_loaders'} or not isinstance(data['asset_loaders'], dict):
        raise ValueError('loader config must contain only [asset_loaders.TYPE] registrations')
    loaders = data['asset_loaders']
    for kind, config in loaders.items():
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', kind):
            raise ValueError('invalid asset type: ' + kind)
        if not isinstance(config, dict) or set(config) - {'command', 'timeout_seconds', 'max_bytes'}:
            raise ValueError('invalid loader registration: ' + kind)
        command = config.get('command')
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x or '\x00' in x for x in command):
            raise ValueError('loader command must be a nonempty argument array: ' + kind)
        if not Path(command[0]).is_absolute():
            raise ValueError('loader executable must be an absolute path: ' + kind)
        if not Path(command[0]).is_file() or not os.access(command[0], os.X_OK):
            raise ValueError('loader executable is missing or not executable: ' + kind)
        if 'timeout_seconds' in config:
            value = config['timeout_seconds']
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('timeout_seconds must be positive when provided')
        if 'max_bytes' in config and (type(config['max_bytes']) is not int or config['max_bytes'] <= 0):
            raise ValueError('max_bytes must be a positive integer when provided')
    return loaders


def inventory(root, max_bytes=None, check=None):
    """Canonical inventory: regular file paths, byte lengths and SHA-256 hashes.

    Directories (including empty ones) are recorded; links and special files fail.
    """
    root = Path(root)
    entries = []
    total = 0
    if root.is_symlink() or not root.is_dir():
        raise ValueError('asset staging must be a directory')
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            if check:
                check()
            path = Path(current) / name
            relative = relpath(path.relative_to(root).as_posix())
            meta = path.lstat()
            if stat.S_ISDIR(meta.st_mode):
                entries.append({'path': relative, 'kind': 'directory'})
            elif stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1:
                total += meta.st_size
                if max_bytes is not None and total > max_bytes:
                    raise ValueError('asset exceeds max_bytes')
                sha = hashlib.sha256()
                with path.open('rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        if check:
                            check()
                        sha.update(chunk)
                after = path.stat()
                if (meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('asset changed during verification')
                entries.append({'path': relative, 'kind': 'file', 'size': meta.st_size, 'sha256': sha.hexdigest(), 'executable': bool(meta.st_mode & 0o111)})
            else:
                raise ValueError('asset links and special files forbidden')
            if len(entries) > 10000:
                raise ValueError('asset exceeds 10000 inventory entries')
    entries.sort(key=lambda item: item['path'])
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': total, 'entries': entries}


def _process_identity(pid):
    probe = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True, timeout=5)
    return probe.stdout.strip() if probe.returncode == 0 else ''


def cleanup_orphan(marker):
    """A start-time check avoids signalling an unrelated reused process ID."""
    marker = Path(marker)
    info = json.loads(marker.read_text())
    if info.get('started') and _process_identity(info['pid']) == info['started']:
        try:
            os.killpg(info['pid'], signal.SIGKILL)
        except ProcessLookupError:
            pass
    marker.unlink(missing_ok=True)


def invoke(config, request, *, progress=None, cancel=None, deadline=None, marker=None, handshake=False):
    """One request on stdin; bounded NDJSON progress and one terminal result."""
    if os.name != 'posix':
        raise ValueError('asset loader execution requires a Unix Runner host')
    # Loader secrets use private files/services, never inherited worker env vars.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
    started = time.monotonic()
    timeout = config.get('timeout_seconds')
    if handshake:
        timeout = min(timeout, 10) if timeout is not None else 10
    end = started + timeout if timeout is not None else float('inf')
    if deadline is not None:
        end = min(end, started + max(0, deadline - time.time()))
    proc = subprocess.Popen(config['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    messages = queue.Queue(maxsize=32)
    closing = threading.Event()

    def read():
        try:
            while not closing.is_set():
                line = proc.stdout.readline(MAX_LINE + 1)
                if not line:
                    return
                if len(line) > MAX_LINE:
                    item = ValueError('loader protocol line exceeds 64 KiB')
                else:
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        item = ValueError('loader emitted invalid JSON')
                while not closing.is_set():
                    try:
                        messages.put(item, timeout=.1)
                        break
                    except queue.Full:
                        pass
                if isinstance(item, Exception):
                    return
        finally:
            while not closing.is_set():
                try:
                    messages.put(None, timeout=.1)
                    break
                except queue.Full:
                    pass

    reader = threading.Thread(target=read, daemon=True)
    def write():
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # The exit status / missing terminal result reports this safely.
    writer = threading.Thread(target=write, daemon=True)
    result = None
    eof = False
    heartbeat = started
    last_progress = 0
    last_quota_check = 0
    try:
        if marker:
            save_json(marker, {'pid': proc.pid, 'started': _process_identity(proc.pid)})
        payload = (json.dumps(dict(request, protocol=PROTOCOL)) + '\n').encode()
        if len(payload) > MAX_LINE:
            raise ValueError('loader request exceeds 64 KiB')
        reader.start()
        writer.start()
        while True:
            now = time.monotonic()
            if cancel and cancel.is_set():
                raise InterruptedError('asset preparation stopped by user')
            if now >= end:
                raise TimeoutError('asset loader deadline exceeded')
            if config.get('max_bytes') is not None and request.get('staging_directory') and now - last_quota_check >= 1:
                size = 0
                for current, dirs, files in os.walk(request['staging_directory'], followlinks=False):
                    for name in files:
                        size += (Path(current) / name).lstat().st_size
                        if size > config['max_bytes']:
                            raise ValueError('asset exceeds max_bytes')
                last_quota_check = now
            if progress and now - heartbeat >= 5:
                progress({'message': 'Still preparing asset', 'elapsed_seconds': int(now - started)})
                heartbeat = now
            try:
                item = messages.get(timeout=.1)
            except queue.Empty:
                if eof and proc.poll() is not None:
                    break
                continue
            if item is None:
                eof = True
                if proc.poll() is not None:
                    break
                continue
            if isinstance(item, Exception):
                raise item
            if not isinstance(item, dict) or result is not None:
                raise ValueError('unexpected loader event after result or invalid event')
            if item.get('event') == 'progress':
                if set(item) - {'event', 'message', 'bytes_completed', 'bytes_total'}:
                    raise ValueError('unknown loader progress fields')
                if not isinstance(item.get('message'), str) or not 1 <= len(item['message']) <= 1000:
                    raise ValueError('invalid loader progress message')
                for key in ('bytes_completed', 'bytes_total'):
                    if key in item and (type(item[key]) is not int or item[key] < 0):
                        raise ValueError('invalid loader progress bytes')
                if 'bytes_total' in item and item.get('bytes_completed', 0) > item['bytes_total']:
                    raise ValueError('loader progress exceeds total')
                # Bound persisted event rate even if a loader emits per chunk.
                if progress and now - last_progress >= .5:
                    progress({k: v for k, v in item.items() if k != 'event'})
                    last_progress = now
                    heartbeat = now
            elif item.get('event') == 'result' and item.get('protocol') == PROTOCOL:
                result = {k: v for k, v in item.items() if k not in ('event', 'protocol')}
            elif item.get('event') == 'error':
                codes = {'not_found', 'access_denied', 'unavailable', 'invalid_request'}
                code = item.get('code')
                raise ValueError('asset loader error: ' + (code if code in codes else 'loader_failed'))
            else:
                raise ValueError('unsupported asset loader event or protocol')
        if proc.wait() != 0:
            raise ValueError('asset loader exited unsuccessfully (diagnostics withheld)')
        if result is None:
            raise ValueError('asset loader did not return a result')
        return result
    finally:
        closing.set()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
        if writer.ident:
            writer.join(timeout=2)
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.stdout.close()
        if reader.ident:
            reader.join(timeout=2)
        if marker:
            Path(marker).unlink(missing_ok=True)


def validate_loaders(loaders):
    results = []
    for kind, config in loaders.items():
        result = invoke(config, {'operation': 'describe', 'asset_type': kind}, handshake=True)
        _description(result)
        results.append({'asset_type': kind, **result, 'protocol': PROTOCOL})
    return {'valid': True, 'loaders': results}


def _description(result):
    if set(result) != {'loader_version', 'operations'} or not isinstance(result['loader_version'], str) or not 1 <= len(result['loader_version']) <= 100:
        raise ValueError('invalid loader description')
    if result['operations'] not in (['resolve', 'materialize'], ['materialize', 'resolve']):
        raise ValueError('loader must support resolve and materialize')
    return result


def _resolution(value):
    if set(value) != {'resolved_id', 'sha256'} or not isinstance(value['resolved_id'], str) or not 1 <= len(value['resolved_id']) <= 2000 or not isinstance(value['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['sha256']):
        raise ValueError('invalid asset resolution')
    return value


def prepare(job, dependencies, loaders, *, progress, cancel, deadline):
    """Lock all IDs before retrieval. Publish only fully verified asset trees."""
    job = Path(job)
    control = job / 'assets'
    control.mkdir(exist_ok=True)
    lock_path = control / 'lock.json'
    marker = control / 'loader-runtime.json'
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if lock.get('dependencies') != dependencies:
            raise ValueError('dependency lock does not match sealed manifest')
    else:
        lock = {'format': 'runner-assets-lock/v1', 'dependencies': dependencies, 'assets': []}
        save_json(lock_path, lock)
    # Fail unknown types before contacting any loader.
    for dep in dependencies:
        if dep['asset_type'] not in loaders:
            raise ValueError('unregistered asset type: ' + dep['asset_type'])
    for dep in dependencies[len(lock['assets']):]:
        kind = dep['asset_type']
        notify = lambda value, dep=dep: progress(dict(value, asset_type=dep['asset_type'], asset_id=dep['asset_id'], destination=dep['destination']))
        description = _description(invoke(loaders[kind], {'operation': 'describe', 'asset_type': kind}, cancel=cancel, deadline=deadline, marker=marker, handshake=True))
        notify({'message': 'Resolving asset'})
        resolved = _resolution(invoke(loaders[kind], {'operation': 'resolve', 'asset_type': kind, 'asset_id': dep['asset_id']}, progress=notify, cancel=cancel, deadline=deadline, marker=marker))
        lock['assets'].append({'request': dep, **resolved, 'loader_version': description['loader_version']})
        save_json(lock_path, lock)
    receipts = []
    for index, entry in enumerate(lock['assets']):
        dep = entry['request']
        kind = dep['asset_type']
        if kind not in loaders:
            raise ValueError('unregistered asset type: ' + kind)
        notify = lambda value, dep=dep: progress(dict(value, asset_type=dep['asset_type'], asset_id=dep['asset_id'], destination=dep['destination']))
        last_check = [0]
        def check():
            if cancel.is_set():
                raise InterruptedError('asset preparation stopped by user')
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError('asset preparation deadline exceeded')
            if time.monotonic() - last_check[0] >= 5:
                notify({'message': 'Preparing and verifying asset'})
                last_check[0] = time.monotonic()
        check()
        cached = control / ('content-' + str(index))
        details = inventory(cached, loaders[kind].get('max_bytes'), check) if cached.exists() else None
        if details is not None and details['sha256'] != entry['sha256']:
            raise ValueError('retained asset integrity mismatch')
        if not cached.exists():
            description = _description(invoke(loaders[kind], {'operation': 'describe', 'asset_type': kind}, cancel=cancel, deadline=deadline, marker=marker, handshake=True))
            if description['loader_version'] != entry['loader_version']:
                raise ValueError('loader version differs from dependency lock')
            stage = control / ('staging-' + str(index))
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir()
            notify({'message': 'Retrieving asset'})
            value = invoke(loaders[kind], {'operation': 'materialize', 'asset_type': kind,
                'asset_id': dep['asset_id'], 'resolved_id': entry['resolved_id'], 'sha256': entry['sha256'],
                'staging_directory': str(stage.resolve())}, progress=notify, cancel=cancel, deadline=deadline, marker=marker)
            if set(value) != {'materialized'} or value['materialized'] is not True:
                raise ValueError('invalid materialize result')
            notify({'message': 'Verifying asset'})
            details = inventory(stage, loaders[kind].get('max_bytes'), check)
            if details['sha256'] != entry['sha256']:
                raise ValueError('asset digest mismatch')
            stage.replace(cached)
        target = job / 'work' / dep['destination']
        # Workspace parents are writable by research; reject escapes on resume.
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError('dependency destination contains a link')
        if target.exists():
            if inventory(target, check=check)['sha256'] != entry['sha256']:
                raise ValueError('materialized dependency changed; restore it or submit a new job')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.rr-asset-staging')
            if temporary.exists():
                raise ValueError('dependency staging destination already exists')
            try:
                def copy(source, dest):
                    with open(source, 'rb') as src, open(dest, 'xb') as out:
                        for chunk in iter(lambda: src.read(1024 * 1024), b''):
                            check()
                            out.write(chunk)
                    shutil.copystat(source, dest)
                shutil.copytree(cached, temporary, copy_function=copy)
                temporary.replace(target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        receipts.append(dict(entry, **details))
        notify({'message': 'Asset ready', 'bytes_completed': details['bytes'], 'bytes_total': details['bytes']})
    receipt = {'format': 'runner-assets-receipt/v1', 'assets': receipts}
    save_json(control / 'receipt.json', receipt)
    return receipt
