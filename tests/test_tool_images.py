"""Declared tool images are verified, built or reused, and fail before the worker."""
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from runner.package import assemble, hash_file, validate_manifest, verify
from runner.service import Service
from runner.tool_images import derived_tag, input_hash
from tests.v3_support import IMAGE_ID, ROOT, group_gone, install_docker, pack, wait_job, write_payload


def manifest_of(source):
    return json.loads((source / 'manifest.json').read_text())


def test_hash_and_tag_are_canonical():
    image = {
        'name': 'demo', 'containerfile': 'tools/demo/Containerfile', 'context': 'tools/demo',
        'platform': 'linux/arm64',
        'inputs': {'tools/demo/b.txt': 'b' * 64, 'tools/demo/a.txt': 'a' * 64},
        'timeout_seconds': 30,
    }
    reversed_inputs = dict(image)
    reversed_inputs['inputs'] = {'tools/demo/a.txt': 'a' * 64, 'tools/demo/b.txt': 'b' * 64}
    assert input_hash(image) == input_hash(reversed_inputs)
    assert derived_tag(image) == 'localhost/rr-tool/demo:in-' + input_hash(image)[:16]
    fetched = dict(image, fetch=[
        {'url': 'https://example.test/a', 'sha256': 'aa' * 32, 'dest': 'a.bin'},
        {'url': 'https://example.test/b', 'sha256': 'bb' * 32, 'dest': 'b.bin'},
    ])
    swapped = dict(fetched, fetch=list(reversed(fetched['fetch'])))
    assert input_hash(fetched) != input_hash(swapped)
    assert input_hash(image) == input_hash(dict(image, fetch=[]))


def test_verify_accepts_v3_and_rejects_bad_declarations(tmp_path):
    source, manifest = write_payload(tmp_path)
    archive = tmp_path / 'ok.rrpkg'
    assemble(source, archive)
    verified, _ = verify(archive)
    assert verified['format'] == 'research-package/v3'
    assert verified['tool_images'][0]['tag'] == derived_tag(verified['tool_images'][0])

    def reject(mutate, match):
        broken = json.loads(json.dumps(manifest))
        mutate(broken)
        (source / 'manifest.json').write_text(json.dumps(broken), encoding='utf-8')
        with pytest.raises(ValueError, match=match):
            validate_manifest(broken)

    reject(lambda m: m['tool_images'][0].__setitem__('tag', 'localhost/rr-tool/demo:in-' + '0' * 16), 'tag does not match')
    reject(lambda m: m['tool_images'][0].__setitem__('containerfile', 'brief.md'), 'inside context')
    reject(lambda m: m['tool_images'][0].__setitem__('containerfile', '/etc/passwd'), 'unsafe package path')
    reject(lambda m: m['tool_images'][0].__setitem__('context', '../outside'), 'unsafe package path')
    reject(lambda m: m['tool_images'][0].__setitem__('image_id', 'sha256:' + 'c' * 64), 'image_id')
    reject(lambda m: m['tool_images'][0].__setitem__('fetch', [{
        'url': 'http://example.test/tool.zip', 'sha256': 'd' * 64, 'dest': 'tool.zip',
    }]), 'https')
    reject(lambda m: m['tool_images'][0].__setitem__('fetch', [{
        'url': 'https://example.test/tool.zip', 'sha256': 'd' * 64, 'dest': 'tool.zip', 'image_id': 'nope',
    }]), 'image_id')
    v2 = json.loads((ROOT / 'sample-mvp' / 'manifest.json').read_text())
    v2['tool_images'] = manifest['tool_images']
    with pytest.raises(ValueError, match='manifest fields'):
        validate_manifest(v2)


def test_verify_rejects_input_hash_that_does_not_match_bytes(tmp_path):
    source, manifest = write_payload(tmp_path)
    image = manifest['tool_images'][0]
    image['inputs']['tools/demo/Containerfile'] = 'f' * 64
    image['tag'] = derived_tag(image)
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='tool image input hash mismatch'):
        assemble(source, tmp_path / 'bad.rrpkg')


