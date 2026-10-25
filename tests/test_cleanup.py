"""Cleanup waits out a slow exit and does not fail solely because wait() timed out."""
import os
import subprocess
import threading
import time

import pytest

from runner.service import Service, cleanup_seconds, stop_reconcile_timeout, supervisor_join_timeout


def test_supervisor_join_covers_one_cleanup(monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '20')
 assert supervisor_join_timeout() >= 20 * 3
 assert stop_reconcile_timeout() >= supervisor_join_timeout() + 5


def test_repeated_stop_waits_for_the_terminal_state(tmp_path, monkeypatch):
 monkeypatch.setattr('runner.service.stop_reconcile_timeout', lambda: 1.0)
 service = Service(tmp_path / 'state')
 try:
  jid = 'ab' * 16
  service.store.submit(jid, 'name', 'fixture', 'submit-key', '0' * 64)
  attempt, token, _, _ = service.store.start_attempt(jid)
  service.store.control(jid, 'stop', 'stop-key', ('running', 'finalizing'), data={})
  def finish():
   time.sleep(.25)
   service.store.finish(jid, attempt, token, 'interrupted', 'stopped by user')
  threading.Thread(target=finish, daemon=True).start()
  started = time.monotonic()
  assert service.stop(jid, 'stop-key')['state'] == 'interrupted'
  assert time.monotonic() - started >= .2
  assert service.stop(jid, 'stop-key')['state'] == 'interrupted'
 finally:
  service._owner.close()


def test_repeated_stop_times_out_while_the_job_stays_active(tmp_path, monkeypatch):
 monkeypatch.setattr('runner.service.stop_reconcile_timeout', lambda: .15)
 service = Service(tmp_path / 'state')
 try:
  jid = 'cd' * 16
  service.store.submit(jid, 'name', 'fixture', 'submit-key', '0' * 64)
  service.store.start_attempt(jid)
  service.store.control(jid, 'stop', 'stop-key', ('running', 'finalizing'), data={})
  started = time.monotonic()
  with pytest.raises(RuntimeError, match='worker stop reconciliation timed out'):
   service.stop(jid, 'stop-key')
  assert time.monotonic() - started < 1
 finally:
  service._owner.close()


def test_reused_stop_key_stops_the_resumed_attempt(tmp_path, monkeypatch):
 monkeypatch.setattr('runner.service.stop_reconcile_timeout', lambda: 1.0)
 service = Service(tmp_path / 'state')
 try:
  jid = 'ef' * 16
  service.store.submit(jid, 'name', 'fixture', 'submit-key', '0' * 64)
  attempt, token, _, _ = service.store.start_attempt(jid)
  service.store.control(jid, 'stop', 'stop-key', ('running', 'finalizing'), data={})
  service.store.finish(jid, attempt, token, 'interrupted', 'stopped by user')
  attempt2, token2, _, _ = service.store.start_attempt(jid)
  class Proc:
   pid = 1
   def wait(self, timeout=None):
    return 0
  service.procs[jid] = (Proc(), attempt2, token2, ['worker'], {})
  stopped = {}
  def stop_process(proc, cmd):
   stopped['attempt'] = attempt2
   service.store.finish(jid, attempt2, token2, 'interrupted', 'stopped by user')
  service._stop_process = stop_process
  started = time.monotonic()
  assert service.stop(jid, 'stop-key')['state'] == 'interrupted'
  assert stopped['attempt'] == attempt2
  assert time.monotonic() - started < 0.5
  assert service.store.get(jid)['attempt'] == attempt2
 finally:
  service._owner.close()


def test_stop_process_calls_do_not_overlap(tmp_path, monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '0.05')
 service = Service(tmp_path / 'state')
 try:
  state = {'active': 0, 'max': 0}
  gate = threading.Lock()
  def verify(pid):
   with gate:
    state['active'] += 1
    state['max'] = max(state['max'], state['active'])
   time.sleep(.15)
   with gate:
    state['active'] -= 1
  service._verify_group = verify
  service._kill_group = lambda pid: None
  class Proc:
   pid = 1
   def wait(self, timeout):
    return 0
  threads = [threading.Thread(target=service._stop_process, args=(Proc(), ['worker'])) for _ in range(2)]
  for thread in threads: thread.start()
  for thread in threads: thread.join(timeout=2)
  assert state['max'] == 1
  assert all(not thread.is_alive() for thread in threads)
 finally:
  service._owner.close()


def test_cleanup_seconds_reads_the_environment(monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '0.25')
 assert cleanup_seconds() == 0.25
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', 'nope')
 assert cleanup_seconds() == 20
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '-3')
 assert cleanup_seconds() == 20


def test_stop_process_tolerates_late_reap_when_group_is_gone(tmp_path, monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '0.2')
 service = Service(tmp_path / 'state')
 try:
  class Proc:
   pid = 42424242
   def wait(self, timeout):
    assert timeout == 0.2
    raise subprocess.TimeoutExpired(cmd='worker', timeout=timeout)
  calls = {'n': 0}
  def run(cmd, **kwargs):
   calls['n'] += 1
   return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
  monkeypatch.setattr('runner.service.subprocess.run', run)
  service._kill_group = lambda pid: None
  service._stop_process(Proc(), ['worker'])
  assert calls['n'] >= 1
 finally:
  service._owner.close()


def test_verify_group_uses_the_configured_window(tmp_path, monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '0.15')
 service = Service(tmp_path / 'state')
 try:
  def run(cmd, **kwargs):
   return subprocess.CompletedProcess(cmd, 0, stdout=f'{os.getpid()} R\n', stderr='')
  monkeypatch.setattr('runner.service.subprocess.run', run)
  started = time.monotonic()
  with pytest.raises(RuntimeError, match='survived termination'):
   service._verify_group(os.getpid())
  assert time.monotonic() - started < 1.5
 finally:
  service._owner.close()


def test_docker_rm_timeout_follows_cleanup_window(tmp_path, monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CLEANUP_SECONDS', '45')
 service = Service(tmp_path / 'state')
 try:
  timeouts = []
  def run(cmd, **kwargs):
   timeouts.append((cmd[:2], kwargs.get('timeout')))
   if cmd[:2] == ['docker', 'inspect']:
    return subprocess.CompletedProcess(cmd, 1, '', 'Error: No such object')
   return subprocess.CompletedProcess(cmd, 0, '', '')
  monkeypatch.setattr('runner.service.subprocess.run', run)
  service._remove_container('rr-example')
  assert (['docker', 'rm'], 45) in timeouts
 finally:
  service._owner.close()
