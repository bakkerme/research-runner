import collections
import fcntl
import json
import os
import pathlib
import shutil
import signal
import sqlite3
import subprocess
import tarfile
import threading
import time
import uuid

from .files import read_file
from .package import MAX_FILE, MAX_FILES, MAX_TOTAL, declared, deploy, digest, relpath, verify
from .protocol import Checkpoint, Result, is_mvp, load_contract
from .store import ACTIVE, Store


class Service:
 def __init__(self,root,engine='host',image='research-runner-worker:0.2.0'):
  self.root=pathlib.Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
  self._owner=(self.root/'service.lock').open('a+b')
  try:fcntl.flock(self._owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('another research-runner service owns this state directory')
  self.store=Store(self.root/'state.sqlite3');self.engine=engine;self.image=image
  # Explicit host configuration, never selected by a submitted workload.
  auth=os.environ.get('RESEARCH_RUNNER_CODEX_AUTH_FILE') if engine=='docker' else None
  self.auth_file=pathlib.Path(auth).expanduser().resolve(strict=True) if auth else None
  if self.auth_file and (not self.auth_file.is_file() or ':' in str(self.auth_file)):
   raise ValueError('Codex auth projection must name a regular host file')
  self.procs={};self.stopping=set();self.lock=threading.Lock();self.controls=threading.RLock()
  self._kill_orphans();self.store.reconcile()
  known={j['id'] for j in self.store.list()}
  for p in (self.root/'jobs').glob('*'):
   if p.is_dir() and p.name not in known:shutil.rmtree(p)

 def _kill_orphans(self):
  if self.engine=='host':
   for marker in (self.root/'jobs').glob('*/runtime.json'):
    info=json.loads(marker.read_text());pid=info.get('pid');jid=marker.parent.name
    if pid:
     probe=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
     if 'runner.worker' in probe.stdout and jid in probe.stdout:
      self._kill_group(pid)
      self._verify_group(pid)
    marker.unlink(missing_ok=True)
   return
  q=subprocess.run(['docker','ps','-aq','--filter','label=research-runner.root='+str(self.root)],capture_output=True,text=True,timeout=10)
  if q.returncode:raise RuntimeError('cannot inspect Docker orphans: '+q.stderr[-500:])
  for cid in q.stdout.splitlines():self._remove_container(cid)
  # Container removal is verified above; do not leave a dead launcher's marker
  # blocking every later attempt after a controller restart.
  for marker in (self.root/'jobs').glob('*/runtime.json'):marker.unlink(missing_ok=True)

 def _kill_group(self,pid):
  try:os.killpg(pid,signal.SIGKILL)
  except ProcessLookupError:pass

 def _verify_group(self,pid):
  deadline=time.monotonic()+2
  while True:
   probe=subprocess.run(['ps','-axo','pgid=,stat='],capture_output=True,text=True,timeout=2)
   if probe.returncode:raise RuntimeError('cannot verify owned process cleanup')
   alive=any(parts[0]==str(pid) and not parts[1].startswith('Z')
             for line in probe.stdout.splitlines() if len(parts:=line.split())>=2)
   if not alive:return
   if time.monotonic()>=deadline:raise RuntimeError('owned process group survived termination')
   time.sleep(.025)

 def _remove_container(self,name):
  result=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True,timeout=10)
  inspect=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=10)
  if inspect.returncode==0 or (result.returncode and 'No such' not in result.stderr):
   raise RuntimeError('container cleanup unconfirmed: '+name)

 def path(self,jid):
  if not isinstance(jid,str) or len(jid)!=32 or any(c not in '0123456789abcdef' for c in jid):raise KeyError(jid)
  return self.root/'jobs'/jid

 def manifest(self,jid):
  # Runtime workspaces are writable; the sealed original is authoritative.
  return verify(self.path(jid)/'package.rrpkg')[0]

 def submit(self,archive,key):
  m,_=verify(archive);jid=uuid.uuid4().hex;path=self.path(jid);path.mkdir(parents=True,exist_ok=False)
  try:
   shutil.copyfile(archive,path/'package.rrpkg');deploy(archive,path/'work')
   for name in ('session','requests','inbox'):(path/name).mkdir()
   actual,created=self.store.submit(jid,m['name'],m['adapter'],key,digest(pathlib.Path(archive).read_bytes()))
   if not created:shutil.rmtree(path)
   return actual,created
  except BaseException:
   shutil.rmtree(path,ignore_errors=True);raise

 def launch(self,jid,key,input_text=None):
  with self.lock:
   j=self.store.get(jid)
   if not j:raise KeyError(jid)
   p=self.path(jid);m=self.manifest(jid)
   if j['state'] not in ACTIVE and (jid in self.procs or (p/'runtime.json').exists()):
    raise ValueError('previous worker cleanup is not yet confirmed; reconcile before resume')
   if self.engine=='docker':
    if not {'image','image_id'}<=set(m['runtime']):raise ValueError('containerized Codex requires an explicitly pinned worker image')
    inspect=subprocess.run(['docker','image','inspect',m['runtime']['image'],'--format','{{.Id}}'],capture_output=True,text=True)
    if inspect.returncode or inspect.stdout.strip()!=m['runtime']['image_id']:raise ValueError('runtime image digest mismatch')
    info=subprocess.run(['docker','info','--format','{{json .SecurityOptions}}'],capture_output=True,text=True)
    if info.returncode:raise RuntimeError('cannot inspect Docker user mapping: '+info.stderr[-500:])
    options=json.loads(info.stdout)
    if not isinstance(options,list) or any(not isinstance(o,str) for o in options):raise ValueError('invalid Docker security options')
    docker_user='0:0' if any(o.split(',')[0]=='name=rootless' for o in options) else str(os.getuid())+':'+str(os.getgid())
   attempt,token,created,result=self.store.start_attempt(jid,key,input_text,m['resources']['time_seconds'],m.get('policy'),self.engine)
   if not created:return result
   for folder in ('inbox','requests'):
    for old in (p/folder).glob('*.json'):old.unlink()
   if is_mvp(m):
    # A failed attempt cannot accidentally reuse an old terminal result.
    old=p/'work/outputs/result.json'
    if old.exists() or old.is_symlink():
     retained=p/'retained';retained.mkdir(exist_ok=True);old.rename(retained/f'result-before-{attempt}.json')
   if self.engine=='docker':
    name=f'rr-{jid[:12]}-{attempt}'
    cmd=['docker','run','--rm','--name',name,'--label','research-runner.root='+str(self.root),'--cpus',str(m['resources']['cpu']),'--memory',str(m['resources']['memory_mb'])+'m','--pids-limit','256','--user',docker_user,'--security-opt','no-new-privileges','--cap-drop','ALL','-v',str((p/'work').resolve())+':/job','-v',str((p/'session').resolve())+':/session','-v',str((p/'requests').resolve())+':/requests','-v',str((p/'inbox').resolve())+':/inbox','-e','CODEX_HOME=/session']
    if is_mvp(m):
     for f in declared(m)+['package.json']:
      cmd+=['-v',str((p/'work'/f).resolve())+':/job/'+f+':ro']
    if m['adapter']=='codex' and self.auth_file:
     cmd+=['-v',str(self.auth_file)+':/session/auth.json:ro']
    if m['adapter']=='fixture' and os.environ.get('RR_FIXTURE_DELAY'):cmd+=['-e','FIXTURE_DELAY='+os.environ['RR_FIXTURE_DELAY']]
    cmd += [m['runtime']['image'],'python','-m','runner.worker','--work','/job','--session','/session']
   else:
    cmd=[os.sys.executable,'-m','runner.worker','--work',str(p/'work'),'--session',str(p/'session')]
   cmd+=['--attempt',str(attempt),'--request-dir','/requests' if self.engine=='docker' else str(p/'requests'),'--input-dir','/inbox' if self.engine=='docker' else str(p/'inbox')]
   j=self.store.get(jid)
   if is_mvp(m):cmd+=['--deadline',str(j['deadline'])]
   if j['thread_id'] and m['adapter']=='codex':cmd+=['--thread',j['thread_id']]
   if input_text:cmd+=['--input',input_text]
   env=os.environ.copy()
   for private in ('RESEARCH_RUNNER_READ_TOKEN','RESEARCH_RUNNER_CONTROL_TOKEN','RESEARCH_RUNNER_CODEX_AUTH_FILE'):
    env.pop(private,None)
   if self.engine=='docker':env.pop('OPENAI_API_KEY',None)
   try:proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=env,start_new_session=True)
   except Exception as e:self.store.finish(jid,attempt,token,'failed',str(e));raise
   (p/'runtime.json').write_text(json.dumps({'pid':proc.pid,'attempt':attempt}))
   self.procs[jid]=(proc,attempt,token,cmd,m)
   threading.Thread(target=self._watch,args=(jid,),daemon=True).start()
   return result

 def _watch(self,jid):
  proc,attempt,token,cmd,m=self.procs[jid];done=threading.Event();forced=threading.Event()
  supervisor_errors=[];stderr=collections.deque(maxlen=20)
  def drain():
   while True:
    line=proc.stderr.readline(4096)
    if not line:break
    stderr.append(line)
  threading.Thread(target=drain,daemon=True).start()
  j=self.store.get(jid);start=time.monotonic();wall=time.time()
  hard=start+(j['terminate_at']-wall if is_mvp(m) else m['resources']['time_seconds'])
  soft=start+(j['finalize_at']-wall) if is_mvp(m) else hard
  def supervise():
   last=0
   while not done.wait(.05):
    try:
     now=time.monotonic()
     if now>=hard:
      forced.set();self._stop_process(proc,cmd);return
     if is_mvp(m):
      state=self.store.get(jid)['state']
      if state not in ACTIVE:return
      if now>=soft and state=='running':self.finalize(jid,f'deadline-{jid}-{attempt}','budget_exhausted')
      elif state=='finalizing':self._signal_finalize(jid)
      if now-last>=m['policy']['checkpoint_seconds']:
       last=now
       try:self._publish(jid,m,attempt,token,final=False)
       except (ValueError,OSError):pass # atomically rewritten outputs may be incomplete
    except Exception as e:
     supervisor_errors.append(str(e));forced.set()
     try:self._stop_process(proc,cmd)
     except Exception as stop_error:supervisor_errors.append(str(stop_error))
     return
  timer=threading.Thread(target=supervise,daemon=True);timer.start()
  try:
   if is_mvp(m) and j['state']=='finalizing':self._signal_finalize(jid)
   while True:
    line=proc.stdout.readline(1024*1024+1)
    if not line:break
    if len(line)>1024*1024:raise ValueError('worker event exceeds 1 MiB')
    try:v=json.loads(line)
    except json.JSONDecodeError:
     self.store.append(jid,attempt,token,'worker_text',{'line':line[:2000]});continue
    kind=v['kind'];data=v['data']
    if not isinstance(data,dict):raise ValueError('invalid worker payload')
    if kind=='approval_request':self.store.request_approval(jid,attempt,token,data)
    elif kind in ('progress','codex','thread','turn','error','finalize_acknowledged'):
     self.store.append(jid,attempt,token,kind,data)
    else:raise ValueError('invalid worker event')
   rc=proc.wait(timeout=max(.1,hard-time.monotonic()))
   done.set();timer.join(timeout=15)
   if forced.is_set():raise TimeoutError('deadline forced' if not supervisor_errors else '; '.join(supervisor_errors))
   if jid in self.stopping:raise InterruptedError('stopped by user')
   if rc:raise RuntimeError(''.join(stderr)[-2000:] or 'worker exit '+str(rc))
   # Closeout is only accepted once all execution owned by this attempt is stopped.
   self._stop_process(proc,cmd)
   self._publish(jid,m,attempt,token,final=True)
  except Exception as e:
   done.set();timer.join(timeout=15)
   cleanup_error=None
   try:self._stop_process(proc,cmd)
   except Exception as cleanup:cleanup_error=str(cleanup)
   if is_mvp(m):
    try:self._publish(jid,m,attempt,token,final=False)
    except Exception:pass
   try:
    reason='cleanup unconfirmed: '+cleanup_error if cleanup_error else str(e)
    state='failed' if cleanup_error else ('interrupted' if forced.is_set() or jid in self.stopping else 'failed')
    self.store.finish(jid,attempt,token,state,reason)
   except ValueError:pass
  finally:
   done.set()
   # Keep ownership markers on unconfirmed cleanup, so a restart can retry it.
   if self.store.get(jid)['error'] is None or 'cleanup unconfirmed' not in self.store.get(jid)['error']:
    (self.path(jid)/'runtime.json').unlink(missing_ok=True)
   with self.lock:self.procs.pop(jid,None);self.stopping.discard(jid)

 def _stop_process(self,proc,cmd):
  # Kill the whole process group, including descendants after leader exit.
  self._kill_group(proc.pid)
  proc.wait(timeout=5)
  self._verify_group(proc.pid)
  if self.engine=='docker':self._remove_container(cmd[cmd.index('--name')+1])

 def _signal_finalize(self,jid):
  j=self.store.get(jid);dest=self.path(jid)/'requests/finalize.json'
  if dest.exists():return
  data={'reason':j['finalize_reason'],'deadline':j['deadline'],'attempt':j['attempt']}
  tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.replace(dest)

 def finalize(self,jid,key,reason='user_requested'):
  with self.controls:
   if not is_mvp(self.manifest(jid)):raise ValueError('graceful finalize requires research-package/v2')
   result,created=self.store.control(jid,'finalize',key,ACTIVE,'finalizing',{'reason':reason})
   if created:self._signal_finalize(jid)
   return result

 def stop(self,jid,key):
  with self.controls:
   result,created=self.store.control(jid,'stop',key,ACTIVE,data={})
   if not created:return result
   with self.lock:item=self.procs.get(jid)
   if not item:raise RuntimeError('worker missing; restart service for reconciliation')
   proc,attempt,token,cmd,m=item;self.stopping.add(jid);self._stop_process(proc,cmd)
   # The watcher retains partial artifacts and owns the terminal transition.
  deadline=time.monotonic()+20
  while self.store.get(jid)['state'] in ACTIVE and time.monotonic()<deadline:time.sleep(.01)
  if self.store.get(jid)['state'] in ACTIVE:raise RuntimeError('worker stop reconciliation timed out')
  return {'id':jid,'state':self.store.get(jid)['state']}

 def input(self,jid,text,key):
  if not text or len(text)>65536:raise ValueError('input must contain 1..65536 characters')
  with self.controls:
   result,created=self.store.control(jid,'input',key,{'running'},data={'text':text})
   if created:
    dest=self.path(jid)/'inbox'/(uuid.uuid4().hex+'.json');tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps({'input':text}));tmp.replace(dest)
   return result

 def approve(self,jid,rid,attempt,turn_id,decision,key):
  with self.controls:
   result=self.store.decide(jid,rid,attempt,turn_id,decision,key)
   path=self.path(jid)/'requests'/(rid+'.json');tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps({'decision':decision}));tmp.replace(path)
   return result

 def _publish(self,jid,m,attempt,token,final=True):
  work=self.path(jid)/'work';pub=self.path(jid)/'artifacts';pub.mkdir(exist_ok=True)
  mvp=is_mvp(m);result=None;checkpoint=None;findings=[]
  if mvp:
   names=[];entries=0
   for root,dirs,files in os.walk(work/'outputs',followlinks=False):
    entries+=len(dirs)+len(files)
    if entries>MAX_FILES:raise ValueError('too many output entries')
    if any((pathlib.Path(root)/d).is_symlink() for d in dirs):raise ValueError('output links forbidden')
    names.extend((pathlib.Path(root)/f).relative_to(work).as_posix() for f in files)
  else:names=m['required_outputs']
  validated=[];total=0
  for name in sorted(names):
   data=read_file(work,name);total+=len(data)
   if total>MAX_TOTAL:raise ValueError('output quota exceeded')
   if mvp and name=='outputs/checkpoint.json':checkpoint=load_contract(Checkpoint,data)
   if final and mvp and name=='outputs/result.json':result=load_contract(Result,data)
   if not mvp and name.endswith('findings.json'):
    findings=json.loads(data)
    if not isinstance(findings,list) or any(not isinstance(f,dict) or not f.get('claim') or not f.get('evidence_url') for f in findings):raise ValueError('invalid findings')
   validated.append((name,data))
  if mvp and final:
   if not result or 'outputs/handoff.md' not in names:raise ValueError('missing result.json or handoff.md closeout')
   if result['outcome']=='complete' and not set(m['required_outputs'])<=set(names):raise ValueError('missing complete-scope required outputs')
   j=self.store.get(jid)
   if j['finalize_reason'] and result['reason'] not in (j['finalize_reason'],'no_actionable_work'):
    raise ValueError('result must respect requested finalization reason')
  metadata=[{'name':n,'sha256':digest(b),'size':len(b)} for n,b in validated]
  for meta,(_,data) in zip(metadata,validated):
   dst=pub/meta['sha256']
   if dst.exists():
    if dst.is_symlink() or digest(dst.read_bytes())!=meta['sha256']:raise ValueError('artifact collision or tamper')
   else:
    tmp=pub/(meta['sha256']+'.tmp-'+uuid.uuid4().hex);tmp.write_bytes(data);tmp.chmod(0o444);tmp.replace(dst)
  with self.store.db() as c:
   c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j or j['state'] not in ACTIVE or j['attempt']!=attempt or j['token']!=token:raise ValueError('stale attempt')
   previous=c.execute('SELECT generation,manifest FROM publications WHERE job_id=? ORDER BY generation DESC LIMIT 1',(jid,)).fetchone()
   changed=not previous or json.loads(previous['manifest'])['files']!=metadata
   if metadata and (changed or final):
    generation=previous['generation']+1 if previous else 1
    publication={'format':'runner-artifacts/v1','job_id':jid,'generation':generation,'attempt':attempt,'created':time.time(),'files':metadata,'checkpoint':checkpoint}
    c.execute('INSERT INTO publications VALUES(?,?,?,?)',(jid,generation,attempt,json.dumps(publication)))
    for f in findings:self.store.event(c,jid,attempt,'finding',f)
    for meta in metadata:self.store.event(c,jid,attempt,'artifact',dict(meta,generation=generation))
    self.store.event(c,jid,attempt,'checkpoint_published',{'generation':generation,'checkpoint':checkpoint})
   if final:
    state='finished' if mvp else 'completed'
    c.execute('UPDATE jobs SET state=?,token=NULL,updated=?,outcome=?,result=? WHERE id=?',(state,time.time(),result['outcome'] if result else None,json.dumps(result) if result else None,jid))
    c.execute("UPDATE approvals SET state='expired' WHERE job_id=? AND state='pending'",(jid,))
    self.store.event(c,jid,attempt,state,{'result':result,'validation':'not_run'})
   c.execute('COMMIT')

 def download(self,jid,name,generation=None):
  if not self.store.get(jid):raise KeyError(jid)
  publication=self.store.publication(jid,generation)
  if publication:
   meta=next((f for f in publication['files'] if f['name']==name),None)
  else:
   # Historical v1 artifacts predate publication generations.
   meta=next((e['data'] for e in reversed(self.store.events(jid)) if e['kind']=='artifact' and e['data']['name']==name),None) if generation is None else None
  if not meta:raise FileNotFoundError(name)
  data=read_file(self.path(jid)/'artifacts',meta['sha256'])
  if digest(data)!=meta['sha256']:raise ValueError('artifact hash mismatch')
  return data,meta

 def status(self,jid):
  j=self.store.get(jid)
  if not j:raise KeyError(jid)
  j={k:v for k,v in j.items() if k!='token'}
  with self.store.db() as c:
   last=c.execute('SELECT MAX(seq) AS seq,MAX(ts) AS ts FROM events WHERE job_id=?',(jid,)).fetchone()
   pending=[dict(a) for a in c.execute("SELECT id,attempt,turn_id FROM approvals WHERE job_id=? AND state='pending'",(jid,))]
  j.update(format='runner-status/v1',remaining_seconds=max(0,j['deadline']-time.time()) if j['deadline'] else None,
           last_event=last['seq'] or 0,last_activity=last['ts'],pending_approvals=pending,
           publication=self.store.publication(jid),validation='not_run')
  j['session_store']='host_codex_home' if j['execution_engine']=='host' else ('job_session_mount' if j['execution_engine']=='docker' else 'legacy_unknown')
  if j['result']:j['result']=json.loads(j['result'])
  return j

 def snapshot(self,jid,dest):
  with self.lock:
   if not self.store.get(jid):raise KeyError(jid)
   if any(x['state'] in ACTIVE for x in self.store.list()):raise ValueError('stop all jobs before snapshot')
   for p in (self.root/'jobs').rglob('*'):
    if p.name in ('auth.json','credentials.json','.env') or p.is_symlink():raise ValueError('secret or link in snapshot: '+str(p))
   dest=pathlib.Path(dest)
   with self.store.db() as source:
    target=sqlite3.connect(self.root/'snapshot.sqlite3');source.backup(target);target.close()
   try:
    with tarfile.open(dest,'w:gz') as t:
     t.add(self.root/'snapshot.sqlite3',arcname='state.sqlite3')
     def export_filter(info):
      parts=pathlib.PurePosixPath(info.name).parts
      if any(x in ('requests','inbox') for x in parts) or info.name.endswith('/runtime.json'):return None
      if 'session' in parts:
       rest=parts[parts.index('session')+1:]
       if rest and rest[0]!='sessions':return None
       if info.isfile() and not info.name.endswith('.jsonl'):return None
      return info
     t.add(self.root/'jobs',arcname='jobs',filter=export_filter)
   finally:(self.root/'snapshot.sqlite3').unlink(missing_ok=True)
   return dest
 @staticmethod
 def restore(archive,root):
  root=pathlib.Path(root)
  if root.exists() and any(root.iterdir()):raise ValueError('restore destination must be empty')
  root.mkdir(parents=True,exist_ok=True);total=0;count=0
  with tarfile.open(archive,'r:gz') as t:
   for item in t:
    name=relpath(item.name);count+=1;total+=item.size
    if count>2000 or total>1024*1024*1024 or item.size>128*1024*1024 or not (item.isfile() or item.isdir()):raise ValueError('unsafe snapshot')
    if name!='state.sqlite3' and name!='jobs' and not name.startswith('jobs/'):raise ValueError('unexpected snapshot member')
    target=root/name
    if item.isdir():target.mkdir(parents=True,exist_ok=True)
    else:
     target.parent.mkdir(parents=True,exist_ok=True)
     with t.extractfile(item) as src,target.open('wb') as out:shutil.copyfileobj(src,out)
  if not (root/'state.sqlite3').is_file():raise ValueError('snapshot missing state')
  return root
