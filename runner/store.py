"""SQLite job/event ledger. Every mutation and its event share one transaction."""
import hashlib,json, math, sqlite3, time, uuid
from pathlib import Path

ACTIVE={'running','finalizing'}
STATES={'queued','running','finalizing','finished','waiting','interrupted','completed','failed'}

class _ClosingConnection:
 """Commit or roll back, then close.

 sqlite3.Connection's context manager ends the transaction and leaves the
 connection open. Store opens one connection per operation, and unclosed
 connections keep the database and WAL descriptors until a later cyclic
 collection. A long job opens often enough for that pile to make a new
 open fail with "unable to open database file" while the file itself is fine.
 """
 def __init__(self,connection):
  self.connection=connection
 def __enter__(self):
  return self.connection.__enter__()
 def __exit__(self,exc_type,exc,tb):
  try:return self.connection.__exit__(exc_type,exc,tb)
  finally:self.connection.close()

class Store:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.db() as c:
   c.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,name TEXT,state TEXT,adapter TEXT,attempt INTEGER DEFAULT 0,token TEXT,thread_id TEXT,created REAL,updated REAL,error TEXT);
   CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,attempt INTEGER,kind TEXT,data TEXT,ts REAL);
   CREATE TABLE IF NOT EXISTS keys(key TEXT PRIMARY KEY,scope TEXT,result TEXT);
   CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,job_id TEXT,attempt INTEGER,turn_id TEXT,request_id TEXT,state TEXT,decision TEXT,created REAL);
   CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);''')
   # Idempotency is scoped to operation/job, not globally to the key string.
   key_columns={r['name']:r['pk'] for r in c.execute('PRAGMA table_info(keys)')}
   if not key_columns.get('scope'):
    c.executescript('''BEGIN IMMEDIATE;
    ALTER TABLE keys RENAME TO keys_legacy;
    CREATE TABLE keys(key TEXT,scope TEXT,result TEXT,PRIMARY KEY(scope,key));
    INSERT INTO keys(key,scope,result) SELECT key,scope,result FROM keys_legacy;
    DROP TABLE keys_legacy;
    COMMIT;''')
   # Additive migration: historical jobs retain null lifecycle/outcome fields.
   columns={r['name'] for r in c.execute('PRAGMA table_info(jobs)')}
   for name,kind in [('started_at','REAL'),('deadline','REAL'),('finalize_at','REAL'),
                     ('terminate_at','REAL'),('finalize_reason','TEXT'),('outcome','TEXT'),
                     ('result','TEXT'),('execution_engine','TEXT')]:
    if name not in columns:c.execute(f'ALTER TABLE jobs ADD COLUMN {name} {kind}')
   c.execute('CREATE TABLE IF NOT EXISTS publications(job_id TEXT,generation INTEGER,attempt INTEGER,manifest TEXT,PRIMARY KEY(job_id,generation))')
 def db(self):
  c=sqlite3.connect(self.path,timeout=30,isolation_level=None)
  try:
   c.row_factory=sqlite3.Row
   c.execute('PRAGMA journal_mode=WAL')
   c.execute('PRAGMA busy_timeout=30000')
  except BaseException:
   c.close();raise
  return _ClosingConnection(c)
 def event(self,c,jid,attempt,kind,data):
  c.execute('INSERT INTO events(job_id,attempt,kind,data,ts) VALUES(?,?,?,?,?)',(jid,attempt,kind,json.dumps(data,sort_keys=True),time.time()))
 def submit(self,jid,name,adapter,key,package_hash):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE')
   old=c.execute('SELECT result FROM keys WHERE key=? AND scope=?',(key,'submit')).fetchone()
   if old:
    prev=json.loads(old['result'])
    if prev['sha256']!=package_hash:raise ValueError('idempotency key reused with different package')
    c.execute('COMMIT');return prev['id'],False
   now=time.time();c.execute('INSERT INTO jobs(id,name,state,adapter,created,updated) VALUES(?,?,?,?,?,?)',(jid,name,'queued',adapter,now,now))
   self.event(c,jid,0,'submitted',{'name':name,'adapter':adapter});c.execute('INSERT INTO keys VALUES(?,?,?)',(key,'submit',json.dumps({'id':jid,'sha256':package_hash})));c.execute('COMMIT');return jid,True
 def control(self,jid,action,key,allowed,new_state=None,data=None):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');scope=f'{jid}:{action}'
   old=c.execute('SELECT result FROM keys WHERE key=? AND scope=?',(key,scope)).fetchone()
   data_hash=hashlib.sha256(json.dumps(data or {},sort_keys=True).encode()).hexdigest()
   prev=json.loads(old['result']) if old else None
   if prev and prev.get('data_hash')!=data_hash:raise ValueError('idempotency key reused with different data')
   j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j:raise KeyError(jid)
   # A stop key applies once per attempt. Repo mode persists that key, so a
   # later resume must be able to stop again with it. Replaying it onto the
   # new attempt would wait for a transition nobody is going to record.
   same_attempt=bool(prev) and prev.get('attempt')==j['attempt']
   if prev and (action!='stop' or same_attempt or j['state'] not in allowed):
    c.execute('COMMIT');return prev,False
   if j['state'] not in allowed:raise ValueError(f'{action} invalid in {j["state"]}')
   if new_state:c.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(new_state,time.time(),jid))
   if action=='finalize':
    c.execute('UPDATE jobs SET finalize_reason=COALESCE(finalize_reason,?) WHERE id=?',((data or {})['reason'],jid))
    c.execute("UPDATE approvals SET state='expired' WHERE job_id=? AND state='pending'",(jid,))
   result={'id':jid,'state':new_state or j['state'],'attempt':j['attempt'],'data_hash':data_hash}
   self.event(c,jid,j['attempt'],action,data or {})
   if old:c.execute('UPDATE keys SET result=? WHERE key=? AND scope=?',(json.dumps(result),key,scope))
   else:c.execute('INSERT INTO keys VALUES(?,?,?)',(key,scope,json.dumps(result)))
   c.execute('COMMIT');return result,True
 def start_attempt(self,jid,key=None,input_text=None,budget=None,policy=None,engine=None):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE')
   if key:
    old=c.execute('SELECT result FROM keys WHERE key=? AND scope=?',(key,f'{jid}:launch')).fetchone()
    if old:
     prev=json.loads(old['result'])
     if prev.get('input')!=input_text:raise ValueError('idempotency key reused with different input')
     c.execute('COMMIT');return None,None,False,prev
   if c.execute("SELECT 1 FROM jobs WHERE state IN ('running','finalizing') LIMIT 1").fetchone():raise ValueError('another job running')
   j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j or j['state'] not in ('queued','interrupted','waiting','failed'):raise ValueError('not startable')
   if engine and j['execution_engine'] and engine!=j['execution_engine']:
    raise ValueError('cannot change a started job execution engine; submit a new job')
   if engine:c.execute('UPDATE jobs SET execution_engine=? WHERE id=?',(engine,jid))
   now=time.time()
   if j['terminate_at'] is not None and now>=j['terminate_at']:raise ValueError('job budget exhausted; submit a new job to grant a new budget')
   if policy and j['started_at'] is None:
    deadline=now+budget
    c.execute('UPDATE jobs SET started_at=?,deadline=?,finalize_at=?,terminate_at=? WHERE id=?',
              (now,deadline,deadline-policy['finalization_seconds'],deadline-policy['termination_seconds'],jid))
   n=j['attempt']+1;token=uuid.uuid4().hex
   state='finalizing' if j['finalize_reason'] or (j['finalize_at'] is not None and now>=j['finalize_at']) else 'running'
   c.execute('UPDATE jobs SET state=?,attempt=?,token=?,updated=?,error=NULL WHERE id=?',(state,n,token,now,jid))
   if state=='finalizing':c.execute("UPDATE jobs SET finalize_reason=COALESCE(finalize_reason,'budget_exhausted') WHERE id=?",(jid,))
   c.execute("UPDATE approvals SET state='expired' WHERE job_id=? AND state='pending'",(jid,))
   self.event(c,jid,n,'started',{'input':input_text})
   result={'id':jid,'attempt':n,'state':state,'input':input_text}
   if key:c.execute('INSERT INTO keys VALUES(?,?,?)',(key,f'{jid}:launch',json.dumps(result)))
   c.execute('COMMIT');return n,token,True,result
 def append(self,jid,attempt,token,kind,data):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j or j['state'] not in ACTIVE or j['attempt']!=attempt or j['token']!=token:raise ValueError('stale attempt')
   if kind=='thread':c.execute('UPDATE jobs SET thread_id=? WHERE id=?',(data['id'],jid))
   self.event(c,jid,attempt,kind,data);c.execute('COMMIT')
 def extend_deadline(self,jid,attempt,token,seconds):
  """Give back time spent provisioning tool images before the worker starts."""
  if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0 or seconds>86400:
   raise ValueError('invalid deadline extension')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j or j['state'] not in ACTIVE or j['attempt']!=attempt or j['token']!=token:raise ValueError('stale attempt')
   if j['deadline'] is None or j['finalize_at'] is None or j['terminate_at'] is None:raise ValueError('job has no deadline to extend')
   c.execute('UPDATE jobs SET deadline=deadline+?,finalize_at=finalize_at+?,terminate_at=terminate_at+?,updated=? WHERE id=?',
             (seconds,seconds,seconds,time.time(),jid))
   self.event(c,jid,attempt,'deadline_extended',{'seconds':seconds});c.execute('COMMIT')
 def finish(self,jid,attempt,token,state,error=None):
  if state not in ('completed','finished','failed','interrupted','waiting'):raise ValueError(state)
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j or j['state'] not in ACTIVE or j['attempt']!=attempt or j['token']!=token:raise ValueError('stale attempt')
   c.execute('UPDATE jobs SET state=?,error=?,token=NULL,updated=? WHERE id=?',(state,error,time.time(),jid))
   c.execute("UPDATE approvals SET state='expired' WHERE job_id=? AND state='pending'",(jid,))
   self.event(c,jid,attempt,state,{'error':error});c.execute('COMMIT')
 def get(self,jid):
  with self.db() as c:
   j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone();return dict(j) if j else None
 def list(self):
  with self.db() as c:return [dict(x) for x in c.execute('SELECT * FROM jobs ORDER BY created DESC')]
 def events(self,jid=None,after=0,limit=None,before=None,newest_first=False,activity=False):
  with self.db() as c:
   sql='SELECT * FROM events WHERE seq>?'+(' AND job_id=?' if jid else '');args=(after,jid) if jid else (after,)
   if activity:sql+=" AND kind!='artifact'"
   if before is not None:sql+=' AND seq<?';args+=(before,)
   sql+=' ORDER BY seq'+(' DESC' if newest_first else '')
   if limit is not None:sql+=' LIMIT ?';args+= (limit,)
   return [{**dict(x),'data':json.loads(x['data'])} for x in c.execute(sql,args)]
 def reconcile(self):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE')
   for j in c.execute("SELECT * FROM jobs WHERE state IN ('running','finalizing')").fetchall():
    c.execute("UPDATE jobs SET state='interrupted',token=NULL,updated=? WHERE id=?",(time.time(),j['id']))
    c.execute("UPDATE approvals SET state='expired' WHERE job_id=? AND state='pending'",(j['id'],))
    self.event(c,j['id'],j['attempt'],'interrupted',{'reason':'service restart; process termination must be verified before resume'})
   c.execute('COMMIT')
 def request_approval(self,jid,attempt,token,data):
  from .events import approval_event_approvable
  if not approval_event_approvable(data):raise ValueError('truncated approval is not approvable')
  rid=data['id'];turn=data.get('turn_id') or ''
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
   if not j or j['state']!='running' or j['attempt']!=attempt or j['token']!=token:raise ValueError('stale attempt')
   c.execute('INSERT INTO approvals(id,job_id,attempt,turn_id,request_id,state,created) VALUES(?,?,?,?,?,?,?)',(rid,jid,attempt,turn,rid,'pending',time.time()))
   self.event(c,jid,attempt,'approval_request',data);c.execute('COMMIT')
 def decide(self,jid,rid,attempt,turn_id,decision,key):
  if decision not in ('accept','decline'):raise ValueError('invalid decision')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');scope=f'{jid}:approval:{rid}'
   old=c.execute('SELECT result FROM keys WHERE key=? AND scope=?',(key,scope)).fetchone()
   if old:
    result=json.loads(old['result'])
    if result['decision']!=decision:raise ValueError('idempotency key reused with different decision')
    c.execute('COMMIT');return result
   j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone();a=c.execute('SELECT * FROM approvals WHERE id=? AND job_id=?',(rid,jid)).fetchone()
   if not j or not a or j['state']!='running' or j['attempt']!=attempt or a['attempt']!=attempt or a['turn_id']!=turn_id:raise ValueError('stale approval')
   if a['state']=='decided' and a['decision']==decision:
    c.execute('COMMIT');return {'id':rid,'decision':decision}
   if a['state']!='pending':raise ValueError('stale approval')
   c.execute('UPDATE approvals SET state=?,decision=? WHERE id=?',('decided',decision,rid))
   self.event(c,jid,attempt,'approval_decision',{'id':rid,'decision':decision,'turn_id':turn_id})
   result={'id':rid,'decision':decision};c.execute('INSERT INTO keys VALUES(?,?,?)',(key,scope,json.dumps(result)));c.execute('COMMIT');return result
 def publication(self,jid,generation=None):
  with self.db() as c:
   row=c.execute('SELECT manifest FROM publications WHERE job_id=?'+(' ORDER BY generation DESC LIMIT 1' if generation is None else ' AND generation=?'),
                 (jid,) if generation is None else (jid,generation)).fetchone()
   return json.loads(row['manifest']) if row else None