def _run(tmp_path, monkeypatch, *, manifest_kwargs=None, docker_env=None, present=None, launches=None):
    monkeypatch.delenv('RR_DOCKER_SLEEP', raising=False)
    monkeypatch.delenv('RR_DOCKER_HOLD', raising=False)
    monkeypatch.delenv('RR_DOCKER_EXIT', raising=False)
    monkeypatch.delenv('RR_DOCKER_ARCH', raising=False)
    monkeypatch.delenv('RR_DOCKER_STDERR', raising=False)
    state = install_docker(tmp_path, monkeypatch)
    for key, value in (docker_env or {}).items():
        monkeypatch.setenv(key, value)
    archive, manifest = pack(tmp_path, **(manifest_kwargs or {}))
    if present == 'match':
        image = manifest['tool_images'][0]
        (state / 'present.json').write_text(json.dumps({
            'Id': IMAGE_ID, 'Architecture': 'arm64', 'Created': '2026-01-01T00:00:00Z',
            'Config': {'Labels': {'research.input_hash': input_hash(image)}},
        }), encoding='utf-8')
    elif present == 'mismatch':
        (state / 'present.json').write_text(json.dumps({
            'Id': IMAGE_ID, 'Architecture': 'arm64', 'Created': '2026-01-01T00:00:00Z',
            'Config': {'Labels': {'research.input_hash': '0' * 64}},
        }), encoding='utf-8')
    elif present == 'arch':
        image = manifest['tool_images'][0]
        (state / 'present.json').write_text(json.dumps({
            'Id': IMAGE_ID, 'Architecture': 'amd64', 'Created': '2026-01-01T00:00:00Z',
            'Config': {'Labels': {'research.input_hash': input_hash(image)}},
        }), encoding='utf-8')
    service = Service(tmp_path / 'state')
    if launches is not None:
        original = service._launch_worker
        def wrapped(*args, **kwargs):
            launches.append(args)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, '_launch_worker', wrapped)
    try:
        jid, _ = service.submit(archive, 'submit')
        service.launch(jid, 'start')
        return service, jid, state, manifest
    except Exception:
        service._owner.close()
        raise


def test_absent_image_is_built_once_before_the_worker_starts(tmp_path, monkeypatch):
    service, jid, state, manifest = _run(tmp_path, monkeypatch)
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'finished', job
        assert job['outcome'] == 'complete'
        receipt = json.loads((service.path(jid) / 'assets' / 'tool-images.json').read_text())
        assert receipt['format'] == 'runner-tool-images/v1'
        assert receipt['images'][0]['action'] == 'built'
        assert receipt['images'][0]['image_id'] == IMAGE_ID
        assert receipt['images'][0]['architecture'] == 'arm64'
        assert receipt['images'][0]['tag'] == manifest['tool_images'][0]['tag']
        visible = service.path(jid) / 'work' / '.runner' / 'tool-images.json'
        assert visible.stat().st_mode & 0o777 == 0o444
        assert json.loads(visible.read_text()) == receipt
        published, _ = service.download(jid, 'outputs/tool-images.json')
        assert json.loads(published) == receipt
        builds = (state / 'builds').read_text().splitlines()
        assert len(builds) == 1
        argv = json.loads(builds[0])
        assert argv[0] == 'build' and '--platform' in argv
        assert argv[argv.index('--platform') + 1] == 'linux/arm64'
        assert argv[argv.index('--label') + 1] == 'research.input_hash=' + receipt['images'][0]['input_hash']
        assert argv[argv.index('-t') + 1] == receipt['images'][0]['tag']
        assert any(event['kind'] == 'tool_image_progress' for event in service.store.events(jid))
        assert 'dependencies' not in manifest
    finally:
        service._owner.close()


def test_matching_label_is_reused_without_a_build(tmp_path, monkeypatch):
    service, jid, state, _ = _run(tmp_path, monkeypatch, present='match')
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'finished', job
        receipt = json.loads((service.path(jid) / 'assets' / 'tool-images.json').read_text())
        assert receipt['images'][0]['action'] == 'reused'
        assert not (state / 'builds').exists()
    finally:
        service._owner.close()


def test_label_mismatch_rebuilds(tmp_path, monkeypatch):
    service, jid, state, _ = _run(tmp_path, monkeypatch, present='mismatch')
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'finished', job
        receipt = json.loads((service.path(jid) / 'assets' / 'tool-images.json').read_text())
        assert receipt['images'][0]['action'] == 'built'
        assert (state / 'builds').read_text().count('\n') == 1
    finally:
        service._owner.close()


def test_build_failure_stops_before_the_worker_and_codex(tmp_path, monkeypatch):
    launches = []
    service, jid, _, _ = _run(tmp_path, monkeypatch, manifest_kwargs={'name': 'ps1-replay'}, launches=launches,
                              docker_env={'RR_DOCKER_EXIT': '1', 'RR_DOCKER_STDERR': 'compilation failed\n'})
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'failed', job
        assert job['error'].startswith('tool image ps1-replay:')
        assert 'compilation failed' in job['error']
        assert launches == []
        assert not any(event['kind'] == 'codex' for event in service.store.events(jid))
        assert not (service.path(jid) / 'runtime.json').exists()
    finally:
        service._owner.close()


