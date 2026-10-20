"""Minimal HTTP producer client. Uses no local service/state lock."""
import json
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .package import MAX_FILE, MAX_FILES, MAX_TOTAL, digest, relpath, verify, hash_file
from .locking import file_lock


def save_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            target.write(json.dumps(value, indent=2) + '\n')
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Client:
    def __init__(self, url, token=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('use an http(s) server URL without credentials, query or fragment')
        self.url = url.rstrip('/')
        self.token = token if token is not None else os.getenv('RESEARCH_RUNNER_TOKEN')

    def request(self, path, method='GET', data=None, key=None, raw=False):
        headers = {}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if key:
            headers['Idempotency-Key'] = key
        if hasattr(data, 'read'):
            headers['Content-Type'] = 'application/octet-stream'
            headers['Content-Length'] = str(os.fstat(data.fileno()).st_size)
        elif data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=3600 if hasattr(data, 'read') else 30) as response:
                body = response.read(MAX_FILE + 1)
                if len(body) > MAX_FILE:
                    raise ValueError('server response exceeds client limit')
                if raw:
                    if response.headers.get('X-Content-SHA256') != digest(body):
                        raise ValueError('download hash header mismatch')
                    return body
                return json.loads(body)
        except urllib.error.HTTPError as error:
            raise ValueError(f'Runner HTTP {error.code}: ' + error.read(2000).decode(errors='replace')) from error

    def submit(self, archive, key, receipt, apply=False):
        manifest, inventory = verify(archive)
        archive = pathlib.Path(archive)
        info = {'format': 'runner-receipt/v1', 'url': self.url,
                'submission_key': key, 'package_sha256': hash_file(archive), 'id': None}
        if not apply:
            return dict(info, preview=True, bytes=archive.stat().st_size, package_format=manifest['format'])
        receipt = pathlib.Path(receipt)
        if receipt.exists():
            old = json.loads(receipt.read_text())
            if any(old.get(k) != info[k] for k in ('url', 'submission_key', 'package_sha256')):
                raise ValueError('receipt belongs to a different submission')
            info = old
        caps = self.request('/api/capabilities')
        if manifest['format'] not in caps['package_formats']:
            raise ValueError('server does not support this package format')
        limits = caps.get('limits', {})
        if archive.stat().st_size > limits.get('archive_bytes', float('inf')):
            raise ValueError('archive exceeds server upload limit')
        if sum(f['size'] for f in inventory.values()) > limits.get('package_bytes', float('inf')) or any(f['size'] > limits.get('file_bytes', float('inf')) for f in inventory.values()):
            raise ValueError('inputs exceed server package limits')
        # Persist request identity before a possibly ambiguous network response.
        save_json(receipt, info)
        with archive.open('rb') as data:
            response = self.request('/api/jobs', 'POST', data, key)
        if response['package_sha256'] != info['package_sha256']:
            raise ValueError('submission receipt hash mismatch')
        info.update(response)
        info['format'] = 'runner-receipt/v1'
        save_json(receipt, info)
        return info

    def control(self, job, action, key, apply=False, body=None):
        path = '/api/jobs/' + urllib.parse.quote(job, safe='') + '/' + action
        if not apply:
            return {'preview': True, 'url': self.url + path, 'key': key, 'body': body or {}}
        return self.request(path, 'POST', body or {}, key)

    def capabilities(self):
        return self.request('/api/capabilities')

    def status(self, job):
        return self.request('/api/jobs/' + urllib.parse.quote(job, safe='') + '/status')

    def events(self, job, after=0, limit=100):
        return self.request('/api/jobs/' + urllib.parse.quote(job, safe='') +
                            f'/event-log?after={after}&limit={limit}')

    def monitor(self, job, after=0, interval=2, timeout=None):
        """Yield paged events and changed statuses using GET only; never resume jobs."""
        if not 0.1 <= interval <= 60 or timeout is not None and timeout < 0:
            raise ValueError('interval must be 0.1..60 seconds; timeout must be nonnegative')
        started = time.monotonic()
        previous = None
        while True:
            status = self.status(job)
            while True:
                page = self.events(job, after=after)
                for event in page['events']:
                    yield {'kind': 'event', 'event': event}
                cursor = page['next_cursor']
                if page.get('has_more') and cursor <= after:
                    raise ValueError('server event cursor did not advance')
                after = cursor
                if not page.get('has_more') or timeout is not None and time.monotonic() - started >= timeout:
                    break
            # Avoid emitting a full status for every unchanged poll.
            signature = {key: status.get(key) for key in ('state', 'outcome', 'result', 'last_event', 'pending_approvals', 'publication', 'error', 'finalize_reason')}
            if signature != previous:
                yield {'kind': 'status', 'status': status, 'cursor': after}
                previous = signature
            if status['state'] in ('finished', 'completed', 'failed', 'interrupted') or status.get('pending_approvals'):
                return
            remaining = None if timeout is None else timeout - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                return
            time.sleep(interval if remaining is None else min(interval, remaining))

    def collect(self, job, output, generation=None, apply=False, progress=None):
        """Pin a generation, resume verified files, then atomically publish output.

        Interrupted downloads remain in OUTPUT.partial. Repeating the same call
        reuses its pinned manifest even if a newer publication now exists.
        Progress callbacks receive file/byte counts; receipts describe integrity.
        """
        destination = pathlib.Path(output).absolute()
        if not apply:
            manifest = self._collection_manifest(job, generation)
            return {'preview': True, 'destination': str(destination), 'manifest': manifest}
        with file_lock(destination.with_name(destination.name + '.lock')):
            return self._collect(job, destination, generation, progress)

    def _collection_manifest(self, job, generation):
        base = '/api/jobs/' + urllib.parse.quote(job, safe='')
        manifest = self.request(base + '/artifact-manifest' + (f'?generation={generation}' if generation else ''))
        self._validate_collection(manifest, job, generation)
        return manifest

    @staticmethod
    def _validate_collection(manifest, job, generation):
        if manifest.get('job_id') != job or type(manifest.get('generation')) is not int or manifest['generation'] < 1:
            raise ValueError('invalid artifact manifest identity')
        if generation is not None and manifest['generation'] != generation:
            raise ValueError('server returned a different generation')
        files = manifest['files']
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ValueError('collection exceeds client limits')
        for meta in files:
            if type(meta.get('size')) is not int or not 0 <= meta['size'] <= MAX_FILE:
                raise ValueError('invalid artifact size')
            sha = meta.get('sha256', '')
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                raise ValueError('invalid artifact hash')
        if sum(f['size'] for f in files) > MAX_TOTAL:
            raise ValueError('collection exceeds client limits')
        names = [relpath(f['name']) for f in files]
        if len({n.casefold() for n in names}) != len(names) or any(not n.startswith('outputs/') for n in names):
            raise ValueError('invalid artifact manifest')
        for name in names:
            for part in name.split('/'):
                if any(c in part for c in '<>:"|?*') or part.endswith((' ', '.')) or part.split('.')[0].upper() in {
                    'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
                    raise ValueError('artifact path is not portable: ' + name)
            if any(parent.as_posix().casefold() in {n.casefold() for n in names} for parent in pathlib.PurePosixPath(name).parents):
                raise ValueError('conflicting artifact paths')

    @staticmethod
    def _safe_tree(root):
        # Never follow a symlink planted in a completed or interrupted collection.
        if root.is_symlink() or any(p.is_symlink() for p in root.rglob('*')):
            raise ValueError('collection links forbidden')

    def _collect(self, job, destination, generation, progress):
        for parent in (destination, *destination.parents):
            if parent.is_symlink():
                raise ValueError('collection links forbidden')
        staging = destination.with_name(destination.name + '.partial')
        existing = destination if destination.exists() else staging
        if existing.exists():
            self._safe_tree(existing)
            receipt_path = existing / 'receipt.json'
            if not receipt_path.is_file():
                raise ValueError('destination exists without a collection receipt; use a new destination')
            result = json.loads(receipt_path.read_text())
            if result.get('url') != self.url or result.get('job_id') != job:
                raise ValueError('destination belongs to a different collection')
            manifest = result['manifest']
            self._validate_collection(manifest, job, generation)
            if result.get('generation') != manifest['generation']:
                raise ValueError('collection receipt generation mismatch')
        else:
            manifest = self._collection_manifest(job, generation)
            result = {'format': 'runner-collection/v1', 'url': self.url, 'job_id': job,
                      'generation': manifest['generation'], 'manifest': manifest, 'verified': False}
            staging.mkdir(parents=True, exist_ok=False)
            save_json(staging / 'receipt.json', result)
        root = destination if destination.exists() else staging
        self._safe_tree(root)
        files = manifest['files']
        total = sum(meta['size'] for meta in files)
        done = 0
        base = '/api/jobs/' + urllib.parse.quote(job, safe='')
        if destination.exists() and not result.get('verified'):
            raise ValueError('completed destination has an incomplete receipt')
        for index, meta in enumerate(files, 1):
            target = root / meta['name']
            valid = target.is_file() and target.stat().st_size == meta['size'] and hash_file(target) == meta['sha256']
            if not valid:
                if destination.exists():
                    raise ValueError('completed collection was modified: ' + meta['name'])
                data = self.request(base + '/artifacts/' + urllib.parse.quote(meta['name'], safe='/') + '?generation=' + str(manifest['generation']), raw=True)
                if len(data) != meta['size'] or digest(data) != meta['sha256']:
                    raise ValueError('artifact does not match selected generation')
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(dir=target.parent, prefix='rr-download-')
                try:
                    with os.fdopen(fd, 'wb') as out:
                        out.write(data)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(name, target)
                finally:
                    pathlib.Path(name).unlink(missing_ok=True)
            done += meta['size']
            if progress:
                progress({'name': meta['name'], 'bytes': done, 'total_bytes': total,
                          'files': index, 'total_files': len(files), 'reused': valid})
        result['verified'] = True
        save_json(root / 'receipt.json', result)
        if root == staging:
            staging.rename(destination)
        return result
