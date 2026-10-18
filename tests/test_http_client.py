"""End-to-end stdlib HTTP client against a real disposable server."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from runner.client import Client
from runner.package import assemble


@pytest.fixture
def server(tmp_path):
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 env=os.environ.copy();env['RESEARCH_RUNNER_READ_TOKEN']='test-read';env['RESEARCH_RUNNER_CONTROL_TOKEN']='test-control'
 proc=subprocess.Popen([sys.executable,'-m','runner.cli','--state',str(tmp_path/'state'),'--engine','host','serve','--host','127.0.0.1','--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,env=env)
 control=Client(f'http://127.0.0.1:{port}','test-control');reader=Client(control.url,'test-read')
 try:
  for _ in range(100):
   if proc.poll() is not None:raise AssertionError(proc.stderr.read().decode())
   try:control.request('/api/capabilities');break
   except (OSError,ValueError):time.sleep(.025)
  else:raise AssertionError('server did not become ready')
  yield control,reader
 finally:
  proc.terminate()
  try:proc.wait(timeout=5)
  except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


def test_actual_http_receipts_status_and_verified_collection(tmp_path,server):
 control,reader=server;archive=tmp_path/'job.rrpkg'
 assemble(Path(__file__).resolve().parents[1]/'sample-mvp',archive)
 receipt=control.submit(archive,'integration-submit',tmp_path/'submission.json',True)
 jid=receipt['id'];launch=control.control(jid,'start','integration-launch',True)
 assert control.control(jid,'start','integration-launch',True)==launch
 for _ in range(100):
  status=reader.request(f'/api/jobs/{jid}/status')
  if status['state'] not in ('running','finalizing'):break
  time.sleep(.025)
 assert status['state']=='finished' and status['outcome']=='complete',status
 assert reader.request(f'/api/jobs/{jid}/event-log?limit=1')['has_more']
 with pytest.raises(ValueError,match='403'):reader.control(jid,'stop','read-must-not-stop',True)
 with pytest.raises(ValueError,match='401'):Client(control.url,'wrong-token').request('/api/jobs')
 result=reader.collect(jid,tmp_path/'collected',apply=True)
 assert result['verified']
 assert json.loads((tmp_path/'collected/outputs/result.json').read_text())['outcome']=='complete'
 assert json.loads((tmp_path/'submission.json').read_text())['id']==jid