def test_architecture_mismatch_fails_the_job(tmp_path, monkeypatch):
    launches = []
    service, jid, state, _ = _run(tmp_path, monkeypatch, present='arch', launches=launches)
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'failed', job
        assert job['error'].startswith('tool image demo:')
        assert 'architecture mismatch' in job['error']
        assert launches == []
        assert not (state / 'builds').exists()
    finally:
        service._owner.close()


def test_matching_fetch_is_written_before_the_build(tmp_path, monkeypatch):
    payload = b'hello-tool'
    digest = hashlib.sha256(payload).hexdigest()

    class Response:
        def __init__(self):
            self._data = payload
        def read(self, count):
            chunk, self._data = self._data[:count], self._data[count:]
            return chunk
        def geturl(self):
            return 'https://example.test/tool.bin'
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr('runner.tool_images._urlopen', lambda _url, timeout: Response())
    service, jid, state, _ = _run(
        tmp_path, monkeypatch,
        manifest_kwargs={'fetch': [{'url': 'https://example.test/tool.bin', 'sha256': digest, 'dest': 'tool.bin'}]})
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'finished', job
        builds = [json.loads(line) for line in (state / 'builds').read_text().splitlines()]
        assert (Path(builds[0][-1]) / 'tool.bin').read_bytes() == payload
    finally:
        service._owner.close()


def test_fetch_sha_mismatch_fails_before_build(tmp_path, monkeypatch):
    launches = []

    class Response:
        def __init__(self):
            self._done = False
        def read(self, _n):
            if self._done:
                return b''
            self._done = True
            return b'not-the-bytes'
        def geturl(self):
            return 'https://example.test/tool.bin'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr('runner.tool_images._urlopen', lambda url, timeout: Response())
    fetch = [{'url': 'https://example.test/tool.bin', 'sha256': 'd' * 64, 'dest': 'tool.bin'}]
    service, jid, state, _ = _run(tmp_path, monkeypatch, launches=launches, manifest_kwargs={'fetch': fetch, 'name': 'ps1-replay'})
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'failed', job
        assert job['error'].startswith('tool image ps1-replay:')
        assert 'sha256' in job['error']
        assert launches == []
        assert not (state / 'builds').exists()
    finally:
        service._owner.close()


def test_build_timeout_kills_the_process_group(tmp_path, monkeypatch):
    state = install_docker(tmp_path, monkeypatch)
    monkeypatch.setenv('RR_DOCKER_HOLD', '1')
    source, manifest = write_payload(tmp_path, name='ps1-replay')
    manifest['tool_images'][0]['timeout_seconds'] = 3
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    archive = tmp_path / 'job.rrpkg'
    assemble(source, archive)
    launches = []
    service = Service(tmp_path / 'state')
    original = service._launch_worker
    def wrapped(*args, **kwargs):
        launches.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, '_launch_worker', wrapped)
    try:
        jid, _ = service.submit(archive, 'submit')
        service.launch(jid, 'start')
        job = wait_job(service, jid, seconds=20)
        assert job['state'] == 'failed', job
        assert job['error'].startswith('tool image ps1-replay:')
        assert 'timed out' in job['error']
        assert launches == []
        assert (state / 'ready').is_file()
        assert group_gone(int((state / 'group').read_text()))
    finally:
        service._owner.close()


def test_stop_during_build_interrupts_and_kills_the_group(tmp_path, monkeypatch):
    launches = []
    service, jid, state, _ = _run(tmp_path, monkeypatch, launches=launches, docker_env={'RR_DOCKER_HOLD': '1'})
    try:
        deadline = time.monotonic() + 10
        while not (state / 'ready').exists():
            if time.monotonic() > deadline:
                raise AssertionError(service.store.get(jid))
            time.sleep(0.02)
        assert service.stop(jid, 'stop')['state'] == 'interrupted'
        assert launches == []
        assert group_gone(int((state / 'group').read_text()))
        assert not any(event['kind'] == 'codex' for event in service.store.events(jid))
    finally:
        service._owner.close()


def test_deadline_is_extended_by_provisioning_time(tmp_path, monkeypatch):
    service, jid, _, manifest = _run(tmp_path, monkeypatch, docker_env={'RR_DOCKER_SLEEP': '0.6'})
    try:
        job = wait_job(service, jid)
        assert job['state'] == 'finished', job
        budget = manifest['resources']['time_seconds']
        extension = job['deadline'] - job['started_at'] - budget
        assert extension >= 0.4
        termination = manifest['policy']['termination_seconds']
        finalization = manifest['policy']['finalization_seconds']
        assert job['terminate_at'] - (job['started_at'] + budget - termination) == pytest.approx(extension, abs=0.05)
        assert job['finalize_at'] - (job['started_at'] + budget - finalization) == pytest.approx(extension, abs=0.05)
        receipt = json.loads((service.path(jid) / 'assets' / 'tool-images.json').read_text())
        assert receipt['images'][0]['seconds'] == pytest.approx(extension, abs=0.5)
    finally:
        service._owner.close()


