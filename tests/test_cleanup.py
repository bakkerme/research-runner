"""Cleanup waits out a slow exit and does not fail solely because wait() timed out."""
import os
import subprocess
import time

import pytest

from runner.service import Service, cleanup_seconds


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
