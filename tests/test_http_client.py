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


def test_repo_library_end_to_end_structured_results_and_dashboard_attach(tmp_path, server):
 """A consuming repo owns all producer state and uses the ordinary remote API."""
 from runner import ResearchRepo
 from runner.protocol import schemas
 control,reader=server
 root=tmp_path/'consumer'
 repo=ResearchRepo.init(root,control.url,'experiment')
 repo.client.token='test-control'
 source=root/'research/experiment'
 manifest=json.loads((source/'manifest.json').read_text())
 manifest['adapter']='fixture'
 manifest['resources']['time_seconds']=20
 manifest['policy']={'finalization_seconds':5,'termination_seconds':1,'checkpoint_seconds':.1}
 (source/'manifest.json').write_text(json.dumps(manifest))
 report={'format':'runner-research/v1','scope':'Deterministic lifecycle fixture',
         'findings':[{'id':'fixture','claim':'The fixture wrote retained bytes','confidence':'supported',
                      'evidence':['bytes'],'limitations':['No scientific claim']}],
         'evidence':[{'id':'bytes','path':'outputs/partial.txt','description':'Retained fixture output'}],
         'validation':[{'id':'fixture-check','method':'Fixture emission','status':'passed',
                        'expected':'Retained fixture work','observed':'Retained fixture work','evidence':['bytes']}],
         'reproduction':{'commands':[],'dependencies':[],'notes':'Fixture adapter only'},
         'deliverables':['outputs/partial.txt'],'limitations':['No model or native emulator qualification']}
 (source/'inputs/fixture.json').write_text(json.dumps({'seconds':.1,'research':report}))
 # Payloads contain the schema even without a checkout on the worker/producer.
 assert json.loads((source/'inputs/contracts.json').read_text())['research']==schemas()['research']
 preview=repo.submit('experiment')
 assert preview['preview']
 receipt=repo.submit('experiment',apply=True)
 assert repo.submit('experiment',apply=True)['id']==receipt['id']
 assert repo.status('experiment')['state']=='queued'
 launch=repo.start('experiment',apply=True)
 assert repo.start('experiment',apply=True)==launch
 # Monitoring and collection work with a read-only credential.
 repo.client.token='test-read'
 observations=list(repo.monitor('experiment',interval=.1,timeout=10))
 assert observations[-1]['status']['state']=='finished'
 assert repo.status('experiment')['outcome']=='complete'
 collected=repo.pull('experiment',apply=True)
 assert collected['verified']
 inspected=repo.results('experiment')
 assert inspected['research']['validation'][0]['status']=='passed'
 assert inspected['delivery_verified'] and inspected['independent_validation']=='not_run'
 # Dashboard-submitted jobs can be adopted without a second submission/start.
 adopter=ResearchRepo.init(tmp_path/'another-consumer',control.url,'another')
 adopter.client.token='test-read'
 adopter.attach('from-dashboard',receipt['id'])
 assert adopter.pull('from-dashboard',apply=True)['generation']==collected['generation']


def test_invalid_structured_report_is_not_a_successful_closeout(tmp_path, server):
 from runner import ResearchRepo
 control,_=server
 repo=ResearchRepo.init(tmp_path/'consumer',control.url)
 repo.client.token='test-control'
 source=repo.root/'research/example'
 manifest=json.loads((source/'manifest.json').read_text());manifest['adapter']='fixture'
 (source/'manifest.json').write_text(json.dumps(manifest))
 # A syntactically present report cannot bypass the structured contract.
 (source/'inputs/fixture.json').write_text(json.dumps({'seconds':.1,'research':{'format':'runner-research/v1'}}))
 repo.submit('example',apply=True);repo.start('example',apply=True)
 observations=list(repo.monitor('example',interval=.1,timeout=10))
 assert observations[-1]['status']['state']=='failed'
 assert repo.status('example')['outcome'] is None
 # Failed runs still retain partial diagnostics for inspection.
 assert repo.pull('example',apply=True)['verified']


@pytest.mark.skipif(not os.getenv('RR_INSTALLED_CLIENT'),reason='optional built-wheel CLI qualification')
def test_installed_cli_lifecycle_outside_runner_checkout(tmp_path, server):
 control,_=server
 executable=os.environ['RR_INSTALLED_CLIENT']
 root=tmp_path/'external-consumer';root.mkdir()
 env=os.environ.copy();env.pop('PYTHONPATH',None);env['RESEARCH_RUNNER_TOKEN']='test-control'
 def command(*args):
  result=subprocess.run([executable,*args],cwd=root,env=env,capture_output=True,text=True,timeout=20)
  assert result.returncode==0,(args,result.stdout,result.stderr)
  return result
 command('init','--url',control.url,'--name','experiment')
 source=root/'research/experiment'
 manifest=json.loads((source/'manifest.json').read_text());manifest['adapter']='fixture'
 (source/'manifest.json').write_text(json.dumps(manifest))
 report={'format':'runner-research/v1','scope':'Installed CLI qualification','findings':[],
         'evidence':[{'id':'fixture','path':'outputs/partial.txt','description':'Fixture bytes'}],
         'validation':[{'id':'check','method':'Fixture emission','status':'passed',
                        'expected':'Retained fixture work','observed':'Retained fixture work','evidence':['fixture']}],
         'reproduction':{'commands':[],'dependencies':[],'notes':'Deterministic fixture'},
         'deliverables':['outputs/partial.txt'],'limitations':['Not a live-model test']}
 (source/'inputs/fixture.json').write_text(json.dumps({'research':report}))
 assert json.loads(command('submit','experiment').stdout)['preview']
 receipt=json.loads(command('submit','experiment','--apply').stdout)
 command('start','experiment','--apply')
 updates=[json.loads(line) for line in command('monitor','experiment','--json','--interval','0.1','--timeout','10').stdout.splitlines()]
 assert updates[-1]['status']['state']=='finished'
 assert json.loads(command('status','experiment').stdout)['outcome']=='complete'
 assert json.loads(command('pull','experiment','--apply').stdout)['verified']
 assert json.loads(command('results','experiment').stdout)['research']['scope']=='Installed CLI qualification'
 command('attach','dashboard-alias',receipt['id'])
 command('pull','dashboard-alias','--apply')
 assert len(control.request('/api/jobs'))==1