def test_v3_without_tool_images_and_v1_v2_do_not_need_docker(tmp_path, monkeypatch):
    bindir = tmp_path / 'no-docker'
    bindir.mkdir()
    docker = bindir / 'docker'
    docker.write_text('#!/bin/sh\nexit 99\n', encoding='utf-8')
    docker.chmod(0o755)
    monkeypatch.setenv('PATH', str(bindir) + os.pathsep + os.environ.get('PATH', ''))
    source = tmp_path / 'v3'
    source.mkdir()
    for name in ('brief.md', 'AGENTS.md', 'inputs/fixture.json', 'manifest.json'):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
    original = json.loads((ROOT / 'sample-mvp' / 'manifest.json').read_text())
    original['format'] = 'research-package/v3'
    (source / 'manifest.json').write_text(json.dumps(original), encoding='utf-8')
    (source / 'brief.md').write_text((ROOT / 'sample-mvp' / 'brief.md').read_text(), encoding='utf-8')
    (source / 'AGENTS.md').write_text((ROOT / 'sample-mvp' / 'AGENTS.md').read_text(), encoding='utf-8')
    (source / 'inputs' / 'fixture.json').write_text((ROOT / 'sample-mvp' / 'inputs' / 'fixture.json').read_text(), encoding='utf-8')
    archive = tmp_path / 'v3.rrpkg'
    assemble(source, archive)
    verify(archive)
    service = Service(tmp_path / 'state')
    try:
        jid, _ = service.submit(archive, 'submit')
        service.launch(jid, 'start')
        assert wait_job(service, jid)['state'] == 'finished'
    finally:
        service._owner.close()
    for folder in ('sample', 'sample-mvp'):
        packed = tmp_path / (folder + '.rrpkg')
        assemble(ROOT / folder, packed)
        verify(packed)


def test_skill_inside_context_must_be_pinned_before_verify(tmp_path):
    source, manifest = write_payload(tmp_path)
    skill = source / 'tools' / 'demo' / 'SKILL.md'
    skill.write_text('skill\n', encoding='utf-8')
    manifest['skills'] = ['tools/demo/SKILL.md']
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='not pinned: tools/demo/SKILL.md'):
        validate_manifest(manifest)
    manifest['tool_images'][0]['inputs']['tools/demo/SKILL.md'] = hash_file(skill)
    manifest['tool_images'][0]['tag'] = derived_tag(manifest['tool_images'][0])
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    archive = tmp_path / 'skill.rrpkg'
    assemble(source, archive)
    verified, _ = verify(archive)
    assert 'tools/demo/SKILL.md' in verified['tool_images'][0]['inputs']


def test_unpinned_context_file_cannot_share_an_image_identity(tmp_path, monkeypatch):
    source, manifest = write_payload(tmp_path / 'decl')
    (source / 'tools' / 'demo' / 'extra.txt').write_text('not pinned\n', encoding='utf-8')
    manifest['inputs'].append('tools/demo/extra.txt')
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='not pinned'):
        validate_manifest(manifest)
    state = install_docker(tmp_path, monkeypatch)
    archive, _ = pack(tmp_path)
    service = Service(tmp_path / 'state')
    try:
        jid, _ = service.submit(archive, 'submit')
        (service.path(jid) / 'work' / 'tools' / 'demo' / 'extra.txt').write_text('later\n', encoding='utf-8')
        launches = []
        original = service._launch_worker
        monkeypatch.setattr(service, '_launch_worker', lambda *args, **kwargs: launches.append(args) or original(*args, **kwargs))
        service.launch(jid, 'start')
        job = wait_job(service, jid)
        assert job['state'] == 'failed', job
        assert 'unpinned context file tools/demo/extra.txt' in job['error']
        assert launches == []
        assert not (state / 'builds').exists()
    finally:
        service._owner.close()


def test_example_package_verifies(tmp_path):
    archive = tmp_path / 'example.rrpkg'
    assemble(ROOT / 'examples' / 'tool-image', archive)
    manifest, _ = verify(archive)
    assert manifest['format'] == 'research-package/v3'
    assert manifest['tool_images'][0]['tag'] == derived_tag(manifest['tool_images'][0])
    assert manifest['required_checks'] == ['tool-image-provisioned']
