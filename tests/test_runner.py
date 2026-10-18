import io,json,os,pathlib,tarfile,time
import pytest
from runner.package import assemble,verify,deploy,digest
from runner.service import Service
from runner.store import Store

SAMPLE=pathlib.Path(__file__).resolve().parents[1]/'sample'

def package(tmp_path):
 a=tmp_path/'job.rrpkg';assemble(SAMPLE,a);return a

def wait(s,j):
 for _ in range(100):
  if s.store.get(j)['state']!='running':return s.store.get(j)['state']
  time.sleep(.1)
 raise AssertionError('worker timeout')

def test_determinism_tamper_and_traversal(tmp_path):
 a=package(tmp_path);b=tmp_path/'b.rrpkg';assert assemble(SAMPLE,b)==digest(a.read_bytes())
 m,files=verify(a);assert m['name']=='blue-sky-sample'
 with tarfile.open(tmp_path/'evil.rrpkg','w:gz') as t:
  data=b'x';i=tarfile.TarInfo('../escape');i.size=1;t.addfile(i,io.BytesIO(data))
 with pytest.raises(ValueError):verify(tmp_path/'evil.rrpkg')
 with tarfile.open(a) as t:
  payload={x.name:t.extractfile(x).read() for x in t}
 payload['brief.md']=b'tampered'
 with tarfile.open(tmp_path/'tampered.rrpkg','w:gz') as t:
  for n,data in payload.items():
   x=tarfile.TarInfo(n);x.size=len(data);t.addfile(x,io.BytesIO(data))
 with pytest.raises(ValueError):verify(tmp_path/'tampered.rrpkg')

def test_fixture_replay_duplicate_artifacts(tmp_path):
 s=Service(tmp_path/'state',engine='host');a=package(tmp_path)
 j,created=s.submit(a,'submit-1');assert created
 assert s.submit(a,'submit-1')==(j,False)
 s.launch(j,'run-1');assert wait(s,j)=='completed'
 events=s.store.events(j);assert [e['seq'] for e in events]==sorted(e['seq'] for e in events)
 assert s.store.events(j,events[2]['seq'])==events[3:]
 data,meta=s.download(j,'outputs/report.md');assert digest(data)==meta['sha256']
 artifact=s.path(j)/'artifacts'/meta['sha256'];artifact.chmod(0o644);artifact.write_bytes(b'evil')
 with pytest.raises(ValueError):s.download(j,'outputs/report.md')

def test_restart_fences_stale_and_approvals(tmp_path):
 st=Store(tmp_path/'state.sqlite3');j,_=st.submit('j','name','fixture','s','0'*64)
 a,t,_,_=st.start_attempt(j);st.request_approval(j,a,t,{'id':'r','turn_id':'turn','attempt':a})
 st.reconcile();assert st.get(j)['state']=='interrupted'
 with pytest.raises(ValueError):st.decide(j,'r',a,'turn','accept','k')
 with pytest.raises(ValueError):st.append(j,a,t,'progress',{})
 a2,t2,_,_=st.start_attempt(j);assert a2>a
 with pytest.raises(ValueError):st.finish(j,a,t,'completed')

