"""Producer-only tests: also run against the installed wheel without server extras."""
import importlib.abc
import json
from pathlib import Path
import subprocess
import sys

import pytest

from runner import Client, ResearchRepo
from runner.package import digest, verify
from runner.protocol import ResearchReport, schemas, validate_research
from runner.repo import inspect_collection


def report():
    return {
        'format': 'runner-research/v1', 'scope': 'A deterministic fixture, not a scientific observation',
        'findings': [{'id': 'claim', 'claim': 'The supplied fixture contains a', 'confidence': 'supported',
                      'evidence': ['observation'], 'limitations': ['Fixture only']}],
        'evidence': [{'id': 'observation', 'path': 'outputs/a.txt', 'description': 'Retained fixture', 'sha256': digest(b'a')}],
        'validation': [{'id': 'check', 'method': 'Compare fixture to expected bytes', 'status': 'passed',
                        'expected': 'a', 'observed': 'a', 'evidence': ['observation']}],
        'reproduction': {'commands': ['Read a.txt'], 'dependencies': [], 'notes': 'No external dependencies'},
        'deliverables': ['outputs/a.txt'], 'limitations': ['No native runtime qualification'],
    }


def remote_files(job='job', generation=7, structured=False):
    data = {'outputs/a.txt': b'a', 'outputs/b.txt': b'b'}
    if structured:
        data['outputs/research.json'] = json.dumps(report()).encode()
        data['outputs/result.json'] = json.dumps({'format': 'runner-result/v1', 'outcome': 'complete',
            'reason': 'scope_complete', 'summary': 'fixture', 'next_action': '', 'blockers': []}).encode()
    manifest = {'format': 'runner-artifacts/v1', 'job_id': job, 'generation': generation,
                'files': [{'name': name, 'size': len(value), 'sha256': digest(value)} for name, value in data.items()]}
    return manifest, data


def test_scaffold_from_nested_directory_and_deterministic_package(tmp_path):
    (tmp_path / '.gitignore').write_text('existing-rule\n')
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid', 'experiment')
    (tmp_path / 'private.txt').write_text('not a payload')
    nested = tmp_path / 'research/experiment/inputs/nested'
    nested.mkdir()
    (nested / 'manifest.json').write_text('{}')
    discovered = ResearchRepo(nested)
    first = discovered.package('experiment')
    assert first == repo.package('experiment')
    manifest, inventory = verify(first['archive'])
    assert 'inputs/nested/manifest.json' in inventory
    assert all('private' not in path for path in inventory)
    assert 'outputs/research.json' in manifest['required_outputs']
    assert (tmp_path / '.gitignore').read_text() == 'existing-rule\n/.research-runner/\n'
    assert (tmp_path / '.agents/skills/research-runner/SKILL.md').is_file()
    with pytest.raises(ValueError, match='overwrite'):
        ResearchRepo.init(tmp_path, 'http://runner.invalid')
    (tmp_path / 'research/experiment/inputs/question.md').write_text('A new question')
    assert repo.package('experiment')['package_sha256'] != first['package_sha256']


def test_library_and_cli_do_not_import_server_modules(tmp_path):
    # Import fcntl only when acquiring a platform lock, never to import/help a client.
    code = '''
import importlib.abc, pathlib, subprocess, sys
sys.modules['fcntl'] = None
class RejectServer(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'fastapi', 'uvicorn', 'openai_codex'} or fullname == 'runner.service':
            raise AssertionError('producer imported server dependency: ' + fullname)
sys.meta_path.insert(0, RejectServer())
from runner import Client, ResearchRepo
from runner.cli import main
sys.argv = ['research-runner', '--help']
main()
'''
    process = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert 'package' in process.stdout and 'monitor' in process.stdout


def test_submission_uncertainty_retains_identity_and_rejects_changed_bytes(tmp_path, monkeypatch):
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid', 'experiment')
    requests = []
    state = {'uncertain': True}
    def request(self, path, method='GET', data=None, key=None, **options):
        if path == '/api/capabilities':
            return {'package_formats': ['research-package/v2'], 'limits': {}}
        assert path == '/api/jobs' and method == 'POST'
        requests.append(key)
        if state['uncertain']:
            state['uncertain'] = False
            raise OSError('lost response')
        return {'id': 'job', 'package_sha256': digest(data.read())}
    monkeypatch.setattr(Client, 'request', request)
    assert repo.submit('experiment')['preview'] and not requests
    with pytest.raises(OSError):
        repo.submit('experiment', apply=True)
    recovered = ResearchRepo(tmp_path)
    assert recovered.submit('experiment', apply=True)['id'] == 'job'
    assert len(set(requests)) == 1
    assert recovered.submit('experiment', apply=True)['id'] == 'job'
    assert len(requests) == 2
    (tmp_path / 'research/experiment/inputs/question.md').write_text('changed')
    with pytest.raises(ValueError, match='different package bytes'):
        recovered.submit('experiment', apply=True)


