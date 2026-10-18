#!/usr/bin/env python3
"""Opt-in host/live-Codex qualification (Docker agents are optional). Owns only its fresh state and jobs.

Run on the execution host using its installed Codex and normal login. No credentials
are copied. An optional Docker-agent mode requires an image and auth projection.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

from runner.client import Client, save_json
from runner.package import assemble, digest


class Qualification:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.output).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.state = self.root / 'state'
        self.repo = Path(__file__).resolve().parents[1]
        self.image_id = self.docker('image', 'inspect', args.image, '--format', '{{.Id}}').strip() if args.engine == 'docker' else None
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.control_token, self.read_token = secrets.token_hex(24), secrets.token_hex(24)
        self.client = Client(f'http://127.0.0.1:{self.port}', self.control_token)
        self.reader = Client(self.client.url, self.read_token)
        self.process = None
        self.log = None
        self.checks = []
        self.jobs = []

    def docker(self, *args):
        return subprocess.check_output(['docker', *args], text=True, timeout=30)

    def record(self, name, **details):
        item = {'check': name, 'passed': True, **details}
        self.checks.append(item)
        save_json(self.root / 'qualification.json', {'format': 'runner-qualification/v1',
                  'engine': self.args.engine, 'image': self.args.image if self.image_id else None, 'image_id': self.image_id, 'checks': self.checks})
        print(json.dumps(item), flush=True)

    def wait(self, predicate, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError('isolated controller exited; inspect controller logs')
            result = predicate()
            if result:
                return result
            time.sleep(.25)
        raise TimeoutError('qualification condition did not arrive')

    def start_server(self):
        env = os.environ.copy()
        env.update(RESEARCH_RUNNER_READ_TOKEN=self.read_token, RESEARCH_RUNNER_CONTROL_TOKEN=self.control_token)
        env.pop('RESEARCH_RUNNER_CODEX_AUTH_FILE', None)
        if self.args.engine == 'docker':
            env['RESEARCH_RUNNER_CODEX_AUTH_FILE'] = str(Path(self.args.auth_file).expanduser().resolve())
        if self.args.codex_bin:
            env['RESEARCH_RUNNER_CODEX_BIN'] = self.args.codex_bin
        if self.log:
            self.log.close()
        self.log = (self.root / f'controller-{time.time_ns()}.log').open('wb')
        self.process = subprocess.Popen([sys.executable, '-m', 'runner.cli', '--state', str(self.state), '--engine', self.args.engine,
                                         'serve', '--host', '127.0.0.1', '--port', str(self.port)],
                                        stdout=self.log, stderr=self.log, env=env, start_new_session=True)
        def ready():
            try:
                return self.reader.request('/api/capabilities')
            except (OSError, ValueError):
                return None
        self.wait(ready, 45)

    def submit(self, name, *, mode='complete', seconds=.2, budget=20, brief=None, extra=None):
        source = self.root / (name + '-source')
        shutil.copytree(self.repo / 'sample-mvp', source)
        manifest = json.loads((source / 'manifest.json').read_text())
        manifest.update(name=name, adapter='codex' if brief else 'fixture')
        if self.image_id:
            manifest['runtime'].update(image=self.args.image, image_id=self.image_id)
        else:
            manifest['runtime'] = {'sdk': 'openai-codex==0.159.0'}
        manifest['resources'].update(time_seconds=budget, memory_mb=2048)
        manifest['policy'] = {'finalization_seconds': budget / 3,
                              'termination_seconds': min(10, budget / 6), 'checkpoint_seconds': 1}
        if brief:
            (source / 'brief.md').write_text(brief)
            (source / 'inputs/fixture.json').unlink()
            manifest['inputs'] = []
        else:
            save_json(source / 'inputs/fixture.json', {'mode': mode, 'seconds': seconds,
                      'summary': name, 'text': name + ' retained output'})
        if extra:
            manifest['required_outputs'].extend(extra)
        save_json(source / 'manifest.json', manifest)
        archive = self.root / (name + '.rrpkg')
        assemble(source, archive)
        receipt = self.client.submit(archive, 'submit-' + name, self.root / (name + '-receipt.json'), True)
        self.jobs.append(receipt['id'])
        return receipt['id']

    def start(self, jid, key, text=None):
        return self.client.control(jid, 'start', key, True, {'input': text} if text else {})

    def status(self, jid):
        return self.reader.request(f'/api/jobs/{jid}/status')

    def terminal(self, jid, seconds=120):
        result = self.wait(lambda: (s if (s := self.status(jid))['state'] not in ('queued', 'running', 'finalizing') else None), seconds)
        save_json(self.root / (jid + '-terminal.json'), result)
        if result['state'] == 'failed':
            raise RuntimeError('job failed: ' + str(result['error']))
        return result

    def checkpoint(self, jid, expected, seconds=120):
        def ready():
            status = self.status(jid)
            if status['state'] in ('failed', 'finished', 'interrupted'):
                raise RuntimeError('job ended before checkpoint: ' + json.dumps(status))
            publication = status['publication']
            return status if publication and publication['checkpoint'] and expected in publication['checkpoint']['summary'] else None
        return self.wait(ready, seconds)

    def run(self):
        self.start_server()
        jid = self.submit('fixture-timeout', seconds=60, budget=9)
        self.start(jid, 'fixture-timeout-start')
        status = self.terminal(jid, 30)
        assert (status['state'], status['outcome'], status['result']['reason']) == ('finished', 'partial', 'budget_exhausted'), status
        self.record(self.args.engine + '-soft-timeout', job_id=jid)
        jid = self.submit('fixture-forced', mode='unresponsive', seconds=60, budget=9)
        self.start(jid, 'fixture-forced-start')
        status = self.terminal(jid, 30)
        assert status['state'] == 'interrupted' and status['publication'] and status['outcome'] is None, status
        assert status['error'] == 'deadline forced', status
        self.reader.collect(jid, self.root / 'forced-collected', apply=True)
        self.record(self.args.engine + '-forced-deadline-partials', job_id=jid)
        jid = self.submit('fixture-blocked', mode='blocked')
        self.start(jid, 'fixture-blocked-start')
        status = self.terminal(jid)
        assert status['state'] == 'finished' and status['outcome'] == 'blocked', status
        self.record(self.args.engine + '-early-blocked', job_id=jid)

        brief = '''This is a session recovery qualification, not research. Do not search externally.
The first operator message supplies an opaque memory marker. Keep it only in the
conversation. Never write that marker to a workspace file in phase one. Phase one:
write outputs/checkpoint.json with summary exactly "Recovery checkpoint ready", an
appropriate next_action and empty blockers; use the runner-checkpoint/v1 format.
Then run a foreground bounded sleep for at most 120 seconds. Do not finish or
write result.json in phase one. The operator will interrupt this worker.
When a later operator explicitly says PHASE TWO, do not restart phase one. Recall
the original memory marker from this conversation, write only that marker to
outputs/recalled-marker.txt, write a handoff and a complete/scope_complete result
using the runner-result/v1 schema. This completes the qualification objective.
If finalization is requested instead, honor its reason and preserve a partial
handoff. Never launch detached processes or touch files outside this workspace.'''
        marker = 'recovery-' + secrets.token_hex(16)
        jid = self.submit('live-recovery', budget=600, brief=brief,
                          extra=['outputs/recalled-marker.txt'])
        self.start(jid, 'live-recovery-first', 'PHASE ONE. The memory marker is ' + marker)
        before = self.checkpoint(jid, 'Recovery checkpoint ready', 180)
        assert before['thread_id'], before
        generation = before['publication']['generation']
        save_json(self.root / 'before-restart.json', before)
        owned = []
        if self.args.engine == 'docker':
            owned = self.docker('ps', '-q', '--filter', 'label=research-runner.root=' + str(self.state)).split()
            assert owned
            inspected = json.loads(self.docker('inspect', owned[0]))[0]
            mounts = {m['Destination']: m['RW'] for m in inspected['Mounts']}
            assert mounts['/session/auth.json'] is False and mounts['/job/brief.md'] is False
            assert inspected['HostConfig']['CapDrop'] == ['ALL']
            assert inspected['HostConfig']['Memory'] == 2048 * 1024 * 1024
            self.record('docker-isolation', user=inspected['Config']['User'], mounts=mounts,
                        memory_bytes=inspected['HostConfig']['Memory'],
                        cpu_nanocpus=inspected['HostConfig']['NanoCpus'])
        else:
            assert before['execution_engine'] == 'host' and before['session_store'] == 'host_codex_home'
            self.record('native-codex-session-store', job_id=jid)
        self.process.kill(); self.process.wait(timeout=15)
        self.start_server()
        interrupted = self.status(jid)
        assert interrupted['state'] == 'interrupted' and interrupted['thread_id'] == before['thread_id'], interrupted
        assert interrupted['deadline'] == before['deadline']
        for container in owned:
            found = subprocess.run(['docker', 'inspect', container], capture_output=True, timeout=10)
            assert found.returncode != 0, 'old container survived restart'
        self.record('live-controller-restart', job_id=jid, thread_id=before['thread_id'], deadline=before['deadline'])
        self.start(jid, 'live-recovery-second', 'PHASE TWO. Continue the qualification from the prior conversation. Do not repeat phase one. Recall the original marker without asking for it again and finish the declared outputs.')
        after = self.terminal(jid, 180)
        assert after['state'] == 'finished' and after['outcome'] == 'complete', after
        assert after['attempt'] == 2 and after['thread_id'] == before['thread_id'] and after['deadline'] == before['deadline'], after
        self.reader.collect(jid, self.root / 'recovery-collected', apply=True)
        actual = (self.root / 'recovery-collected/outputs/recalled-marker.txt').read_text().strip()
        assert actual == marker, 'restored conversation did not recall original marker'
        prior = self.reader.request(f'/api/jobs/{jid}/artifact-manifest?generation={generation}')
        for entry in prior['files']:
            data = self.reader.request(f'/api/jobs/{jid}/artifacts/{entry["name"]}?generation={generation}', raw=True)
            assert digest(data) == entry['sha256']
        self.record('live-session-recall-and-collection', job_id=jid, attempt=after['attempt'], thread_id=after['thread_id'], marker_sha256=digest(marker.encode()), prior_generation=generation)

        brief = '''This is a live graceful-finalization qualification. Do not search externally.
Write outputs/checkpoint.json with format runner-checkpoint/v1, summary exactly
"Finalization checkpoint ready", next_action "Await finalize request" and blockers [].
Then run short foreground sleeps, at most 5 seconds each, checking the runner's
/requests/finalize.json between them. Remain active until the operator requests
finalization. When a RUNNER FINALIZE message or that file arrives, stop waiting,
write outputs/handoff.md describing retained work, and write a runner-result/v1
result with outcome partial, the supplied reason (normally user_requested), a
nonempty summary, next_action and blockers []. Then finish. No detached work.'''
        jid = self.submit('live-finalize', budget=240, brief=brief)
        self.start(jid, 'live-finalize-start')
        self.checkpoint(jid, 'Finalization checkpoint ready', 150)
        self.client.control(jid, 'finalize', 'live-finalize-request', True)
        status = self.terminal(jid, 90)
        assert status['state'] == 'finished' and status['outcome'] == 'partial' and status['result']['reason'] == 'user_requested', status
        cursor = 0
        acknowledged = False
        while True:
            page = self.reader.request(f'/api/jobs/{jid}/event-log?after={cursor}&limit=100')
            acknowledged |= any(e['kind'] == 'finalize_acknowledged' for e in page['events'])
            cursor = page['next_cursor']
            if not page['has_more']:
                break
        assert acknowledged, 'model closeout did not acknowledge finalize delivery'
        self.reader.collect(jid, self.root / 'finalize-collected', apply=True)
        self.record('live-graceful-finalize', job_id=jid, thread_id=status['thread_id'])
        if self.args.engine == 'docker':
            assert not self.docker('ps', '-q', '--filter', 'label=research-runner.root=' + str(self.state)).strip()
        assert not list((self.state / 'jobs').glob('*/runtime.json'))
        self.record('owned-execution-cleaned', engine=self.args.engine)

    def close(self):
        if self.process and self.process.poll() is None:
            for jid in self.jobs:
                try:
                    if self.status(jid)['state'] in ('running', 'finalizing'):
                        self.client.control(jid, 'stop', 'qualification-cleanup-' + jid, True)
                except Exception:
                    pass
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)
        # Only labelled containers owned by this newly created qualification state.
        if self.args.engine == 'docker':
            for cid in self.docker('ps', '-aq', '--filter', 'label=research-runner.root=' + str(self.state)).split():
                self.docker('rm', '-f', cid)
        if self.log:
            self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['host', 'docker'], default='host')
    parser.add_argument('--codex-bin', help='operator-selected native Codex executable')
    parser.add_argument('--image', default='research-runner-worker:0.2.0')
    parser.add_argument('--auth-file', help='only for optional containerized-agent mode')
    parser.add_argument('--output', required=True, help='new private qualification directory')
    parser.add_argument('--apply', action='store_true', help='launch isolated runner state and short live model work')
    args = parser.parse_args()
    if args.engine == 'docker' and not args.auth_file:
        parser.error('Docker-agent qualification requires --auth-file')
    if not args.apply:
        print(json.dumps({'preview': True, 'engine': args.engine, 'image': args.image if args.engine == 'docker' else None, 'output': str(Path(args.output).resolve()), 'live_model_requests': True}))
        return
    qualification = Qualification(args)
    try:
        qualification.run()
    finally:
        qualification.close()


if __name__ == '__main__':
    main()
