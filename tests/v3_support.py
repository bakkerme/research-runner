"""Helpers for v3 tool-image tests. Not a test module."""
import json
import os
from pathlib import Path
import subprocess
import textwrap
import time

from runner.package import assemble, hash_file
from runner.tool_images import derived_tag


ROOT = Path(__file__).resolve().parents[1]
IMAGE_ID = 'sha256:' + ('ab' * 32)


def container_bytes():
    return b'# Generic declared tool image. The runner tags it from the input hash.\nFROM scratch\n'


def write_payload(tmp_path, *, name='demo', checks=None, research=None, fetch=None, extra_inputs=None, mode='complete'):
    source = tmp_path / 'payload'
    (source / 'tools' / 'demo').mkdir(parents=True)
    (source / 'inputs').mkdir()
    (source / 'tools' / 'demo' / 'Containerfile').write_bytes(container_bytes())
    (source / 'brief.md').write_text('Read the inputs and finish the fixture.\n', encoding='utf-8')
    (source / 'AGENTS.md').write_text('Preserve inputs. Write the required outputs.\n', encoding='utf-8')
    fixture = {'mode': mode, 'seconds': 0.05, 'summary': 'v3 fixture', 'text': 'retained'}
    if research is not None:
        fixture['research'] = research
    (source / 'inputs' / 'fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
    inputs = ['inputs/fixture.json', 'tools/demo/Containerfile']
    pinned = {'tools/demo/Containerfile': hash_file(source / 'tools' / 'demo' / 'Containerfile')}
    for rel, text in (extra_inputs or {}).items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        inputs.append(rel)
        pinned[rel] = hash_file(path)
    image = {
        'name': name,
        'containerfile': 'tools/demo/Containerfile',
        'context': 'tools/demo',
        'platform': 'linux/arm64',
        'inputs': pinned,
        'timeout_seconds': 30,
    }
    if fetch:
        image['fetch'] = fetch
    image['tag'] = derived_tag(image)
    manifest = {
        'format': 'research-package/v3',
        'name': 'tool-image-fixture',
        'adapter': 'fixture',
        'runtime': {'sdk': 'openai-codex==0.159.0'},
        'brief': 'brief.md',
        'agents': 'AGENTS.md',
        'skills': [],
        'inputs': inputs,
        'resources': {'cpu': 1, 'memory_mb': 1024, 'time_seconds': 30},
        'required_outputs': ['outputs/result.json', 'outputs/handoff.md'],
        'policy': {'finalization_seconds': 10, 'termination_seconds': 3, 'checkpoint_seconds': 1},
        'tool_images': [image],
    }
    if checks is not None:
        manifest['required_checks'] = checks
        if 'outputs/research.json' not in manifest['required_outputs']:
            manifest['required_outputs'].append('outputs/research.json')
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return source, manifest


def pack(tmp_path, **kwargs):
    source, manifest = write_payload(tmp_path, **kwargs)
    archive = tmp_path / 'job.rrpkg'
    assemble(source, archive)
    return archive, manifest


def install_docker(tmp_path, monkeypatch):
    state = tmp_path / 'docker-state'
    state.mkdir()
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    script = bindir / 'docker'
    script.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import json, os, subprocess, sys, time
        state = os.environ['RR_DOCKER_STATE']
        args = sys.argv[1:]

        def write(name, text):
            path = os.path.join(state, name)
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())

        def load():
            for name in ('built.json', 'present.json'):
                path = os.path.join(state, name)
                if os.path.exists(path):
                    with open(path, encoding='utf-8') as handle:
                        return json.load(handle)
            return None

        if args[:2] == ['image', 'inspect']:
            image = load()
            if image is None:
                sys.stderr.write('No such image\\n')
                sys.exit(1)
            json.dump([image], sys.stdout)
            sys.exit(0)
        if args[:1] != ['build']:
            sys.stderr.write('unexpected docker command\\n')
            sys.exit(1)
        with open(os.path.join(state, 'builds'), 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(args) + '\\n')
        context = args[-1]
        if not os.path.isfile(os.path.join(context, 'Containerfile')):
            sys.stderr.write('missing Containerfile\\n')
            sys.exit(1)
        if os.environ.get('RR_DOCKER_HOLD'):
            write('group', str(os.getpgrp()))
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])
            write('child', str(child.pid))
            write('ready', '1')
            time.sleep(300)
        if os.environ.get('RR_DOCKER_SLEEP'):
            time.sleep(float(os.environ['RR_DOCKER_SLEEP']))
        if os.environ.get('RR_DOCKER_EXIT'):
            sys.stderr.write(os.environ.get('RR_DOCKER_STDERR', 'build failed\\n'))
            sys.exit(int(os.environ['RR_DOCKER_EXIT']))
        print('Step 1/1 : FROM scratch', flush=True)
        label = args[args.index('--label') + 1].split('=', 1)[1]
        platform = args[args.index('--platform') + 1]
        image = {
            'Id': %r,
            'Architecture': os.environ.get('RR_DOCKER_ARCH') or platform.split('/')[-1],
            'Created': '2026-01-01T00:00:00Z',
            'Config': {'Labels': {'research.input_hash': label}},
        }
        write('built.json', json.dumps(image))
        sys.exit(0)
    ''' % IMAGE_ID), encoding='utf-8')
    script.chmod(0o755)
    monkeypatch.setenv('RR_DOCKER_STATE', str(state))
    monkeypatch.setenv('PATH', str(bindir) + os.pathsep + os.environ.get('PATH', ''))
    return state


def wait_job(service, jid, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = service.store.get(jid)
        if job['state'] not in ('running', 'finalizing', 'queued') and jid not in service.procs and jid not in service.preparing:
            return job
        time.sleep(0.02)
    raise AssertionError(service.store.get(jid))


def group_gone(pgid):
    probe = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True, text=True, timeout=10)
    for line in probe.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == str(pgid) and not parts[1].startswith('Z'):
            return False
    return True


def passed_report(check='parser-suite'):
    return {
        'format': 'runner-research/v1',
        'scope': 'Fixture validation',
        'findings': [],
        'evidence': [{'id': 'bytes', 'path': 'outputs/partial.txt', 'description': 'Retained fixture output'}],
        'validation': [{
            'id': check, 'method': 'Fixture write', 'status': 'passed',
            'expected': 'retained', 'observed': 'retained', 'evidence': ['bytes'],
        }],
        'reproduction': {'commands': [], 'dependencies': [], 'notes': 'Fixture only'},
        'deliverables': ['outputs/partial.txt'],
        'limitations': ['No scientific claim'],
    }
