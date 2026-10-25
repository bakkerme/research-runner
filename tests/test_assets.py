"""Independent loaders, strict materialization and observable preparation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

from runner.assets import PROTOCOL, inventory, invoke, prepare, read_config, validate_loaders
from runner.package import assemble, validate_manifest
from runner.service import Service
from runner.worker import dependency_receipt_note

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def asset(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'model.bin').write_bytes(b'model weights' * 1000)
    (source / 'empty').mkdir()
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'model': {'source': str(source), 'resolved_id': 'revision-1'}}))
    config = {'command': [sys.executable, str(ROOT / 'examples/asset-loader/loader.py'), '--catalog', str(catalog)]}
    dep = {'asset_type': 'local-files', 'asset_id': 'model', 'destination': 'inputs/model'}
    return source, config, dep


def job(tmp_path):
    root = tmp_path / 'job'
    (root / 'work').mkdir(parents=True)
    return root


def test_config_optional_limits_and_cli_handshake(tmp_path, asset):
    source, config, dep = asset
    path = tmp_path / 'loaders.toml'
    path.write_text('[asset_loaders.local-files]\ncommand = ' + json.dumps(config['command']) + '\n')
    loaders = read_config(path)
    assert 'max_bytes' not in loaders['local-files'] and 'timeout_seconds' not in loaders['local-files']
    assert validate_loaders(loaders)['loaders'][0]['loader_version'] == 'example-1'
    result = subprocess.run([sys.executable, '-m', 'runner.cli', 'validate-loaders', '--config', str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['valid']
    for extra in ('max_bytes = 0', 'max_bytes = 1.5', 'timeout_seconds = -1', 'timeout_seconds = true', 'unknown = 1'):
        path.write_text('[asset_loaders.local-files]\ncommand = ' + json.dumps(config['command']) + '\n' + extra)
        with pytest.raises(ValueError):
            read_config(path)
    path.write_text('[asset_loaders.local-files]\ncommand = ["/does/not/exist"]\n')
    with pytest.raises(ValueError, match='missing'):
        read_config(path)


def test_standalone_example_progress_receipt_and_locked_resume(tmp_path, asset):
    source, config, dep = asset
    root = job(tmp_path)
    events = []
    receipt = prepare(root, [dep], {'local-files': config}, progress=events.append, cancel=threading.Event(), deadline=None)
    assert (root / 'work/inputs/model/model.bin').read_bytes() == (source / 'model.bin').read_bytes()
    assert any('bytes_completed' in event for event in events)
    assert receipt['assets'][0]['sha256'] == inventory(source)['sha256']
    assert receipt['assets'][0]['resolved_id'] == 'revision-1'
    # Neither an alias change nor the original source's disappearance changes a retained input.
    shutil.rmtree(source)
    assert prepare(root, [dep], {'local-files': config}, progress=events.append, cancel=threading.Event(), deadline=None) == receipt
    (root / 'work/inputs/model/model.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        prepare(root, [dep], {'local-files': config}, progress=events.append, cancel=threading.Event(), deadline=None)


def test_digest_failure_never_materializes_destination(tmp_path, asset):
    source, config, dep = asset
    root = job(tmp_path)
    original = inventory(source)['sha256']
    (root / 'assets').mkdir()
    (root / 'assets/lock.json').write_text(json.dumps({'dependencies': [dep], 'assets': [
        {'request': dep, 'resolved_id': 'revision-1', 'sha256': original, 'loader_version': 'example-1'}]}))
    (source / 'model.bin').write_bytes(b'changed after locking')
    with pytest.raises(ValueError, match='loader error'):
        prepare(root, [dep], {'local-files': config}, progress=lambda x: None, cancel=threading.Event(), deadline=None)
    assert not (root / 'work/inputs/model').exists()
    assert not (root / 'assets/receipt.json').exists()


def test_quota_and_unsafe_staged_files(tmp_path, asset):
    source, config, dep = asset
    with pytest.raises(ValueError, match='max_bytes'):
        prepare(job(tmp_path), [dep], {'local-files': dict(config, max_bytes=1)}, progress=lambda x: None, cancel=threading.Event(), deadline=None)
    (source / 'link').symlink_to(source / 'model.bin')
    with pytest.raises(ValueError, match='links'):
        inventory(source)


@pytest.mark.parametrize('destination', ['/tmp/escape', '../escape', 'outputs/private', 'inputs', 'AGENTS.md', 'package.json', 'manifest.json'])
def test_manifest_rejects_dependency_collisions(destination):
    manifest = json.loads((ROOT / 'sample-mvp/manifest.json').read_text())
    manifest['dependencies'] = [{'asset_type': 'local-files', 'asset_id': 'model', 'destination': destination}]
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_manifest_accepts_optional_dependencies_and_rejects_duplicates(asset):
    _, _, dep = asset
    manifest = json.loads((ROOT / 'sample-mvp/manifest.json').read_text())
    validate_manifest(manifest)
    manifest['dependencies'] = [dep]
    validate_manifest(manifest)
    manifest['dependencies'].append(dep)
    with pytest.raises(ValueError, match='overlaps'):
        validate_manifest(manifest)


def script(tmp_path, code):
    path = tmp_path / 'fake.py'
    path.write_text(code)
    return {'command': [sys.executable, str(path)]}


def test_timeout_cancellation_protocol_errors_and_secret_env(tmp_path, monkeypatch):
    config = script(tmp_path, 'import time; time.sleep(20)')
    with pytest.raises(TimeoutError):
        invoke(dict(config, timeout_seconds=.1), {'operation': 'describe'})
    cancel = threading.Event()
    timer = threading.Timer(.1, cancel.set)
    timer.start()
    with pytest.raises(InterruptedError):
        invoke(config, {'operation': 'describe'}, cancel=cancel)
    timer.join()
    config = script(tmp_path, "print('x' * 65537)")
    with pytest.raises(ValueError, match='64 KiB'):
        invoke(config, {})
    monkeypatch.setenv('PRIVATE_LOADER_TOKEN', 'secret')
    config = script(tmp_path, "import os,json; assert 'PRIVATE_LOADER_TOKEN' not in os.environ; print(json.dumps({'event':'result','protocol':'runner-asset-loader/v1','ok':True}))")
    assert invoke(config, {}) == {'ok': True}


def wait(service, jid):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        state = service.status(jid)['state']
        if state not in ('running', 'finalizing'):
            return state
        time.sleep(.02)
    raise AssertionError('job did not finish')


def archive(tmp_path, dep):
    source = tmp_path / 'payload'
    shutil.copytree(ROOT / 'sample-mvp', source)
    path = source / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['dependencies'] = [dep]
    path.write_text(json.dumps(manifest))
    output = tmp_path / 'package.rrpkg'
    assemble(source, output)
    return output


def test_service_prepares_on_start_and_publishes_authoritative_receipt(tmp_path, asset, monkeypatch):
    _, config, dep = asset
    service = Service(tmp_path / 'state', loaders={'local-files': config})
    try:
        jid, _ = service.submit(archive(tmp_path, dep), 'submit')
        assert not (service.path(jid) / 'assets').exists()
        publish = service._publish
        def replace_receipt(*args, **kwargs):
            (service.path(jid) / 'work/outputs/dependencies.json').write_text('{"forged":true}')
            return publish(*args, **kwargs)
        monkeypatch.setattr(service, '_publish', replace_receipt)
        service.launch(jid, 'start')
        assert wait(service, jid) == 'finished', service.status(jid)
        events = service.store.events(jid)
        assert any(event['kind'] == 'asset_progress' and 'bytes_completed' in event['data'] for event in events)
        data, _ = service.download(jid, 'outputs/dependencies.json')
        assert json.loads(data)['assets'][0]['resolved_id'] == 'revision-1'
        visible = service.path(jid) / 'work/.runner/dependencies.json'
        assert json.loads(visible.read_text()) == json.loads(data)
        assert visible.stat().st_mode & 0o777 == 0o444
        assert not visible.is_symlink()
    finally:
        service._owner.close()


def test_receipt_copy_replaces_read_only_file_and_refuses_links(tmp_path):
    archive = tmp_path / 'job.rrpkg'
    assemble(ROOT / 'sample-mvp', archive)
    service = Service(tmp_path / 'state')
    try:
        jid, _ = service.submit(archive, 'submit')
        assets = service.path(jid) / 'assets'
        assets.mkdir()
        receipt = {'format': 'runner-assets-receipt/v1', 'assets': []}
        (assets / 'receipt.json').write_text(json.dumps(receipt))
        service._expose_dependency_receipt(jid)
        service._expose_dependency_receipt(jid)
        visible = service.path(jid) / 'work' / '.runner' / 'dependencies.json'
        assert json.loads(visible.read_text()) == receipt
        assert visible.stat().st_mode & 0o777 == 0o444
        outside = tmp_path / 'outside'
        outside.mkdir()
        visible.unlink()
        view = visible.parent
        view.rmdir()
        view.symlink_to(outside)
        with pytest.raises(ValueError, match='link'):
            service._expose_dependency_receipt(jid)
        assert not (outside / 'dependencies.json').exists()
    finally:
        service._owner.close()


def test_docker_receipt_mount_skips_symlinks(tmp_path):
    service = Service(tmp_path / 'state')
    try:
        work = tmp_path / 'work'
        view = work / '.runner'
        view.mkdir(parents=True)
        assert service._dependency_receipt_mount(work) == ['-v', str(view.resolve()) + ':/job/.runner:ro']
        view.rmdir()
        outside = tmp_path / 'outside'
        outside.mkdir()
        view.symlink_to(outside)
        assert service._dependency_receipt_mount(work) == []
    finally:
        service._owner.close()


def test_dependency_receipt_note_names_regular_file_only(tmp_path):
    root = tmp_path / 'work'
    view = root / '.runner'
    view.mkdir(parents=True)
    path = view / 'dependencies.json'
    path.write_text('{}')
    note = dependency_receipt_note(root)
    assert str(path) in note and 'outputs/dependencies.json' in note and 'read-only' in note
    path.unlink()
    assert dependency_receipt_note(root) == ''
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'dependencies.json').write_text('{}')
    view.rmdir()
    view.symlink_to(outside)
    assert dependency_receipt_note(root) == ''


def test_service_stop_during_loader_and_unknown_type(tmp_path, asset):
    _, config, dep = asset
    config = script(tmp_path, "import json,sys,time; r=json.load(sys.stdin); print(json.dumps({'event':'progress','message':'Downloading','bytes_completed':1,'bytes_total':100}),flush=True); time.sleep(20)")
    service = Service(tmp_path / 'state', loaders={'local-files': config})
    try:
        jid, _ = service.submit(archive(tmp_path, dep), 'submit')
        service.launch(jid, 'start')
        end = time.monotonic() + 5
        while time.monotonic() < end and not any(e['kind'] == 'asset_progress' for e in service.store.events(jid)):
            time.sleep(.02)
        assert service.stop(jid, 'stop')['state'] == 'interrupted'
        assert not (service.path(jid) / 'runtime.json').exists()
        assert not (service.path(jid) / 'assets/loader-runtime.json').exists()
        service.loaders = {}
        service.launch(jid, 'retry')
        assert wait(service, jid) == 'failed'
        assert 'unregistered' in service.status(jid)['error']
    finally:
        service._owner.close()


def test_quiet_loader_gets_heartbeat_without_optional_limits(tmp_path):
    config = script(tmp_path, "import time,json; time.sleep(5.2); print(json.dumps({'event':'result','protocol':'runner-asset-loader/v1','ok':True}))")
    events = []
    assert invoke(config, {}, progress=events.append) == {'ok': True}
    assert any(event['message'] == 'Still preparing asset' for event in events)
    with pytest.raises(TimeoutError):
        invoke(config, {}, deadline=time.time() + .1)


def test_runner_rejects_wrong_content_even_when_loader_claims_success(tmp_path, asset):
    _, _, dep = asset
    root = job(tmp_path)
    config = script(tmp_path, """import json,sys,pathlib
r=json.load(sys.stdin)
if r['operation']=='describe':
 v={'loader_version':'test','operations':['resolve','materialize']}
elif r['operation']=='resolve':
 v={'resolved_id':'fixed','sha256':'0'*64}
else:
 pathlib.Path(r['staging_directory'],'wrong.bin').write_bytes(b'wrong')
 v={'materialized':True}
print(json.dumps(dict(event='result',protocol='runner-asset-loader/v1',**v)))
""")
    with pytest.raises(ValueError, match='digest mismatch'):
        prepare(root, [dep], {'local-files': config}, progress=lambda x: None, cancel=threading.Event(), deadline=None)
    assert not (root / 'work/inputs/model').exists()


def test_workspace_symlink_is_rejected_on_resume(tmp_path, asset):
    _, config, dep = asset
    root = job(tmp_path)
    options = dict(progress=lambda x: None, cancel=threading.Event(), deadline=None)
    prepare(root, [dep], {'local-files': config}, **options)
    shutil.rmtree(root / 'work/inputs')
    (root / 'work/inputs').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        prepare(root, [dep], {'local-files': config}, **options)
    assert not (tmp_path / 'model').exists()
