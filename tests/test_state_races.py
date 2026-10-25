"""State transitions racing worker events must not fail the job."""
from pathlib import Path
import json
import time
import uuid

from runner.package import assemble
from runner.service import Service


def _started(tmp_path):
 archive = tmp_path / 'job.rrpkg'
 assemble(Path(__file__).resolve().parents[1] / 'sample-mvp', archive)
 service = Service(tmp_path / 'state')
 jid, _ = service.submit(archive, 'submit')
 m = service.manifest(jid)
 attempt, token, _, _ = service.store.start_attempt(jid, 'k', None, m['resources']['time_seconds'], m.get('policy'), 'host')
 return service, jid, attempt, token


def test_approval_during_finalizing_is_declined_not_fatal(tmp_path):
 service, jid, attempt, token = _started(tmp_path)
 service.store.control(jid, 'finalize', 'f', {'running'}, 'finalizing', {'reason': 'user_requested'})
 rid = uuid.uuid4().hex
 service._record(jid, attempt, token, 'approval_request', {'id': rid, 'turn_id': 't', 'details': {}})
 assert json.loads((service.path(jid) / 'requests' / (rid + '.json')).read_text()) == {'decision': 'decline'}
 assert service.store.get(jid)['state'] == 'finalizing'


def test_event_after_attempt_superseded_is_dropped(tmp_path):
 service, jid, attempt, token = _started(tmp_path)
 service.store.finish(jid, attempt, token, 'interrupted', 'stopped')
 before = [event['seq'] for event in service.store.events(jid)]
 service._record(jid, attempt, token, 'progress', {'message': 'late'})
 assert [event['seq'] for event in service.store.events(jid)] == before
 assert service.store.get(jid)['state'] == 'interrupted'


def test_unsafe_approval_id_is_not_written_as_a_path(tmp_path):
 service, jid, attempt, token = _started(tmp_path)
 service.store.control(jid, 'finalize', 'f', {'running'}, 'finalizing', {'reason': 'user_requested'})
 service._record(jid, attempt, token, 'approval_request', {'id': '../' + 'a' * 29, 'turn_id': 't', 'details': {}})
 assert service.store.get(jid)['state'] == 'finalizing'
 assert list((service.path(jid) / 'requests').glob('*.json')) == []
 assert not (service.path(jid) / ('a' * 29 + '.json')).exists()


def test_auto_decline_does_not_follow_a_tmp_symlink(tmp_path):
 service, jid, attempt, token = _started(tmp_path)
 service.store.control(jid, 'finalize', 'f', {'running'}, 'finalizing', {'reason': 'user_requested'})
 rid = uuid.uuid4().hex
 outside = tmp_path / 'outside.txt'
 outside.write_text('keep')
 (service.path(jid) / 'requests' / (rid + '.tmp')).symlink_to(outside)
 service._record(jid, attempt, token, 'approval_request', {'id': rid, 'turn_id': 't', 'details': {'command': 'true'}})
 assert outside.read_text() == 'keep'
 dest = service.path(jid) / 'requests' / (rid + '.json')
 assert dest.is_file() and not dest.is_symlink()
 assert json.loads(dest.read_text()) == {'decision': 'decline'}
 assert not (service.path(jid) / 'requests' / (rid + '.tmp')).exists()
 assert service.store.get(jid)['state'] == 'finalizing'


def test_checkpoint_error_does_not_fail_the_job(tmp_path, monkeypatch):
 archive = tmp_path / 'job.rrpkg'
 assemble(Path(__file__).resolve().parents[1] / 'sample-mvp', archive)
 service = Service(tmp_path / 'state')
 jid, _ = service.submit(archive, 'submit')
 publish = service._publish
 def boom(*args, **kwargs):
  if kwargs.get('final') is False:
   raise RuntimeError('checkpoint blew up')
  return publish(*args, **kwargs)
 monkeypatch.setattr(service, '_publish', boom)
 service.launch(jid, 'start')
 deadline = time.monotonic() + 8
 while time.monotonic() < deadline and (service.store.get(jid)['state'] in ('running', 'finalizing') or jid in service.procs):
  time.sleep(.02)
 job = service.store.get(jid)
 assert job['state'] == 'finished', job.get('error')
 messages = [event['data'].get('message', '') for event in service.store.events(jid) if event['kind'] == 'progress']
 assert any('checkpoint skipped' in message and 'checkpoint blew up' in message for message in messages)
