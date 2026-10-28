"""Build or reuse declared tool images before a worker starts.

The image tag is derived from the declared inputs. A local image is reused
only when its research.input_hash label matches. Builds, fetches and
architecture checks finish before any worker process is started.
"""
import collections
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

from .package import relpath

FORMAT = 'runner-tool-images/v1'
MAX_IMAGES = 8
MAX_FETCHES = 16
MAX_INPUTS = 128
MAX_FETCH_BYTES = 8 * 1024 ** 3
STDERR_TAIL = 2048
NAME_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$')
PLATFORM_RE = re.compile(r'^linux/[a-z0-9]+$')
SHA_RE = re.compile(r'^[a-f0-9]{64}$')
CHECK_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,99}$')
PROVENANCE_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
URL_FROM_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
IMAGE_ID_RE = re.compile(r'^sha256:[a-f0-9]{64}$')


class ToolImageError(Exception):
    """Provisioning failed. The message is safe to store on the job."""


def input_material(image):
    """Canonical identity of a declared tool image.

    Key order does not matter. Fetch contributes only its sha256 values, in
    declaration order. An omitted fetch and an empty fetch are the same.
    """
    material = {
        'containerfile': image['containerfile'],
        'context': image['context'],
        'platform': image['platform'],
        'inputs': dict(image['inputs']),
    }
    fetch = image.get('fetch') or []
    if fetch:
        material['fetch'] = [item['sha256'] for item in fetch]
    return material