def test_control_uncertainty_reuses_key_and_body(tmp_path, monkeypatch):
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid')
    monkeypatch.setattr(Client, 'status', lambda self, job: {'state': 'queued'})
    repo.attach('existing', 'job')
    calls = []
    def control(self, job, action, key, apply=False, body=None):
        calls.append((job, action, key, apply, body))
        raise OSError('lost control response')
    monkeypatch.setattr(Client, 'control', control)
    for _ in range(2):
        with pytest.raises(OSError):
            ResearchRepo(tmp_path).start('existing', apply=True)
    assert calls[0] == calls[1]
    with pytest.raises(ValueError, match='body changed'):
        repo.control('existing', 'start', apply=True, body={'input': 'new instructions'})
    with pytest.raises(OSError):
        repo.control('existing', 'start', apply=True, key='deliberate-new-key', body={'input': 'new instructions'})
    assert calls[-1][2] == 'deliberate-new-key'


def test_interrupted_collection_pins_generation_and_reuses_verified_files(tmp_path):
    client = Client('http://runner.invalid')
    manifest, data = remote_files()
    requests = []
    interrupted = [True]
    def request(path, **options):
        requests.append(path)
        if '/artifact-manifest' in path:
            return manifest
        name = path.split('/artifacts/')[1].split('?')[0]
        if name == 'outputs/b.txt' and interrupted[0]:
            interrupted[0] = False
            raise OSError('connection dropped')
        assert path.endswith('generation=7')
        return data[name]
    client.request = request
    output = tmp_path / 'collection'
    assert client.collect('job', output)['manifest']['generation'] == 7
    assert not output.exists() and not output.with_suffix('.partial').exists()
    with pytest.raises(OSError):
        client.collect('job', output, apply=True)
    assert not output.exists()
    staging = tmp_path / 'collection.partial'
    assert not json.loads((staging / 'receipt.json').read_text())['verified']
    manifest = dict(manifest, generation=8)  # Server advances while download is interrupted.
    progress = []
    result = client.collect('job', output, apply=True, progress=progress.append)
    assert result['verified'] and result['generation'] == 7 and not staging.exists()
    assert sum('/a.txt?' in path for path in requests) == 1
    assert progress[0]['reused'] and progress[-1]['files'] == 2
    before = list(requests)
    assert client.collect('job', output, apply=True) == result
    assert before == requests
    with pytest.raises(ValueError, match='different generation'):
        client.collect('job', output, generation=8, apply=True)
    (output / 'outputs/a.txt').write_text('corruption')
    with pytest.raises(ValueError, match='modified'):
        client.collect('job', output, apply=True)


def test_repo_pull_recovers_same_generation_across_instances(tmp_path, monkeypatch):
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid')
    manifest, data = remote_files(structured=True)
    interrupted = [True]
    gets = []
    def request(self, path, **options):
        gets.append(path)
        if path.endswith('/status'):
            return {'state': 'finished'}
        if '/artifact-manifest' in path:
            if 'generation=7' not in path:
                return manifest
            return dict(manifest, generation=7)
        name = path.split('/artifacts/')[1].split('?')[0]
        if name == 'outputs/b.txt' and interrupted[0]:
            interrupted[0] = False
            raise OSError('interrupted')
        return data[name]
    monkeypatch.setattr(Client, 'request', request)
    repo.attach('dashboard-job', 'job')
    with pytest.raises(OSError):
        repo.pull('dashboard-job', apply=True)
    manifest = dict(manifest, generation=8)
    fresh = ResearchRepo(tmp_path)
    collected = fresh.pull('dashboard-job', apply=True)
    assert collected['generation'] == 7
    assert fresh.results('dashboard-job')['research']['validation'][0]['status'] == 'passed'
    assert fresh.results('dashboard-job')['independent_validation'] == 'not_run'
    assert sum('/a.txt?' in path for path in gets) == 1
    assert fresh.pull('dashboard-job')['manifest']['generation'] == 8


