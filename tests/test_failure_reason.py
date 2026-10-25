"""A failed worker reports its own error, not a bare exit code."""
from pathlib import Path
import subprocess, sys, time

import pytest

from runner.package import assemble
from runner.service import Service
from runner.worker import inbox_text, turn_followup


def test_worker_error_event_becomes_job_error(tmp_path, monkeypatch):
 archive = tmp_path / 'job.rrpkg'
 assemble(Path(__file__).resolve().parents[1] / 'sample', archive)
 service = Service(tmp_path / 'state')
 jid, _ = service.submit(archive, 'submit')
 real = subprocess.Popen
 script = "from runner.worker import emit; import sys; emit('error', {'message': 'rate limited by upstream'}); sys.exit(1)"
 def launch(cmd, **kw):
  return real([sys.executable, '-c', script], **kw) if 'runner.worker' in cmd else real(cmd, **kw)
 monkeypatch.setattr('runner.service.subprocess.Popen', launch)
 service.launch(jid, 'start')
 deadline = time.monotonic() + 10
 while time.monotonic() < deadline and (service.store.get(jid)['state'] == 'running' or jid in service.procs):
  time.sleep(.02)
 job = service.store.get(jid)
 assert job['state'] == 'failed'
 assert 'rate limited by upstream' in job['error']


@pytest.mark.parametrize('status,saw,missing,retries,time_left,expected', [
 ('completed', True, False, 2, 100, 'accept'),
 ('failed', True, False, 2, 100, 'retry'),
 ('failed', True, False, 0, 100, 'fail'),
 ('failed', True, True, 0, 100, 'fail'),
 ('failed', True, False, 2, 14, 'fail'),
 ('failed', True, False, 2, 15, 'retry'),
 ('completed', True, True, 2, 100, 'retry'),
 ('completed', True, True, 0, 100, 'accept'),
 (None, False, True, 2, 100, 'retry'),
 (None, False, False, 2, 100, 'accept'),
 (None, False, True, 0, 100, 'accept'),
])
def test_turn_followup(status, saw, missing, retries, time_left, expected):
 assert turn_followup(status, saw, missing, retries, time_left) == expected


def test_inbox_text_discards_non_objects(tmp_path):
 path = tmp_path / 'note.json'
 path.write_text('[1, 2]')
 with pytest.raises(ValueError):
  inbox_text(path)
 path.write_text('{"input": 1}')
 with pytest.raises(ValueError):
  inbox_text(path)
 path.write_text('{"input": "continue"}')
 assert inbox_text(path) == 'continue'