def input_hash(image):
    encoded = json.dumps(input_material(image), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(encoded.encode('ascii')).hexdigest()


def derived_tag(image):
    return f"localhost/rr-tool/{image['name']}:in-{input_hash(image)[:16]}"


def _reject_image_id(value):
    if isinstance(value, dict):
        if 'image_id' in value:
            raise ValueError('image_id is not allowed in tool_images')
        for item in value.values():
            _reject_image_id(item)
    elif isinstance(value, list):
        for item in value:
            _reject_image_id(item)


def _inside(path, root):
    return path.startswith(root + '/')


def require_https(url):
    if not isinstance(url, str) or not url or len(url) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError('tool image fetch url must be https')
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if parts.scheme != 'https' or not host or parts.username is not None or parts.password is not None:
        raise ValueError('tool image fetch url must be https')
    return url


def validate_tool_images(manifest):
    images = manifest.get('tool_images')
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
        raise ValueError('tool_images must contain 1..8 images')
    _reject_image_id(images)
    declared = set(manifest['inputs'])
    names = set()
    for image in images:
        _validate_image(image, declared)
        if image['name'] in names:
            raise ValueError('duplicate tool image name: ' + image['name'])
        names.add(image['name'])
    return images


def _validate_image(image, declared):
    if not isinstance(image, dict):
        raise ValueError('tool image must be an object')
    allowed = {'name', 'containerfile', 'context', 'platform', 'inputs', 'fetch', 'tag', 'timeout_seconds'}
    required = allowed - {'fetch'}
    if set(image) - allowed or not required <= set(image):
        raise ValueError('tool image fields')
    name = image['name']
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError('tool image name')
    context = relpath(image['context'])
    containerfile = relpath(image['containerfile'])
    if not _inside(containerfile, context):
        raise ValueError('tool image containerfile must be inside context')
    platform = image['platform']
    if not isinstance(platform, str) or not PLATFORM_RE.fullmatch(platform):
        raise ValueError('tool image platform')
    inputs = image['inputs']
    if not isinstance(inputs, dict) or not 1 <= len(inputs) <= MAX_INPUTS:
        raise ValueError('tool image inputs')
    for path, digest in inputs.items():
        rel = relpath(path)
        if rel != path or not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
            raise ValueError('tool image inputs')
        if not _inside(rel, context):
            raise ValueError('tool image input must be inside context: ' + rel)
        if rel not in declared:
            raise ValueError('tool image input is not a package input: ' + rel)
    if containerfile not in inputs:
        raise ValueError('tool image containerfile must be a pinned input')
    timeout = image['timeout_seconds']
    if type(timeout) is not int or not 1 <= timeout <= 7200:
        raise ValueError('tool image timeout_seconds must be an integer from 1 to 7200')
    fetch = image.get('fetch', [])
    if not isinstance(fetch, list) or len(fetch) > MAX_FETCHES:
        raise ValueError('tool image fetch')
    dests = set()
    pinned = {str(PurePosixPath(path).relative_to(context)) for path in inputs}
    for entry in fetch:
        _validate_fetch(entry, declared, inputs)
        dest = entry['dest']
        if dest in dests:
            raise ValueError('duplicate tool image fetch dest')
        dests.add(dest)
        if dest in pinned:
            raise ValueError('tool image fetch dest overwrites a pinned input')
    if image['tag'] != derived_tag(image):
        raise ValueError('tool image tag does not match input hash')


def _validate_fetch(entry, declared, inputs):
    if not isinstance(entry, dict):
        raise ValueError('tool image fetch')
    allowed = {'url', 'url_from', 'sha256', 'dest'}
    if set(entry) - allowed or 'sha256' not in entry or 'dest' not in entry:
        raise ValueError('tool image fetch fields')
    if ('url' in entry) == ('url_from' in entry):
        raise ValueError('tool image fetch requires exactly one of url or url_from')
    if not isinstance(entry['sha256'], str) or not SHA_RE.fullmatch(entry['sha256']):
        raise ValueError('tool image fetch sha256')
    dest = relpath(entry['dest'])
    if dest != entry['dest']:
        raise ValueError('tool image fetch dest')
    if 'url' in entry:
        require_https(entry['url'])
        return
    url_from = entry['url_from']
    if not isinstance(url_from, str) or url_from.count('#') != 1:
        raise ValueError('tool image fetch url_from')
    path, key = url_from.split('#', 1)
    rel = relpath(path)
    if rel != path or not URL_FROM_KEY_RE.fullmatch(key):
        raise ValueError('tool image fetch url_from')
    if rel not in declared or rel not in inputs:
        raise ValueError('tool image fetch url_from is not a pinned input')


def validate_required_checks(manifest):
    checks = manifest.get('required_checks')
    if not isinstance(checks, list) or len(checks) > 50:
        raise ValueError('required_checks must be a list of at most 50 ids')
    for check in checks:
        if not isinstance(check, str) or not CHECK_RE.fullmatch(check):
            raise ValueError('required_checks id')
    if len(set(checks)) != len(checks):
        raise ValueError('duplicate required_checks')
    if checks and 'outputs/research.json' not in manifest['required_outputs']:
        raise ValueError('required_checks requires outputs/research.json')
    return checks


def validate_provenance(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 16:
        raise ValueError('provenance')
    for key, item in value.items():
        if not isinstance(key, str) or not PROVENANCE_KEY_RE.fullmatch(key):
            raise ValueError('provenance')
        if not isinstance(item, str) or not item or len(item) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in item):
            raise ValueError('provenance')
    if len(json.dumps(value, sort_keys=True)) > 4096:
        raise ValueError('provenance')
    return value


def check_tool_image_payload(manifest, payload_hashes):
    for image in manifest.get('tool_images') or []:
        for path, expected in image['inputs'].items():
            if payload_hashes.get(path) != expected:
                raise ValueError('tool image input hash mismatch: ' + path)


def _failure(name, reason, stderr=''):
    text = ' '.join(str(reason).split())
    if len(text) > 500:
        text = text[:500]
    message = f'tool image {name}: {text}'
    if stderr:
        data = stderr.encode('utf-8', errors='replace') if isinstance(stderr, str) else bytes(stderr)
        tail = data[-STDERR_TAIL:].decode('utf-8', errors='replace').strip()
        if tail:
            message += ': ' + tail
    return ToolImageError(message)


def _utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _docker_env():
    env = os.environ.copy()
    for key in ('RESEARCH_RUNNER_READ_TOKEN', 'RESEARCH_RUNNER_CONTROL_TOKEN', 'RESEARCH_RUNNER_CODEX_AUTH_FILE'):
        env.pop(key, None)
    return env


def _run(argv, timeout):
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=timeout, env=_docker_env())
    except subprocess.TimeoutExpired as error:
        raise ToolImageError('docker inspect timed out') from error


def _inspect(docker, tag):
    result = _run([docker, 'image', 'inspect', tag], 60)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        raise ToolImageError('docker image inspect returned invalid JSON') from error
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ToolImageError('docker image inspect returned an unexpected payload')
    return data[0]


def _label(info):
    config = info.get('Config')
    if not isinstance(config, dict):
        return None
    labels = config.get('Labels')
    if not isinstance(labels, dict):
        return None
    value = labels.get('research.input_hash')
    return value if isinstance(value, str) else None


def _group_alive(pid):
    probe = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True, text=True, timeout=10)
    if probe.returncode:
        return True
    for line in probe.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == str(pid) and not parts[1].startswith('Z'):
            return True
    return False


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _group_alive(pid):
            return
        time.sleep(0.05)
    raise ToolImageError('process group survived termination')