def test_monitor_drains_pages_then_stops_for_approval_without_controls():
    client = Client('http://runner.invalid')
    calls = []
    def request(path, method='GET', **options):
        calls.append((method, path))
        if path.endswith('/status'):
            return {'state': 'running', 'pending_approvals': [{'id': 'approval'}]}
        if 'after=0' in path:
            return {'events': [{'seq': 1}], 'next_cursor': 1, 'has_more': True}
        return {'events': [{'seq': 2}], 'next_cursor': 2, 'has_more': False}
    client.request = request
    messages = list(client.monitor('job'))
    assert [item['event']['seq'] for item in messages if item['kind'] == 'event'] == [1, 2]
    assert messages[-1]['cursor'] == 2
    assert {method for method, _ in calls} == {'GET'}


@pytest.mark.parametrize('change', ['wrong-job', 'wrong-generation', 'traversal', 'duplicate', 'oversize', 'bad-hash', 'collision'])
def test_malformed_manifest_fails_before_creating_destination(tmp_path, change):
    client = Client('http://runner.invalid')
    manifest, _ = remote_files()
    if change == 'wrong-job': manifest['job_id'] = 'other'
    if change == 'wrong-generation': manifest['generation'] = 9
    if change == 'traversal': manifest['files'][0]['name'] = 'outputs/../escape'
    if change == 'duplicate': manifest['files'][1]['name'] = 'outputs/A.txt'
    if change == 'oversize': manifest['files'][0]['size'] = 33 * 1024 * 1024
    if change == 'bad-hash': manifest['files'][0]['sha256'] = 'not-a-hash'
    if change == 'collision': manifest['files'][1]['name'] = 'outputs/a.txt/nested'
    client.request = lambda *args, **kwargs: manifest
    with pytest.raises(ValueError):
        client.collect('job', tmp_path / 'output', generation=7, apply=True)
    assert not (tmp_path / 'output').exists() and not (tmp_path / 'output.partial').exists()


def test_structured_research_references_and_retains_failed_checks():
    value = report()
    value['validation'][0]['status'] = 'failed'
    assert ResearchReport.model_validate(value).validation[0].status == 'failed'
    artifact = {'outputs/a.txt': {'sha256': digest(b'a')}}
    assert validate_research(json.dumps(value).encode(), artifact)['scope']
    with pytest.raises(ValueError, match='unpublished'):
        validate_research(json.dumps(value).encode(), {})
    value['findings'][0]['evidence'] = ['unknown']
    with pytest.raises(ValueError, match='unknown evidence'):
        ResearchReport.model_validate(value)
    value = report()
    value['validation'][0]['evidence'] = []
    with pytest.raises(ValueError, match='requires evidence'):
        ResearchReport.model_validate(value)
    assert schemas()['research']['properties']['format']['const'] == 'runner-research/v1'