def test_stop_and_snapshot(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXTURE_DELAY','2')
 s=Service(tmp_path/'state',engine='host');j,_=s.submit(package(tmp_path),'submit')
 s.launch(j,'run');time.sleep(.2);s.stop(j,'stop');assert s.store.get(j)['state']=='interrupted'
 snap=s.snapshot(j,tmp_path/'snapshot.tar.gz');assert snap.exists()
 with tarfile.open(snap) as t:assert 'state.sqlite3' in t.getnames()
 restored=Service.restore(snap,tmp_path/'restored')
 assert (restored/'jobs'/j/'work'/'brief.md').read_bytes()==(s.path(j)/'work'/'brief.md').read_bytes()

def test_web_api_and_reconnect_cursor(tmp_path):
 from fastapi.testclient import TestClient
 from runner.web import app
 with TestClient(app(tmp_path/'web',engine='host')) as client:
  pkg=package(tmp_path)
  r=client.post('/api/jobs',content=pkg.read_bytes(),headers={'Idempotency-Key':'web-submit'})
  assert r.status_code==200;job=r.json()['id']
  assert client.post('/api/jobs',content=pkg.read_bytes(),headers={'Idempotency-Key':'web-submit'}).json()['id']==job
  assert client.get('/api/jobs').json()[0]['id']==job
  assert 'token' not in client.get('/api/jobs/'+job).json()
  r=client.post(f'/api/jobs/{job}/start',json={},headers={'Idempotency-Key':'web-start'})
  assert r.status_code==200
  for _ in range(100):
   if client.get('/api/jobs/'+job).json()['state']!='running':break
   time.sleep(.1)
  assert client.get('/api/jobs/'+job).json()['state']=='completed'
  events=client.app.state.service.store.events(job);cursor=events[2]['seq']
  assert client.app.state.service.store.events(job,cursor)==events[3:]
  import asyncio
  endpoint=next(r.endpoint for r in client.app.routes if getattr(r,'path',None)=='/api/jobs/{jid}/events')
  async def first_replayed():
   response=await endpoint(job,after=cursor)
   return await response.body_iterator.__anext__()
  replay=asyncio.run(first_replayed())
  assert f'\"seq\": {events[3]["seq"]}' in replay
  assert client.get(f'/api/jobs/{job}/artifacts/outputs/report.md').status_code==200

def test_duplicate_submit_payload_binding_and_snapshot_integrity(tmp_path):
 import shutil,sqlite3
 a=package(tmp_path);s=Service(tmp_path/'state',engine='host');j,_=s.submit(a,'same-key')
 variant=tmp_path/'variant';shutil.copytree(SAMPLE,variant)
 manifest=json.loads((variant/'manifest.json').read_text());manifest['name']='different sample'
 (variant/'manifest.json').write_text(json.dumps(manifest))
 b=tmp_path/'variant.rrpkg';assemble(variant,b)
 with pytest.raises(ValueError,match='idempotency key reused'):s.submit(b,'same-key')
 s.launch(j,'run');assert wait(s,j)=='completed'
 original,_=s.download(j,'outputs/report.md')
 snap=s.snapshot(j,tmp_path/'complete-snapshot.tar.gz')
 restored=Service.restore(snap,tmp_path/'restored')
 with sqlite3.connect(restored/'state.sqlite3') as db:assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 again=Service(restored,engine='host')
 copied,meta=again.download(j,'outputs/report.md')
 assert copied==original and digest(copied)==meta['sha256']
 assert again.store.get(j)['state']=='completed'

@pytest.mark.parametrize('options,expected',[
 (['name=seccomp,profile=builtin','name=rootless','name=cgroupns'],'0:0'),
 (['name=seccomp,profile=builtin','name=cgroupns'],None),
])
def test_docker_launch_user_mapping(tmp_path,monkeypatch,options,expected):
 from types import SimpleNamespace
 import runner.service as service
 s=Service(tmp_path/'state',engine='host');j,_=s.submit(package(tmp_path),'submit')
 s.engine='docker';m=json.loads((s.path(j)/'work/package.json').read_text())['manifest']
 def run(cmd,**kwargs):
  if cmd[:3]==['docker','image','inspect']:return SimpleNamespace(returncode=0,stdout=m['runtime']['image_id'],stderr='')
  assert cmd==['docker','info','--format','{{json .SecurityOptions}}']
  return SimpleNamespace(returncode=0,stdout=json.dumps(options),stderr='')
 commands=[]
 def popen(cmd,**kwargs):
  commands.append(cmd)
  raise OSError('test launch stopped')
 monkeypatch.setattr(service.subprocess,'run',run)
 monkeypatch.setattr(service.subprocess,'Popen',popen)
 with pytest.raises(OSError,match='test launch stopped'):s.launch(j,'run')
 cmd=commands[0]
 assert cmd[cmd.index('--user')+1]==(expected or f'{os.getuid()}:{os.getgid()}')
 assert cmd[cmd.index('--cap-drop')+1]=='ALL'
 assert cmd[cmd.index('--security-opt')+1]=='no-new-privileges'

def test_docker_mapping_inspection_failure_does_not_start_attempt(tmp_path,monkeypatch):
 from types import SimpleNamespace
 import runner.service as service
 s=Service(tmp_path/'state',engine='host');j,_=s.submit(package(tmp_path),'submit')
 s.engine='docker';m=json.loads((s.path(j)/'work/package.json').read_text())['manifest']
 def run(cmd,**kwargs):
  if cmd[:3]==['docker','image','inspect']:return SimpleNamespace(returncode=0,stdout=m['runtime']['image_id'],stderr='')
  return SimpleNamespace(returncode=1,stdout='',stderr='daemon unavailable')
 monkeypatch.setattr(service.subprocess,'run',run)
 with pytest.raises(RuntimeError,match='cannot inspect Docker user mapping'):s.launch(j,'run')
 assert s.store.get(j)['state']=='queued'
 assert s.store.get(j)['attempt']==0