def _process_started(pid):
    probe = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True, timeout=5)
    return probe.stdout.strip() if probe.returncode == 0 else ''


def _opener():
    class Guard(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            try:
                require_https(newurl)
            except ValueError as error:
                raise ToolImageError('tool image fetch redirected off https') from error
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(Guard)


def _urlopen(url, timeout):
    return _opener().open(url, timeout=timeout)


def _download(url, dest, expected, cancel, deadline):
    try:
        require_https(url)
    except ValueError as error:
        raise ToolImageError(str(error)) from error
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + '.partial-' + uuid.uuid4().hex)
    hasher = hashlib.sha256()
    size = 0
    try:
        with _urlopen(url, timeout=30) as response:
            try:
                require_https(response.geturl())
            except ValueError as error:
                raise ToolImageError(str(error)) from error
            with temporary.open('wb') as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError('tool image preparation stopped by user')
                    if time.monotonic() >= deadline:
                        raise ToolImageError('fetch timed out')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FETCH_BYTES:
                        raise ToolImageError('fetch exceeds size limit')
                    hasher.update(chunk)
                    out.write(chunk)
        if hasher.hexdigest() != expected:
            raise ToolImageError('fetch sha256 mismatch')
        temporary.replace(dest)
    except ToolImageError:
        raise
    except InterruptedError:
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise ToolImageError('fetch failed') from error
    finally:
        temporary.unlink(missing_ok=True)


def _resolve_url(work, entry):
    if 'url' in entry:
        return entry['url']
    path, key = entry['url_from'].split('#', 1)
    file = Path(work) / path
    root = Path(work).resolve()
    if file.is_symlink() or not file.is_file():
        raise ToolImageError('fetch url_from is not a packaged file')
    resolved = file.resolve()
    if not resolved.is_relative_to(root):
        raise ToolImageError('fetch url_from escapes the workspace')
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as error:
        raise ToolImageError('fetch url_from file is not JSON') from error
    if not isinstance(data, dict) or not isinstance(data.get(key), str):
        raise ToolImageError('fetch url_from key is not a string')
    return data[key]


def _copy_context(work, context, dest):
    source = Path(work) / context
    root = Path(work).resolve()
    if source.is_symlink() or not source.is_dir():
        raise ToolImageError('tool image context is not a directory')
    if not source.resolve().is_relative_to(root):
        raise ToolImageError('tool image context escapes the workspace')
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or not dest.is_dir():
            dest.unlink()
        else:
            shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for current, dirs, files in os.walk(source, followlinks=False):
        relative = Path(current).relative_to(source)
        for name in list(dirs):
            path = Path(current) / name
            if path.is_symlink() or not path.is_dir():
                raise ToolImageError('tool image context contains a link or special file')
            (dest / relative / name).mkdir(exist_ok=True)
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                raise ToolImageError('tool image context contains a link or special file')
            target = dest / relative / name
            shutil.copyfile(path, target)
            target.chmod(0o644)


def _stream_build(name, argv, cancel, deadline, progress, marker):
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors='replace', start_new_session=True, env=_docker_env())
    tail = collections.deque()
    tail_bytes = 0
    lock = threading.Lock()
    emitted = 0

    def remember(line):
        nonlocal tail_bytes
        raw = line.encode('utf-8', errors='replace')
        tail.append(raw)
        tail_bytes += len(raw)
        while tail_bytes > STDERR_TAIL and tail:
            tail_bytes -= len(tail.popleft())

    def pump(stream, stream_name, keep):
        nonlocal emitted
        try:
            while True:
                line = stream.readline(8192)
                if not line:
                    return
                if keep:
                    with lock:
                        remember(line)
                text = line.rstrip('\n')[:2000]
                if not text:
                    continue
                with lock:
                    if emitted >= 200 or progress is None:
                        continue
                    emitted += 1
                    send = emitted
                if send == 200:
                    text = '[tool image log truncated]'
                try:
                    progress({'name': name, 'stream': stream_name, 'line': text})
                except ValueError:
                    return
        finally:
            stream.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, 'stdout', False), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, 'stderr', True), daemon=True),
    ]
    for thread in threads:
        thread.start()
    if marker is not None:
        marker.write_text(json.dumps({'pid': proc.pid, 'started': _process_started(proc.pid)}))
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                _kill_group(proc.pid)
                proc.wait(timeout=5)
                raise InterruptedError('tool image preparation stopped by user')
            if time.monotonic() >= deadline:
                _kill_group(proc.pid)
                proc.wait(timeout=5)
                raise _failure(name, 'timed out', b''.join(tail).decode('utf-8', errors='replace'))
            time.sleep(0.05)
        for thread in threads:
            thread.join(timeout=2)
        return proc.returncode, b''.join(tail).decode('utf-8', errors='replace')
    finally:
        if proc.poll() is None:
            _kill_group(proc.pid)
            proc.wait(timeout=5)