def test_cli_scaffolds_from_installed_entrypoint(tmp_path):
    # sys.executable -m resolves the installed distribution in the wheel CI job.
    process = subprocess.run([sys.executable, '-m', 'runner.cli', 'init', str(tmp_path), '--url',
                              'http://runner.invalid', '--name', 'cli-job'], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    packaged = subprocess.run([sys.executable, '-m', 'runner.cli', '--repo', str(tmp_path), 'package', 'cli-job'],
                               capture_output=True, text=True)
    assert packaged.returncode == 0, packaged.stderr
    assert Path(json.loads(packaged.stdout)['archive']).is_file()
    skill = tmp_path / '.agents/skills/research-runner/SKILL.md'
    assert skill.read_text().startswith('---\nname: research-runner\n')


def test_repo_monitor_remembers_cursor_between_instances(tmp_path, monkeypatch):
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid')
    requests = []
    def request(self, path, **options):
        requests.append(path)
        if path.endswith('/status'):
            return {'state': 'finished', 'pending_approvals': []}
        if 'after=0' in path:
            return {'events': [{'seq': 4}], 'next_cursor': 4, 'has_more': False}
        return {'events': [], 'next_cursor': 4, 'has_more': False}
    monkeypatch.setattr(Client, 'request', request)
    repo.attach('finished', 'job')
    first = list(repo.monitor('finished'))
    second = list(ResearchRepo(tmp_path).monitor('finished'))
    assert any(message['kind'] == 'event' for message in first)
    assert not any(message['kind'] == 'event' for message in second)
    assert any('after=4' in path for path in requests)


def test_client_timeout_bounds_paging_even_with_backlog():
    client = Client('http://runner.invalid')
    calls = []
    def request(path, **options):
        calls.append(path)
        if path.endswith('/status'):
            return {'state': 'running'}
        return {'events': [{'seq': 1}], 'next_cursor': 1, 'has_more': True}
    client.request = request
    messages = list(client.monitor('job', timeout=0))
    assert len(calls) == 2
    assert messages[-1]['cursor'] == 1


def test_completed_collection_rejects_corruption_and_unpublished_research(tmp_path):
    client = Client('http://runner.invalid')
    manifest, data = remote_files(structured=True)
    def request(path, **options):
        if '/artifact-manifest' in path:
            return manifest
        return data[path.split('/artifacts/')[1].split('?')[0]]
    client.request = request
    output = tmp_path / 'collected'
    client.collect('job', output, apply=True)
    assert inspect_collection(output)['research']['findings'][0]['confidence'] == 'supported'
    (output / 'outputs/a.txt').write_text('tampered')
    with pytest.raises(ValueError, match='integrity mismatch'):
        inspect_collection(output)


def test_payload_and_download_do_not_follow_links(tmp_path):
    # Windows CI may lack permission to create symlinks; other producer cases run there.
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid')
    outside = tmp_path / 'private.txt'
    outside.write_text('private')
    link = tmp_path / 'research/example/inputs/link'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation unavailable')
    with pytest.raises(ValueError, match='links'):
        repo.package('example')
    output = tmp_path / 'output'
    output.symlink_to(tmp_path / 'research', target_is_directory=True)
    client = Client('http://runner.invalid')
    client.request = lambda *args, **kwargs: pytest.fail('must reject link before network access')
    with pytest.raises(ValueError, match='links'):
        client.collect('job', output, apply=True)


def test_documentation_and_schemas_available_without_checkout(tmp_path):
    from runner.documentation import documents, export_documents
    content = documents()
    assert {'README.md', 'AGENTS.md', 'docs/agent-quickstart.md', 'docs/client-workflow.md',
            'docs/job-runner-spec.md', 'schemas/research.json', 'skills/research-runner/SKILL.md'} <= content.keys()
    assert json.loads(content['schemas/research.json']) == schemas()['research']
    output = tmp_path / 'documentation'
    exported = export_documents(output)
    assert (output / 'docs/agent-quickstart.md').is_file()
    assert export_documents(output) == exported
    modified = output / 'README.md'
    modified.write_text('User annotations')
    with pytest.raises(ValueError, match='overwrite modified'):
        export_documents(output)
    assert modified.read_text() == 'User annotations'
    process = subprocess.run([sys.executable, '-m', 'runner.cli', 'docs'], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert process.stdout == content['docs/agent-quickstart.md']
    # Init exports the maintained docs separately from the uploaded definition.
    repo = ResearchRepo.init(tmp_path / 'consumer', 'http://runner.invalid')
    assert (repo.state / 'docs/schemas/research.json').is_file()
    assert (repo.root / '.agents/skills/research-runner/SKILL.md').is_file()


def test_asset_authoring_materials_and_optional_manifest_ship_with_client(tmp_path):
    from runner.documentation import documents, export_documents
    content = documents()
    assert {'docs/asset-loaders.md', 'schemas/asset-loader.json',
            'examples/asset-loader/loader.py', 'examples/asset-loader/loaders.example.toml'} <= content.keys()
    assert json.loads(content['schemas/asset-loader.json']) == schemas()['asset-loader']
    assert 'dependencies' not in schemas()['manifest-v2']['required']
    assert 'from runner' not in content['examples/asset-loader/loader.py']
    export_documents(tmp_path / 'docs')
    process = subprocess.run([sys.executable, str(tmp_path / 'docs/examples/asset-loader/loader.py'),
        '--catalog', str(tmp_path / 'not-needed-for-describe.json')],
        input=json.dumps({'protocol': 'runner-asset-loader/v1', 'operation': 'describe', 'asset_type': 'local-files'}) + '\n',
        capture_output=True, text=True, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['operations'] == ['resolve', 'materialize']


def test_client_rejects_asset_submission_to_old_server(tmp_path):
    repo = ResearchRepo.init(tmp_path, 'http://runner.invalid')
    path = tmp_path / 'research/example/manifest.json'
    value = json.loads(path.read_text())
    value['dependencies'] = [{'asset_type': 'private-data', 'asset_id': 'revision-1', 'destination': 'inputs/external'}]
    path.write_text(json.dumps(value))
    package = repo.package('example')
    client = Client('http://runner.invalid')
    client.request = lambda *args: {'package_formats': ['research-package/v2'], 'features': {}}
    with pytest.raises(ValueError, match='asset dependencies'):
        client.submit(package['archive'], 'key', tmp_path / 'receipt.json', apply=True)
