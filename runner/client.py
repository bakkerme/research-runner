"""Minimal HTTP producer client. Uses no local service/state lock."""
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

from .package import MAX_FILE, MAX_FILES, MAX_TOTAL, digest, relpath, verify, hash_file


def save_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


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

    def collect(self, job, output, generation=None, apply=False):
        base = '/api/jobs/' + urllib.parse.quote(job, safe='')
        manifest = self.request(base + '/artifact-manifest' + (f'?generation={generation}' if generation else ''))
        files = manifest['files']
        if len(files) > MAX_FILES or sum(f['size'] for f in files) > MAX_TOTAL:
            raise ValueError('collection exceeds client limits')
        names = [relpath(f['name']) for f in files]
        if len(set(names)) != len(names) or any(not n.startswith('outputs/') for n in names):
            raise ValueError('invalid artifact manifest')
        if not apply:
            return {'preview': True, 'destination': str(pathlib.Path(output).resolve()), 'manifest': manifest}
        destination = pathlib.Path(output)
        destination.mkdir(parents=True, exist_ok=False)
        for meta in files:
            if not 0 <= meta['size'] <= MAX_FILE:
                raise ValueError('invalid artifact size')
            data = self.request(base + '/artifacts/' + urllib.parse.quote(meta['name'], safe='/') + '?generation=' + str(manifest['generation']), raw=True)
            if len(data) != meta['size'] or digest(data) != meta['sha256']:
                raise ValueError('artifact does not match selected generation')
            target = destination / meta['name']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        result = {'format': 'runner-collection/v1', 'url': self.url, 'job_id': job,
                  'generation': manifest['generation'], 'manifest': manifest, 'verified': True}
        save_json(destination / 'receipt.json', result)
        return result
