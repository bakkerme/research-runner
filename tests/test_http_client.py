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


def test_large_self_contained_package_streams_through_real_http(tmp_path, server, monkeypatch):
 """One input exceeds both old quotas, without payload-sized Python reads."""
 import shutil
 from runner.package import deploy, hash_file, verify
 source=tmp_path/'payload'
 shutil.copytree(Path(__file__).resolve().parents[1]/'sample-mvp',source)
 manifest=json.loads((source/'manifest.json').read_text())
 manifest['inputs'].append('inputs/research-object.bin')
 (source/'manifest.json').write_text(json.dumps(manifest))
 research_object=source/'inputs/research-object.bin'
 size=129*1024*1024
 with research_object.open('wb') as out:out.truncate(size)
 expected=hash_file(research_object)
 import tarfile
 original_read=tarfile.ExFileObject.read
 def bounded_read(self,size=-1):
  assert 0<=size<=1024*1024+1, 'unbounded archive member read'
  return original_read(self,size)
 monkeypatch.setattr(tarfile.ExFileObject,'read',bounded_read)
 def forbidden(*args,**kwargs):raise AssertionError('whole-file read forbidden')
 monkeypatch.setattr(Path,'read_bytes',forbidden)
 archive=tmp_path/'large.rrpkg'
 package_hash=assemble(source,archive)
 _,inventory=verify(archive)
 assert inventory['inputs/research-object.bin']=={'size':size,'sha256':expected}
 deploy(archive,tmp_path/'extracted')
 assert hash_file(tmp_path/'extracted/inputs/research-object.bin')==expected
 control,_=server
 caps=control.request('/api/capabilities')
 assert caps['limits']['file_bytes']>=size
 assert caps['limits']['output_file_bytes']==32*1024*1024
 receipt=control.submit(archive,'large-object',tmp_path/'large-receipt.json',True)
 assert receipt['package_sha256']==package_hash
 job=tmp_path/'state/jobs'/receipt['id']
 assert hash_file(job/'work/inputs/research-object.bin')==expected
 assert (job/'work/inputs/research-object.bin').stat().st_size==size
 assert control.submit(archive,'large-object',tmp_path/'large-receipt.json',True)['id']==receipt['id']

 # Recovery archives also contain the sealed package and its extracted object.
 from runner.service import Service
 service=Service(tmp_path/'snapshot-state')
 jid,_=service.submit(archive,'snapshot-large')
 snapshot=service.snapshot(jid,tmp_path/'snapshot.tar.gz')
 restored=Service.restore(snapshot,tmp_path/'restored')
 assert hash_file(restored/'jobs'/jid/'work/inputs/research-object.bin')==expected


def test_controller_shutdown_with_open_event_stream_releases_lock(tmp_path):
 """An open dashboard SSE connection must not prevent controller recovery."""
 import urllib.request
 from runner.service import Service
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 root=tmp_path/'shutdown-state';url=f'http://127.0.0.1:{port}'
 log=(tmp_path/'shutdown.log').open('w')
 proc=subprocess.Popen([sys.executable,'-m','runner.cli','--state',str(root),'serve','--host','127.0.0.1','--port',str(port)],stdout=subprocess.DEVNULL,stderr=log)
 stream=None
 try:
  client=Client(url)
  for _ in range(100):
   try:client.request('/api/capabilities');break
   except OSError:time.sleep(.025)
  archive=tmp_path/'shutdown.rrpkg';assemble(Path(__file__).resolve().parents[1]/'sample-mvp',archive)
  jid=client.submit(archive,'shutdown',tmp_path/'receipt.json',True)['id']
  stream=urllib.request.urlopen(url+f'/api/jobs/{jid}/events',timeout=5)
  assert stream.readline().startswith(b'id:')
  import signal
  proc.terminate();assert proc.wait(timeout=9) in (0,-signal.SIGTERM)
  log.flush();assert 'Finished server process' in (tmp_path/'shutdown.log').read_text()
  replacement=Service(root);replacement._owner.close()
 finally:
  if stream:stream.close()
  if proc.poll() is None:proc.kill();proc.wait(timeout=5)
  log.close()
