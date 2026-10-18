"""MVP contracts exercised with disposable workers and independent clients."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from runner.client import Client
from runner.package import assemble, digest, verify, validate_manifest
from runner.protocol import Result, schemas
from runner.service import Service
from runner.store import ACTIVE, Store
from runner.web import app

SAMPLE=Path(__file__).resolve().parents[1]/'sample-mvp'


def package(tmp_path,mode='complete',seconds=.1,budget=5,extra_required=False):
 source=tmp_path/'source';shutil.copytree(SAMPLE,source)
 m=json.loads((source/'manifest.json').read_text());m['resources']['time_seconds']=budget
 m['policy']={'finalization_seconds':budget*.5,'termination_seconds':budget*.15,'checkpoint_seconds':.1}
 if extra_required:m['required_outputs'].append('outputs/not-produced.txt')
 (source/'manifest.json').write_text(json.dumps(m))
 (source/'inputs/fixture.json').write_text(json.dumps({'mode':mode,'seconds':seconds,'text':mode,'summary':mode}))
 archive=tmp_path/'job.rrpkg';assemble(source,archive);return archive


def until(predicate,seconds=8):
 deadline=time.monotonic()+seconds
 while time.monotonic()<deadline:
  value=predicate()
  if value:return value
  time.sleep(.025)
 raise AssertionError('condition timed out')


def finished(service,jid):
 until(lambda:service.store.get(jid)['state'] not in ACTIVE)
 # The watcher drops its process ownership just after committing terminal state.
 until(lambda:jid not in service.procs)
 return service.status(jid)


@pytest.mark.parametrize('mode,outcome',[('complete','complete'),('blocked','blocked')])
def test_finish_early_and_published_generation(tmp_path,mode,outcome):
 service=Service(tmp_path/'state','host');archive=package(tmp_path,mode)
 jid,_=service.submit(archive,'submission');launch=service.launch(jid,'launch')
 assert service.launch(jid,'launch')==launch
 status=finished(service,jid)
 assert status['state']=='finished' and status['outcome']==outcome,status
 assert status['remaining_seconds']>0 and status['validation']=='not_run'
 manifest=status['publication'];assert manifest['checkpoint']['summary']==mode
 for entry in manifest['files']:
  data,_=service.download(jid,entry['name'],manifest['generation'])
  assert digest(data)==entry['sha256']
 with pytest.raises(ValueError):service.launch(jid,'new-launch')


def test_soft_timeout_wraps_up_partial_even_with_missing_complete_output(tmp_path):
 service=Service(tmp_path/'state','host');jid,_=service.submit(package(tmp_path,seconds=20,budget=2,extra_required=True),'s')
 service.launch(jid,'r');status=finished(service,jid)
 assert (status['state'],status['outcome'],status['result']['reason'])==('finished','partial','budget_exhausted'),status
 kinds=[e['kind'] for e in service.store.events(jid)]
 assert 'finalize' in kinds and 'finalize_acknowledged' in kinds
 assert service.download(jid,'outputs/partial.txt')[0]==b'complete'


def test_forced_deadline_retains_partial_and_cannot_gain_new_budget(tmp_path):
 service=Service(tmp_path/'state','host');jid,_=service.submit(package(tmp_path,'unresponsive',20,budget=2),'s')
 service.launch(jid,'r');until(lambda:service.store.publication(jid))
 first=service.store.publication(jid);status=finished(service,jid)
 assert status['state']=='interrupted' and status['outcome'] is None,status
 assert service.download(jid,'outputs/partial.txt',first['generation'])[0]==b'unresponsive'
 with pytest.raises(ValueError,match='budget exhausted'):service.launch(jid,'new-attempt')


def test_manual_finalize_is_idempotent_and_readable(tmp_path):
 service=Service(tmp_path/'state','host');jid,_=service.submit(package(tmp_path,seconds=20),'s')
 service.launch(jid,'r');until(lambda:service.store.publication(jid))
 response=service.finalize(jid,'wrap');assert service.finalize(jid,'wrap')==response
 status=finished(service,jid)
 assert status['state']=='finished' and status['result']['reason']=='user_requested',status
 assert len([e for e in service.store.events(jid) if e['kind']=='finalize'])==1


def test_restart_preserves_deadline_and_fences_publications(tmp_path):
 store=Store(tmp_path/'ledger');jid='a'*32
 store.submit(jid,'generic','fixture','s','f'*64)
 policy={'finalization_seconds':2,'termination_seconds':1}
 attempt,token,_,_=store.start_attempt(jid,'r',budget=10,policy=policy)
 before=store.get(jid);store.reconcile()
 after=Store(tmp_path/'ledger');a2,t2,_,_=after.start_attempt(jid,'r2',budget=1000,policy=policy)
 assert after.get(jid)['deadline']==before['deadline']
 with pytest.raises(ValueError,match='stale'):after.append(jid,attempt,token,'progress',{})
 assert a2==2 and t2!=token
 with after.db() as c:c.execute('UPDATE jobs SET terminate_at=? WHERE id=?',(time.time()-1,jid))
 after.reconcile()
 with pytest.raises(ValueError,match='exhausted'):after.start_attempt(jid,'r3',budget=1000,policy=policy)


def test_old_schema_migration_keeps_historical_outcome_unknown(tmp_path):
 path=tmp_path/'old.sqlite3'
 with sqlite3.connect(path) as c:
  c.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,name TEXT,state TEXT,adapter TEXT,attempt INTEGER DEFAULT 0,token TEXT,thread_id TEXT,created REAL,updated REAL,error TEXT)')
  c.execute("INSERT INTO jobs(id,state) VALUES('old','completed')")
 store=Store(path)
 assert store.get('old')['outcome'] is None and store.get('old')['deadline'] is None


def test_contracts_and_missing_complete_deliverable(tmp_path):
 archive=package(tmp_path,extra_required=True);m,_=verify(archive)
 for bad in (float('nan'),float('inf'),True):
  m['resources']['time_seconds']=bad
  with pytest.raises(ValueError):validate_manifest(m)
 with pytest.raises(ValueError):Result.model_validate({'format':'runner-result/v1','outcome':'blocked','reason':'no_actionable_work','summary':'No way forward','next_action':'','blockers':[]})
 service=Service(tmp_path/'state','host');jid,_=service.submit(archive,'s');service.launch(jid,'r')
 status=finished(service,jid)
 assert status['state']=='failed' and status['outcome'] is None
 assert 'required outputs' in status['error']
 # Failed closeout still preserves diagnostics.
 assert service.download(jid,'outputs/result.json')[0]
 for name,value in schemas().items():
  assert json.loads((SAMPLE.parent/'schemas'/f'{name}.json').read_text())==value


def test_published_bytes_are_immutable_and_links_cannot_escape(tmp_path):
 service=Service(tmp_path/'state','host');jid,_=service.submit(package(tmp_path,seconds=20),'s')
 service.launch(jid,'r');until(lambda:service.store.publication(jid))
 proc,attempt,token,cmd,m=service.procs[jid];first=service.store.publication(jid)
 output=service.path(jid)/'work/outputs/partial.txt';output.write_text('updated')
 service._publish(jid,m,attempt,token,False)
 assert service.download(jid,'outputs/partial.txt',first['generation'])[0]==b'complete'
 assert service.download(jid,'outputs/partial.txt')[0]==b'updated'
 output.unlink();output.symlink_to('/etc/passwd')
 with pytest.raises((ValueError,OSError)):service._publish(jid,m,attempt,token,False)
 assert service.download(jid,'outputs/partial.txt')[0]==b'updated'
 output.unlink();output.write_text('restored')
 service.finalize(jid,'f');finished(service,jid)
 with pytest.raises(ValueError,match='stale'):service._publish(jid,m,attempt,token,False)


def test_http_read_control_receipts_paging_and_reconnect(tmp_path,monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_READ_TOKEN','read-secret');monkeypatch.setenv('RESEARCH_RUNNER_CONTROL_TOKEN','control-secret')
 application=app(tmp_path/'state','host')
 with TestClient(application) as client:
  read={'Authorization':'Bearer read-secret'};control={'Authorization':'Bearer control-secret'}
  assert client.get('/api/capabilities').status_code==401
  assert client.get('/api/capabilities',headers=read).json()['features']['datasets'] is False
  archive=package(tmp_path,'blocked')
  assert client.post('/api/jobs',content=archive.read_bytes(),headers=dict(read,**{'Idempotency-Key':'s'})).status_code==403
  response=client.post('/api/jobs',content=archive.read_bytes(),headers=dict(control,**{'Idempotency-Key':'s'}))
  receipt=response.json();jid=receipt['id'];assert receipt['package_sha256']==digest(archive.read_bytes())
  assert client.post('/api/jobs',content=archive.read_bytes(),headers=dict(control,**{'Idempotency-Key':'s'})).json()['id']==jid
  assert client.post(f'/api/jobs/{jid}/start',json={},headers=dict(control,**{'Idempotency-Key':'r'})).status_code==200
  finished(application.state.service,jid)
  status=client.get(f'/api/jobs/{jid}/status',headers=read).json()
  assert status['outcome']=='blocked' and 'token' not in status
  first=client.get(f'/api/jobs/{jid}/event-log?limit=2',headers=read).json()
  second=client.get(f'/api/jobs/{jid}/event-log?after={first["next_cursor"]}',headers=read).json()
  assert first['has_more'] and second['events'][0]['seq']>first['next_cursor']
  assert client.get(f'/api/jobs/{jid}/event-log?limit=1001',headers=read).status_code==422
  endpoint=next(route.endpoint for route in application.routes if getattr(route,'path',None)=='/api/jobs/{jid}/events')
  async def replay():
   stream=await endpoint(jid,after=0,last_event_id=str(first['next_cursor']))
   value=await stream.body_iterator.__anext__();await stream.body_iterator.aclose();return value
  assert f'"seq": {second["events"][0]["seq"]}' in asyncio.run(replay())
  manifest=client.get(f'/api/jobs/{jid}/artifact-manifest',headers=read).json()
  entry=manifest['files'][0]
  downloaded=client.get(f'/api/jobs/{jid}/artifacts/{entry["name"]}?generation={manifest["generation"]}',headers=read)
  assert downloaded.headers['X-Content-SHA256']==digest(downloaded.content)
  assert client.post('/api/session',headers=read).status_code==204
  assert client.get(f'/api/jobs/{jid}/status').status_code==200
  assert client.post(f'/api/jobs/{jid}/start',json={},headers={'Idempotency-Key':'r2'}).status_code==403


def test_client_receipt_survives_lost_response_and_collection_is_verified(tmp_path,monkeypatch):
 archive=package(tmp_path);client=Client('http://runner.test');receipt=tmp_path/'receipt.json'
 assert client.submit(archive,'s',receipt)['preview'] and not receipt.exists()
 calls=[]
 def request(path,method='GET',data=None,key=None,raw=False):
  if path=='/api/capabilities':return {'package_formats':['research-package/v2']}
  calls.append(key)
  if len(calls)==1:raise OSError('response lost')
  return {'id':'a'*32,'package_sha256':digest(archive.read_bytes())}
 monkeypatch.setattr(client,'request',request)
 with pytest.raises(OSError):client.submit(archive,'s',receipt,True)
 assert json.loads(receipt.read_text())['submission_key']=='s'
 assert client.submit(archive,'s',receipt,True)['id']=='a'*32 and calls==['s','s']
 with pytest.raises(ValueError):client.submit(archive,'different-key',receipt,True)
 data=b'workload-independent output'
 manifest={'generation':1,'files':[{'name':'outputs/output.bin','sha256':digest(data),'size':len(data)}]}
 monkeypatch.setattr(client,'request',lambda path,**kwargs:data if kwargs.get('raw') else manifest)
 destination=tmp_path/'collected'
 assert client.collect('a'*32,destination)['preview'] and not destination.exists()
 assert client.collect('a'*32,destination,apply=True)['verified']
 assert (destination/'outputs/output.bin').read_bytes()==data
 with pytest.raises(FileExistsError):client.collect('a'*32,destination,apply=True)
 manifest['files'][0]['sha256']='0'*64
 with pytest.raises(ValueError):client.collect('a'*32,tmp_path/'bad',apply=True)
 assert not (tmp_path/'bad/receipt.json').exists()


def test_real_controller_death_and_resume_keep_artifacts_and_original_budget(tmp_path):
 archive=package(tmp_path,seconds=20,budget=8)
 script='''from runner.service import Service
import sys,time,json
service=Service(sys.argv[1],"host")
jid,_=service.submit(sys.argv[2],"submit")
service.launch(jid,"first-start")
while not service.store.publication(jid):time.sleep(.02)
print(json.dumps({"id":jid,"deadline":service.store.get(jid)["deadline"]}),flush=True)
time.sleep(60)
'''
 state=tmp_path/'state'
 controller=subprocess.Popen([sys.executable,'-u','-c',script,str(state),str(archive)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  info=json.loads(controller.stdout.readline());controller.kill();controller.wait(timeout=5)
  service=Service(state,'host');jid=info['id']
  assert service.store.get(jid)['state']=='interrupted'
  assert service.download(jid,'outputs/partial.txt')[0]==b'complete'
  service.launch(jid,'second-start')
  assert service.store.get(jid)['deadline']==info['deadline']
  until(lambda:service.store.get(jid)['attempt']==2)
  service.finalize(jid,'finish-after-recovery')
  status=finished(service,jid)
  assert status['state']=='finished' and status['outcome']=='partial',status
 finally:
  if controller.poll() is None:controller.kill();controller.wait(timeout=5)


def test_worker_has_independent_deadline_without_a_controller(tmp_path):
 from runner.package import deploy
 archive=package(tmp_path,'unresponsive',seconds=20,budget=3)
 work=tmp_path/'work';deploy(archive,work)
 requests=tmp_path/'requests';requests.mkdir();inbox=tmp_path/'inbox';inbox.mkdir()
 proc=subprocess.Popen([sys.executable,'-m','runner.worker','--work',str(work),'--session',str(tmp_path/'session'),'--request-dir',str(requests),'--input-dir',str(inbox),'--deadline',str(time.time()+.7)],start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 try:
  proc.communicate(timeout=3)
  assert proc.returncode!=0
  assert (work/'outputs/partial.txt').read_text()=='unresponsive'
  assert not (work/'outputs/result.json').exists()
 finally:
  if proc.poll() is None:proc.kill();proc.wait(timeout=5)


def test_idempotency_keys_are_scoped_and_legacy_keys_migrate(tmp_path):
 path=tmp_path/'legacy-keys.sqlite3'
 with sqlite3.connect(path) as c:
  c.execute('CREATE TABLE keys(key TEXT PRIMARY KEY,scope TEXT,result TEXT)')
  c.execute('INSERT INTO keys VALUES(?,?,?)',('same-key','old-scope','{}'))
 store=Store(path)
 for jid in ('a'*32,'b'*32):
  store.submit(jid,'fixture','fixture','submit-'+jid,'0'*64)
  attempt,token,created,response=store.start_attempt(jid,'same-start-key')
  assert created and attempt==1
  store.control(jid,'finalize','same-finalize-key',{'running'},'finalizing',{'reason':'budget_exhausted'})
  assert store.start_attempt(jid,'same-start-key')[3]==response
  store.finish(jid,attempt,token,'interrupted')
 with store.db() as c:
  assert c.execute("SELECT result FROM keys WHERE key='same-key' AND scope='old-scope'").fetchone()['result']=='{}'