def _record(name, spec, info, action, seconds):
    image_id = info.get('Id')
    architecture = info.get('Architecture')
    if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
        raise _failure(name, 'docker image id missing')
    if not isinstance(architecture, str):
        raise _failure(name, 'docker image architecture missing')
    expected = spec['platform'].split('/', 1)[1]
    if architecture != expected:
        raise _failure(name, f'architecture mismatch: expected {expected}, got {architecture}')
    created = info.get('Created')
    return {
        'name': name,
        'tag': spec['tag'],
        'image_id': image_id,
        'architecture': architecture,
        'input_hash': input_hash(spec),
        'action': action,
        'seconds': round(seconds, 3),
        'built_at': created if isinstance(created, str) and created else _utc_now(),
    }


def _provision_one(job, spec, progress, cancel, docker):
    name = spec['name']
    started = time.monotonic()
    deadline = started + spec['timeout_seconds']
    digest = input_hash(spec)
    if cancel is not None and cancel.is_set():
        raise InterruptedError('tool image preparation stopped by user')
    current = _inspect(docker, spec['tag'])
    if current is not None and _label(current) == digest:
        record = _record(name, spec, current, 'reused', time.monotonic() - started)
        if progress:
            progress({'name': name, 'stream': 'stdout', 'line': 'reused ' + spec['tag']})
        return record
    work = Path(job) / 'work'
    build = Path(job) / 'tool-build' / name
    _copy_context(work, spec['context'], build)
    for entry in spec.get('fetch') or []:
        if time.monotonic() >= deadline:
            raise _failure(name, 'timed out')
        dest = build / entry['dest']
        if dest.exists() or dest.is_symlink():
            raise _failure(name, 'fetch destination already exists')
        _download(_resolve_url(work, entry), dest, entry['sha256'], cancel, deadline)
    relative = PurePosixPath(spec['containerfile']).relative_to(spec['context'])
    dockerfile = build / relative
    if not dockerfile.is_file() or dockerfile.is_symlink():
        raise _failure(name, 'containerfile missing from build context')
    argv = [docker, 'build', '--platform', spec['platform'], '--label', 'research.input_hash=' + digest,
            '-t', spec['tag'], '-f', str(dockerfile), str(build)]
    if progress:
        progress({'name': name, 'stream': 'stdout', 'line': 'building ' + spec['tag']})
    marker = Path(job) / 'assets' / 'tool-build-runtime.json'
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        code, stderr = _stream_build(name, argv, cancel, deadline, progress, marker)
    finally:
        marker.unlink(missing_ok=True)
    if code != 0:
        raise _failure(name, 'build exited ' + str(code), stderr)
    built = _inspect(docker, spec['tag'])
    if built is None:
        raise _failure(name, 'built image is missing')
    if _label(built) != digest:
        raise _failure(name, 'build did not record input hash')
    return _record(name, spec, built, 'built', time.monotonic() - started)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o644)
    temporary.replace(path)


def provision(job, manifest, *, progress, cancel, docker='docker'):
    """Build every declared image. Raise ToolImageError or InterruptedError.

    The receipt is written only after every image succeeds, so a failed
    attempt cannot look provisioned.
    """
    job = Path(job)
    receipt_path = job / 'assets' / 'tool-images.json'
    exposed = job / 'work' / '.runner' / 'tool-images.json'
    receipt_path.unlink(missing_ok=True)
    if exposed.is_symlink() or exposed.is_file():
        if exposed.is_file() and not exposed.is_symlink():
            exposed.chmod(0o644)
        exposed.unlink()
    images = []
    try:
        for spec in manifest['tool_images']:
            try:
                images.append(_provision_one(job, spec, progress, cancel, docker))
            except InterruptedError:
                raise
            except ToolImageError as error:
                text = str(error)
                if text.startswith('tool image ' + spec['name'] + ':'):
                    raise
                raise _failure(spec['name'], text) from error
            except Exception as error:
                raise _failure(spec.get('name', 'tool-image'), error) from error
    except Exception:
        receipt_path.unlink(missing_ok=True)
        raise
    _write_json(receipt_path, {'format': FORMAT, 'images': images})
    return images
