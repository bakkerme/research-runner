"""Repo-local producer workflow. No service checkout or server imports required."""
from importlib.resources import files
import json
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
import uuid

from .client import Client, save_json
from .locking import file_lock
from .package import assemble, hash_file, verify
from .protocol import Result, load_contract, schemas, validate_research

CONFIG = 'research-runner.toml'
STATE = '.research-runner'


def name_checked(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
        raise ValueError('names must start with a lowercase letter and contain up to 64 letters, digits or hyphens')
    if name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('name is reserved on Windows')
    return name


def bundled_skill():
    return files('runner').joinpath('skills/research-runner/SKILL.md').read_text(encoding='utf-8')


def inspect_collection(directory):
    """Verify a local collection and read its structured reports; execute nothing."""
    directory = Path(directory)
    Client._safe_tree(directory)
    receipt = json.loads((directory / 'receipt.json').read_text())
    if receipt.get('format') != 'runner-collection/v1' or not receipt.get('verified'):
        raise ValueError('collection is incomplete')
    manifest = receipt['manifest']
    Client._validate_collection(manifest, receipt['job_id'], receipt['generation'])
    artifacts = {item['name']: item for item in manifest['files']}
    for path, meta in artifacts.items():
        target = directory / path
        if not target.is_file() or target.stat().st_size != meta['size'] or hash_file(target) != meta['sha256']:
            raise ValueError('collection integrity mismatch: ' + path)
    result = load_contract(Result, (directory / 'outputs/result.json').read_bytes()) if 'outputs/result.json' in artifacts else None
    report = validate_research((directory / 'outputs/research.json').read_bytes(), artifacts) if 'outputs/research.json' in artifacts else None
    return {'delivery_verified': True, 'job_id': receipt['job_id'], 'generation': receipt['generation'],
            'result': result, 'research': report, 'independent_validation': 'not_run'}


class ResearchRepo:
    """A consuming repo's definitions, durable job handles and result collections.

    Client credentials come only from the constructor or RESEARCH_RUNNER_TOKEN.
    Mutating methods preview by default. Monitoring never starts or resumes work.
    """
    def __init__(self, root='.', token=None):
        root = Path(root).resolve()
        if root.is_file():
            root = root.parent
        self.root = next((p for p in (root, *root.parents) if (p / CONFIG).is_file()), None)
        if self.root is None:
            raise ValueError('no research-runner.toml found; run research-runner init --url URL')
        config = tomllib.loads((self.root / CONFIG).read_text())
        if config.get('version') != 1 or set(config) != {'version', 'url'}:
            raise ValueError('unsupported repo configuration; expected version = 1 and url')
        self.client = Client(config['url'], token)
        self.state = self.root / STATE
        self._safe_path(self.state)

    def _safe_path(self, path):
        path = Path(path).absolute()
        if not path.is_relative_to(self.root):
            raise ValueError('repo path escaped root')
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError('repo state and definitions must not contain links')
        return path

    @classmethod
    def init(cls, root='.', url='http://127.0.0.1:8765', name='example', skill=True):
        name_checked(name)
        client = Client(url)
        root = Path(root).resolve()
        paths = [root / CONFIG, root / 'research' / name]
        if skill:
            paths.append(root / '.agents/skills/research-runner/SKILL.md')
        for path in paths:
            if path.exists() or path.is_symlink():
                raise ValueError('refusing to overwrite ' + str(path))
            for parent in path.parents:
                if parent == root:
                    break
                if parent.is_symlink():
                    raise ValueError('scaffold paths must not contain links')
        ignore = root / '.gitignore'
        if ignore.is_symlink():
            raise ValueError('.gitignore must not be a symlink')
        root.mkdir(parents=True, exist_ok=True)
        (root / CONFIG).write_text('version = 1\nurl = ' + json.dumps(client.url) + '\n', encoding='utf-8')
        old = ignore.read_text() if ignore.exists() else ''
        if f'/{STATE}/' not in old.splitlines():
            ignore.write_text(old + ('\n' if old and not old.endswith('\n') else '') + f'/{STATE}/\n', encoding='utf-8')
        repo = cls(root)
        repo.new(name)
        from .documentation import export_documents
        export_documents(repo.state / 'docs')
        if skill:
            repo.install_skill()
        return repo

    def install_skill(self):
        target = self._safe_path(self.root / '.agents/skills/research-runner/SKILL.md')
        content = bundled_skill()
        if target.exists() and target.read_text() != content:
            raise ValueError('existing skill differs; preserve it or choose a fresh repo')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        return str(target)

    def new(self, name):
        name_checked(name)
        source = self._safe_path(self.root / 'research' / name)
        source.mkdir(parents=True, exist_ok=False)
        (source / 'inputs').mkdir()
        manifest = {
            'format': 'research-package/v2', 'name': name, 'adapter': 'codex',
            'runtime': {'sdk': 'openai-codex==0.159.0'}, 'brief': 'brief.md', 'agents': 'AGENTS.md',
            'skills': [], 'inputs': ['inputs/question.md', 'inputs/contracts.json'],
            'resources': {'cpu': 1, 'memory_mb': 4096, 'time_seconds': 3600},
            'required_outputs': ['outputs/result.json', 'outputs/handoff.md', 'outputs/research.json'],
            'policy': {'finalization_seconds': 300, 'termination_seconds': 60, 'checkpoint_seconds': 30},
        }
        save_json(source / 'manifest.json', manifest)
        save_json(source / 'inputs/contracts.json', schemas())
        (source / 'inputs/question.md').write_text(
            '# Research question\n\nDefine the question, bounded scope, acceptance criteria and required inputs here.\n', encoding='utf-8')
        (source / 'brief.md').write_text(
            'Read inputs/question.md. Research the declared scope and validate the findings against its acceptance criteria.\n'
            'Include every required dependency in this payload before submission; identify unavailable inputs explicitly.\n'
            'Retain failed and inconclusive checks. Report blockers and useful partial results honestly.\n'
            'Deliver outputs/result.json, outputs/handoff.md and outputs/research.json as described in AGENTS.md.\n'
            'If the manifest lists required_checks, complete is accepted only when every listed check passed.\n', encoding='utf-8')
        (source / 'AGENTS.md').write_text(
            '# Research execution\n\n'
            'Work within the brief, supplied inputs and Runner deadline. Preserve original inputs.\n'
            'Write useful partial artifacts under outputs/ and maintain outputs/checkpoint.json.\n'
            'Use the JSON schemas in inputs/contracts.json: result, checkpoint and research.\n'
            'The closeout result uses format runner-result/v1 with outcome complete/partial/blocked,\n'
            'reason scope_complete/budget_exhausted/no_actionable_work/user_requested, summary, next_action and blockers.\n'
            'Complete requires scope_complete; blocked requires no_actionable_work and nonempty blockers.\n'
            'When the manifest lists required_checks, outcome complete is accepted only if every listed check\n'
            'passed. If a required check cannot be run, report partial or blocked instead of complete.\n'
            'Respect Runner finalization requests; finish early when no permitted work remains.\n\n'
            'Write outputs/research.json using runner-research/v1. Include scope, findings, evidence, validation,\n'
            'reproduction, deliverables and limitations. Findings refer to evidence IDs and state confidence\n'
            '(supported/tentative/unresolved). Validation checks record method, expected, observed, status\n'
            '(passed/failed/inconclusive/not_run) and evidence IDs. Passed checks require retained evidence.\n'
            'Include at least one validation check, including an explicit not_run explanation if blocked.\n'
            'Evidence and deliverable paths start with outputs/ and must be published files.\n'
            'Record dependency hashes/provenance and reproduction commands; disclose external tool requirements.\n'
            'Validation performed during research is welcome. Distinguish observations from assumptions,\n'
            'synthetic checks from native behavior, and delivery hashes from scientific validation.\n'
            'Do not claim unperformed checks passed. Do not send messages or change external systems without task authorization.\n', encoding='utf-8')
        return str(source)

    def package(self, name):
        """Package this definition only, automatically declaring its input files."""
        name_checked(name)
        source = self._safe_path(self.root / 'research' / name)
        manifest = json.loads((source / 'manifest.json').read_text())
        payload = []
        for path in source.rglob('*'):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('payload links and special files forbidden: ' + str(path))
            if path.is_file() and path != source / 'manifest.json':
                payload.append(path.relative_to(source).as_posix())
        declared = [manifest['brief'], manifest['agents'], *manifest['skills']]
        if not set(declared) <= set(payload):
            raise ValueError('brief, agents or skill file missing')
        manifest['inputs'] = sorted(set(payload) - set(declared))
        cache = self._safe_path(self.state / 'packages')
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache, prefix='build-') as temporary:
            temporary = Path(temporary)
            stage = temporary / 'payload'
            stage.mkdir()
            for name_ in payload:
                target = stage / name_
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name_, target)
            save_json(stage / 'manifest.json', manifest)
            archive = temporary / 'package.rrpkg'
            sha = assemble(stage, archive)
            target = cache / f'{name}-{sha}.rrpkg'
            if target.exists():
                if target.is_symlink() or hash_file(target) != sha:
                    raise ValueError('cached package integrity mismatch')
            else:
                archive.replace(target)
        return {'archive': str(target), 'package_sha256': sha, 'definition': name}

    def _run_path(self, name):
        return self._safe_path(self.state / 'runs' / name_checked(name))

    def _run(self, name):
        path = self._run_path(name)
        if not (path / 'run.json').is_file():
            raise ValueError('unknown local run; submit or attach it first: ' + name)
        run = json.loads((path / 'run.json').read_text())
        if run.get('url') != self.client.url:
            raise ValueError('run belongs to a different server; restore its configured URL')
        if not run.get('job_id') and (path / 'submission.json').is_file():
            run['job_id'] = json.loads((path / 'submission.json').read_text()).get('id')
        return run

    def _job(self, name):
        run = self._run(name)
        if not run.get('job_id'):
            raise ValueError('submission has no confirmed job ID; retry submit with the same run name')
        return run['job_id']

    def submit(self, definition, run=None, apply=False):
        run = name_checked(run or definition)
        package = self.package(definition)
        if not apply:
            return dict(package, preview=True, run=run, url=self.client.url)
        with file_lock(self.state / 'operation.lock'):
            path = self._run_path(run)
            if (path / 'run.json').exists():
                handle = self._run(run)
                if handle.get('package_sha256') != package['package_sha256']:
                    raise ValueError('run already identifies different package bytes; use --run with a fresh name')
            else:
                handle = {'format': 'runner-local-run/v1', 'name': run, 'definition': definition,
                          'url': self.client.url, 'package_sha256': package['package_sha256'],
                          'archive': str(Path(package['archive']).relative_to(self.root)),
                          'submission_key': uuid.uuid4().hex, 'job_id': None, 'controls': {}}
                save_json(path / 'run.json', handle)
            if handle.get('job_id'):
                return json.loads((path / 'submission.json').read_text())
            receipt = self.client.submit(self.root / handle['archive'], handle['submission_key'], path / 'submission.json', apply=True)
            handle['job_id'] = receipt['id']
            save_json(path / 'run.json', handle)
            return receipt

    def attach(self, name, job):
        """Give a dashboard-submitted job a local name; performs only a remote GET."""
        status = self.client.status(job)
        with file_lock(self.state / 'operation.lock'):
            path = self._run_path(name)
            if (path / 'run.json').exists():
                if self._job(name) != job:
                    raise ValueError('local name already identifies another job')
                return status
            save_json(path / 'run.json', {'format': 'runner-local-run/v1', 'name': name,
                      'url': self.client.url, 'job_id': job, 'controls': {}})
        return status

    def control(self, name, action, apply=False, key=None, body=None):
        if action not in ('start', 'stop', 'finalize'):
            raise ValueError('unsupported repo control')
        job = self._job(name)
        if not apply:
            return self.client.control(job, action, key or '(persisted on apply)', body=body)
        with file_lock(self.state / 'operation.lock'):
            run = self._run(name)
            operation = run['controls'].get(action)
            body = body or {}
            if operation and (key is None or operation['key'] == key):
                if operation['body'] != body:
                    raise ValueError('operation body changed; use a fresh explicit --key for a deliberate new operation')
                key = operation['key']
            else:
                key = key or uuid.uuid4().hex
                run['controls'][action] = {'key': key, 'body': body}
                save_json(self._run_path(name) / 'run.json', run)
            return self.client.control(job, action, key, apply=True, body=body)

    def start(self, name, apply=False, key=None):
        return self.control(name, 'start', apply=apply, key=key)

    def status(self, name):
        return self.client.status(self._job(name))

    def events(self, name, after=0, limit=100):
        return self.client.events(self._job(name), after, limit)

    def monitor(self, name, after=None, **options):
        """Resume the local event cursor; persist progress without controlling jobs."""
        job = self._job(name)
        cursor_path = self._run_path(name) / 'monitor.json'
        with file_lock(self._run_path(name) / 'monitor.lock'):
            if after is None:
                after = json.loads(cursor_path.read_text())['cursor'] if cursor_path.exists() else 0
            for message in self.client.monitor(job, after=after, **options):
                yield message
                cursor = message['event']['seq'] if message['kind'] == 'event' else message['cursor']
                save_json(cursor_path, {'cursor': cursor})

    def pull(self, name, generation=None, apply=False, progress=None):
        job = self._job(name)
        pending = self._run_path(name) / 'pull.json'
        def plan():
            if pending.exists():
                saved = json.loads(pending.read_text())
                if generation is not None and generation != saved['generation']:
                    raise ValueError('an interrupted pull is pinned to generation ' + str(saved['generation']) + '; finish it first')
                return saved
            manifest = self.client._collection_manifest(job, generation)
            return {'generation': manifest['generation']}
        if not apply:
            selected = plan()
            destination = self.state / 'collections' / name / f'g{selected["generation"]}'
            return self.client.collect(job, destination, selected['generation'])
        with file_lock(self.state / 'operation.lock'):
            selected = plan()
            save_json(pending, selected)
            destination = self._safe_path(self.state / 'collections' / name / f'g{selected["generation"]}')
            result = self.client.collect(job, destination, selected['generation'], apply=True, progress=progress)
            save_json(self._run_path(name) / 'collection.json', {'directory': str(destination.relative_to(self.root)), 'generation': result['generation']})
            pending.unlink()
            return dict(result, directory=str(destination))

    def results(self, name):
        selected = json.loads((self._run_path(name) / 'collection.json').read_text())
        return inspect_collection(self._safe_path(self.root / selected['directory']))
