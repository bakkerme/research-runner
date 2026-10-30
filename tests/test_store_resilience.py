"""SQLite connections are closed, and a transient status open does not kill a job."""
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from runner.service import Service, status_backoff, status_interval
from runner.store import ACTIVE, Store
from test_mvp import package


def until(predicate,seconds=8):
 deadline=time.monotonic()+seconds
 while time.monotonic()<deadline:
  if predicate():return True
  time.sleep(.02)
 raise AssertionError('condition timed out')


def _fds():
 return len(list(Path('/proc/self/fd').iterdir()))


def test_store_closes_connections_and_keeps_wal(tmp_path):
 store=Store(tmp_path/'state.sqlite3')
 jid='a'*32
 store.submit(jid,'n','fixture','k','ab'*32)
 for _ in range(20):store.get(jid)
 before=_fds()
 for _ in range(200):
  assert store.get(jid)['state']=='queued'
 assert _fds()-before<8
 with store.db() as c:
  assert c.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
  assert c.execute('PRAGMA busy_timeout').fetchone()[0]==30000
  held=c
 with pytest.raises(sqlite3.ProgrammingError):
  held.execute('SELECT 1')


def test_store_db_commits_on_success_and_rolls_back_and_closes_on_error(tmp_path):
 store=Store(tmp_path/'state.sqlite3')
 jid='b'*32
 store.submit(jid,'n','fixture','k','cd'*32)
 with store.db() as c:
  c.execute('BEGIN IMMEDIATE')
  c.execute('UPDATE jobs SET error=? WHERE id=?',('kept',jid))
 assert store.get(jid)['error']=='kept'
 with pytest.raises(RuntimeError):
  with store.db() as c:
   c.execute('BEGIN IMMEDIATE')
   c.execute('UPDATE jobs SET error=? WHERE id=?',('nope',jid))
   held=c
   raise RuntimeError('boom')
 assert store.get(jid)['error']=='kept'
 with pytest.raises(sqlite3.ProgrammingError):
  held.execute('SELECT 1')


def test_status_read_waits_for_checkpoint_or_soft_deadline():
 assert status_backoff(1)==.05
 assert status_backoff(3)==.2
 assert status_backoff(20)==5
 # Far from finalization the in-memory checkpoint schedule is enough.
 assert status_interval(100,10_000,100,30)==30
 # The soft deadline is sooner than the next checkpoint.
 assert status_interval(100,110,100,30)==10
 # Inside the finalization window, follow the short supervisor wake.
 assert status_interval(100,100,90,30)==.05
 # A checkpoint that is already due is not postponed.
 assert status_interval(100,10_000,50,30)==.05
 assert status_interval(100,10_000,0,30)==.05


def _launch(tmp_path,seconds,budget):
 service=Service(tmp_path/'state','host')
 jid,_=service.submit(package(tmp_path,'unresponsive',seconds=seconds,budget=budget),'s')
 service.launch(jid,'start')
 return service,jid


def _release(monkeypatch,service,jid):
 monkeypatch.undo()
 if service.store.get(jid)['state'] in ACTIVE:
  service.stop(jid,'stop-test')


def test_transient_status_open_does_not_force_stop(tmp_path,monkeypatch):
 service,jid=_launch(tmp_path,seconds=30,budget=60)
 real=service.store.get
 seen={'ok':False}
 def flaky(job_id):
  if threading.current_thread().name.startswith('job-supervisor-'):
   seen['n']=seen.get('n',0)+1
   if seen['n']<=5:raise sqlite3.OperationalError('unable to open database file')
   seen['ok']=True
  return real(job_id)
 monkeypatch.setattr(service.store,'get',flaky)
 try:
  until(lambda:seen['ok'])
  job=real(jid)
  assert job['state']=='running',job
  assert job['error'] is None
  assert service.procs[jid][0].poll() is None
  assert 'unable to open database file' not in (job['error'] or '')
 finally:_release(monkeypatch,service,jid)


def test_status_open_failures_do_not_replace_the_hard_deadline(tmp_path,monkeypatch):
 service,jid=_launch(tmp_path,seconds=30,budget=2)
 real=service.store.get
 def blocked(job_id):
  if threading.current_thread().name.startswith('job-supervisor-'):
   raise sqlite3.OperationalError('unable to open database file')
  return real(job_id)
 monkeypatch.setattr(service.store,'get',blocked)
 try:
  until(lambda:real(jid)['state'] not in ACTIVE,seconds=8)
  job=real(jid)
  assert job['state']=='interrupted',job
  assert job['error']=='deadline forced',job
 finally:_release(monkeypatch,service,jid)


def test_unexpected_supervisor_error_still_stops_the_job(tmp_path,monkeypatch):
 service,jid=_launch(tmp_path,seconds=30,budget=60)
 real=service.store.get
 def boom(job_id):
  if threading.current_thread().name.startswith('job-supervisor-'):
   raise RuntimeError('status bug')
  return real(job_id)
 monkeypatch.setattr(service.store,'get',boom)
 try:
  until(lambda:real(jid)['state'] not in ACTIVE)
  job=real(jid)
  assert job['state']=='interrupted',job
  assert job['error']=='status bug',job
 finally:_release(monkeypatch,service,jid)
