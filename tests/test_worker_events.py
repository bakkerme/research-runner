"""Large tool telemetry must not terminate otherwise successful research."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from runner.events import MAX_EVENT_CHARS, encode_event
from runner.package import assemble
from runner.service import Service


@pytest.mark.parametrize('text', ['x', '\x00', '\U0001f600'])
def test_large_output_is_bounded_without_mutating_agent_result(text):
 output = text * (MAX_EVENT_CHARS + 10)
 data = {'method': 'item/completed', 'payload': {'thread_id': 'thread',
         'turn_id': 'turn', 'item': {'id': 'cmd', 'type': 'commandExecution',
         'status': 'completed', 'exit_code': 0, 'aggregated_output': output}}}
 line = encode_event('codex', data)
 assert len(line.encode('utf-8')) + 1 <= MAX_EVENT_CHARS
 event = json.loads(line)['data']
 assert event['runner_log']['truncated'] is True
 assert event['payload']['item']['status'] == 'completed'
 assert event['payload']['item']['exit_code'] == 0
 assert 'truncated' in event['payload']['item']['aggregated_output']
 assert data['payload']['item']['aggregated_output'] == output


def test_aggregate_fallback_preserves_turn_completion_and_identity():
 data = {'method': 'turn/completed', 'payload': {'threadId': 'thread',
         'turn': {'id': 'turn', 'status': 'completed', 'items': ['x' * MAX_EVENT_CHARS] * 3}}}
 event = json.loads(encode_event('codex', data))
 assert len(json.dumps(event)) + 1 <= MAX_EVENT_CHARS
 assert event['data']['payload']['turn'] == {'id': 'turn', 'status': 'completed'}
 assert event['data']['payload']['threadId'] == 'thread'
 assert event['data']['runner_log']['truncated'] is True


@pytest.mark.parametrize('kind,data', [
 ('codex', {'method': 'turn/completed', 'payload': {'turn': {'status': 'failed'}}}),
 ('approval_request', {'id': 'approval', 'details': {'command': 'example'}}),
 ('progress', {'message': 'working'}),
])
def test_normal_events_are_unchanged(kind, data):
 assert json.loads(encode_event(kind, data)) == {'kind': kind, 'data': data}


def test_controller_publishes_success_after_large_tool_output(tmp_path, monkeypatch):
 archive = tmp_path / 'job.rrpkg'
 assemble(Path(__file__).resolve().parents[1] / 'sample', archive)
 service = Service(tmp_path / 'state')
 jid, _ = service.submit(archive, 'submit')
 start_process = subprocess.Popen
 # Run a real disposable worker process and the real controller pipe reader,
 # replacing only the external Codex session with deterministic telemetry.
 script = '''
import sys, json
from pathlib import Path
from runner.worker import emit, fixture
from runner.events import MAX_EVENT_CHARS
emit('codex', {'method':'item/commandExecution/outputDelta',
              'payload':{'itemId':'cmd', 'delta':'\\x00' * (MAX_EVENT_CHARS * 2)}})
emit('codex', {'method':'turn/completed', 'payload':{'turn':{'status':'completed'}}})
root = Path(sys.argv[1])
(root / 'outputs').mkdir(exist_ok=True)
fixture(root, json.loads((root / 'package.json').read_text())['manifest'], Path(sys.argv[2]))
'''
 def launch(cmd, **kwargs):
  if 'runner.worker' not in cmd:
   return start_process(cmd, **kwargs)
  return start_process([sys.executable, '-c', script, str(service.path(jid) / 'work'),
                        str(service.path(jid) / 'requests')], **kwargs)
 monkeypatch.setattr('runner.service.subprocess.Popen', launch)
 service.launch(jid, 'start')
 deadline = time.monotonic() + 10
 while time.monotonic() < deadline and (service.store.get(jid)['state'] == 'running' or jid in service.procs):
  time.sleep(.02)
 status = service.status(jid)
 assert status['state'] == 'completed', status.get('error')
 events = service.store.events(jid)
 codex = [event['data'] for event in events if event['kind'] == 'codex']
 assert codex[0]['runner_log']['truncated'] is True
 assert codex[1]['method'] == 'turn/completed'
 assert service.download(jid, 'outputs/report.md')[0]
