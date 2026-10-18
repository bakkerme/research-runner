"""Dashboard and JSON API; SSE replays durable event sequence."""
import asyncio,json,os,pathlib,re,tempfile,uuid
from typing import Annotated
from fastapi import FastAPI,Header,HTTPException,Query,Request
from fastapi.responses import HTMLResponse,JSONResponse,Response,StreamingResponse
from pydantic import BaseModel
from .service import Service

class Control(BaseModel):
 input: str|None=None
 request_id: str|None=None
 attempt: int|None=None
 turn_id: str|None=None
 decision: str|None=None

PAGE='''<!doctype html><html><head><meta charset="utf-8"><title>Research Runner</title><style>body{font:15px system-ui;margin:2rem;max-width:1100px}button,input{padding:.5rem;margin:.25rem}nav{display:flex;gap:2rem}section{border:1px solid #ccc;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto;background:#f6f6f6;padding:1rem}.row{cursor:pointer;border-bottom:1px solid #ddd;padding:.5rem} .state{font-weight:bold}</style></head><body><h1>Research Runner</h1><p>Local single host research jobs</p><section><input id="token" type="password" placeholder="Access token (if configured)"><button onclick="login()">Sign in</button></section><section><input id="pkg" type="file" accept=".rrpkg"><button onclick="submit()">Submit</button></section><nav><section style="min-width:250px"><h2>Jobs</h2><div id="jobs"></div></section><section style="flex:1"><h2 id="title">Select a job</h2><div id="status"></div><div id="controls"></div><h3>Progress and tools</h3><pre id="events"></pre><h3>Findings</h3><div id="findings"></div><h3>Artifacts</h3><div id="artifacts"></div></section></nav><script>
let current=null,seen=0,es=null,lastState=null;const key=()=>{if(typeof crypto.randomUUID==='function')return crypto.randomUUID();const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;};const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function api(path,opt={}){let r=await fetch(path,opt);let b=await r.json();if(!r.ok)throw Error(b.detail||r.status);return b}
async function login(){try{let r=await fetch('/api/session',{method:'POST',headers:{'Authorization':'Bearer '+document.querySelector('#token').value}});if(!r.ok)throw Error('Sign-in failed');document.querySelector('#token').value='';await refresh()}catch(e){alert(e.message)}}
async function refresh(){let jobs=await api('/api/jobs');document.querySelector('#jobs').innerHTML=jobs.map(j=>`<div class="row" onclick="openJob('${j.id}')">${esc(j.name)} <span class="state">${j.state}</span><br><small>${j.id}</small></div>`).join('');if(current){let j=jobs.find(x=>x.id===current);if(j){document.querySelector('#title').textContent=j.name+' · '+j.state;let s=await api(`/api/jobs/${current}/status`);document.querySelector('#status').textContent=(s.outcome?'Outcome: '+s.outcome+' · ':'')+(s.remaining_seconds===null?'':'Budget remaining: '+Math.ceil(s.remaining_seconds)+'s · ')+(s.finalize_reason||'')+' · Validation: '+s.validation;if(lastState!==j.state){lastState=j.state;document.querySelector('#controls').innerHTML=`<button onclick="act('start')" ${['queued','waiting','interrupted','failed'].includes(j.state)?'':'disabled'}>Start/resume</button><button onclick="act('stop')" ${['running','finalizing'].includes(j.state)?'':'disabled'}>Stop</button><button onclick="act('finalize')" ${j.state==='running'?'':'disabled'}>Finish up</button><input id="input" placeholder="Continuation input" ${j.state==='running'?'':'disabled'}><button onclick="act('input')" ${j.state==='running'?'':'disabled'}>Send input</button>`}}}}
async function submit(){let f=document.querySelector('#pkg').files[0];if(!f)return;try{let r=await api('/api/jobs',{method:'POST',headers:{'Idempotency-Key':key()},body:f});await refresh();openJob(r.id)}catch(e){alert(e.message)}}
async function act(action){if(!current)return;let input=document.querySelector('#input').value;try{await api(`/api/jobs/${current}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key()},body:JSON.stringify({input})});await refresh()}catch(e){alert(e.message)}}
function render(e){let d=e.data;if(e.kind==='approval_request'){let p=document.createElement('p');p.id='approval-'+d.id;p.textContent='Approval requested: '+d.method+' · attempt '+d.attempt+' · turn '+d.turn_id+' '+JSON.stringify(d.details||{})+' ';for(let choice of ['accept','decline']){let b=document.createElement('button');b.textContent=choice;b.onclick=()=>decide(d,choice);p.append(b)}document.querySelector('#controls').append(p)}else if(e.kind==='approval_decision'){let p=document.getElementById('approval-'+d.id);if(p)p.textContent='Approval '+d.decision}else if(e.kind==='finding'){let p=document.createElement('p');p.textContent=d.claim+' — '+(d.evidence_url||'');document.querySelector('#findings').append(p)}else if(e.kind==='artifact'){let a=document.createElement('a');let old=document.getElementById('artifact-'+d.name);if(old)old.remove();a.id='artifact-'+d.name;a.href=`/api/jobs/${current}/artifacts/${encodeURIComponent(d.name)}${d.generation?'?generation='+d.generation:''}`;a.textContent=d.name+' (SHA-256 '+d.sha256.slice(0,12)+'…)';document.querySelector('#artifacts').append(a,document.createElement('br'))}else{let p=document.querySelector('#events');p.textContent+=`#${e.seq} ${e.kind} ${JSON.stringify(d)}\n`;p.scrollTop=p.scrollHeight}}
async function decide(d,decision){try{await api(`/api/jobs/${current}/approve`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key()},body:JSON.stringify({request_id:d.id,attempt:d.attempt,turn_id:d.turn_id,decision})})}catch(e){alert(e.message)}}
function openJob(id){if(es)es.close();current=id;seen=0;lastState=null;document.querySelector('#events').textContent='';document.querySelector('#findings').textContent='';document.querySelector('#artifacts').textContent='';refresh();es=new EventSource(`/api/jobs/${id}/events?after=0`);es.onmessage=x=>{let e=JSON.parse(x.data);if(e.seq>seen){seen=e.seq;render(e)}};es.onerror=()=>{}}setInterval(()=>refresh().catch(()=>{}),3000);refresh().catch(()=>{});</script></body></html>'''

def app(root,engine='host',image='research-runner-worker:0.2.0'):
 from .protocol import capabilities,schemas
 from .package import digest,MAX_TOTAL
 import secrets
 read_token=os.getenv('RESEARCH_RUNNER_READ_TOKEN');control_token=os.getenv('RESEARCH_RUNNER_CONTROL_TOKEN')
 if bool(read_token)!=bool(control_token) or (read_token and read_token==control_token):
  raise ValueError('configure distinct nonempty read and control tokens, or neither for trusted local use')
 svc=Service(root,engine,image);a=FastAPI()
 @a.middleware('http')
 async def auth(request,call_next):
  if request.url.path.startswith('/api/') and read_token:
   bearer=request.headers.get('authorization','')
   credential=bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('rr_token','')
   role='control' if secrets.compare_digest(credential.encode(),control_token.encode()) else ('read' if secrets.compare_digest(credential.encode(),read_token.encode()) else None)
   if not role:return JSONResponse({'detail':'Unauthorized'},status_code=401)
   if request.method not in ('GET','HEAD') and role!='control' and request.url.path!='/api/session':return JSONResponse({'detail':'Read-only credential'},status_code=403)
   if not bearer and request.method not in ('GET','HEAD'):
    origin=request.headers.get('origin')
    if origin and origin!=str(request.base_url).rstrip('/'):return JSONResponse({'detail':'Cross-origin control denied'},status_code=403)
  return await call_next(request)
 @a.post('/api/session')
 def session(request: Request):
  response=Response(status_code=204)
  credential=request.headers.get('authorization','').removeprefix('Bearer ')
  if read_token:response.set_cookie('rr_token',credential,httponly=True,samesite='strict',secure=request.url.scheme=='https',path='/api')
  return response
 @a.get('/')
 def page():return HTMLResponse(PAGE)
 @a.get('/api/capabilities')
 def caps():return dict(capabilities(),engine=engine,access='read_control_tokens' if read_token else 'trusted_network',resource_enforcement='container_limits' if engine=='docker' else 'deadline_and_owned_processes')
 @a.get('/api/schemas')
 def contract_schemas():return schemas()
 @a.get('/api/jobs')
 def jobs():return [{k:v for k,v in j.items() if k!='token'} for j in svc.store.list()]
 @a.get('/api/jobs/{jid}')
 def job(jid):
  j=svc.store.get(jid)
  if not j:raise HTTPException(404,'job not found')
  return {k:v for k,v in j.items() if k!='token'}
 @a.get('/api/jobs/{jid}/status')
 def status(jid):
  try:return svc.status(jid)
  except KeyError:raise HTTPException(404,'job not found')
 @a.get('/api/jobs/{jid}/event-log')
 def event_log(jid,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=1000)):
  if not svc.store.get(jid):raise HTTPException(404,'job not found')
  items=svc.store.events(jid,after,limit+1);page=[];size=0
  for item in items[:limit]:
   item_size=len(json.dumps(item))
   if page and size+item_size>2*1024*1024:break
   page.append(item);size+=item_size
  return {'events':page,'next_cursor':page[-1]['seq'] if page else after,'has_more':len(items)>len(page)}
 @a.get('/api/jobs/{jid}/artifact-manifest')
 def artifact_manifest(jid,generation:int|None=Query(None,ge=1)):
  if not svc.store.get(jid):raise HTTPException(404,'job not found')
  data=svc.store.publication(jid,generation)
  if not data:raise HTTPException(404,'no published generation')
  return data
 @a.get('/api/jobs/{jid}/checkpoint')
 def checkpoint(jid):
  if not svc.store.get(jid):raise HTTPException(404,'job not found')
  data=svc.store.publication(jid)
  return {'generation':data['generation'] if data else None,'checkpoint':data['checkpoint'] if data else None}
 @a.post('/api/jobs')
 async def submit(request: Request,idempotency_key:str=Header(...,min_length=1,max_length=200)):
  data=bytearray()
  async for chunk in request.stream():
   data.extend(chunk)
   if len(data)>MAX_TOTAL:raise HTTPException(413,'package too large')
  with tempfile.NamedTemporaryFile(suffix='.rrpkg') as f:
   f.write(data);f.flush()
   try:jid,created=svc.submit(f.name,idempotency_key)
   except Exception as e:raise HTTPException(400,str(e))
  return {'format':'runner-submission/v1','id':jid,'created':created,'package_sha256':digest(data),'api_url':str(request.base_url).rstrip('/')+'/api/jobs/'+jid,'dashboard_url':str(request.base_url)}
 @a.post('/api/jobs/{jid}/{action}')
 def control(jid,action,body:Control,idempotency_key:str=Header(...,min_length=1,max_length=200)):
  try:
   if action in ('start','resume'):return svc.launch(jid,idempotency_key,body.input)
   if action=='input':return svc.input(jid,body.input,idempotency_key)
   if action=='stop':return svc.stop(jid,idempotency_key)
   if action=='finalize':return svc.finalize(jid,idempotency_key)
   if action=='approve':
    if not body.request_id or body.attempt is None or not body.turn_id or not body.decision:raise ValueError('approval identity required')
    return svc.approve(jid,body.request_id,body.attempt,body.turn_id,body.decision,idempotency_key)
   raise ValueError('unknown action')
  except KeyError:raise HTTPException(404,'job not found')
  except Exception as e:raise HTTPException(409,str(e))
 @a.get('/api/jobs/{jid}/events')
 async def events(jid,after:Annotated[int,Query(ge=0)]=0,last_event_id:Annotated[str|None,Header()]=None):
  if not svc.store.get(jid):raise HTTPException(404,'job not found')
  try:cursor=max(after,int(last_event_id)) if last_event_id else after
  except ValueError:raise HTTPException(400,'invalid Last-Event-ID')
  async def stream():
   nonlocal cursor
   while True:
    for e in svc.store.events(jid,cursor,100):
     cursor=e['seq'];yield 'id: '+str(cursor)+'\ndata: '+json.dumps(e)+'\n\n'
    await asyncio.sleep(.4)
  return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store'})
 @a.get('/api/jobs/{jid}/artifacts/{name:path}')
 def artifact(jid,name,generation:int|None=Query(None,ge=1)):
  try:
   data,meta=svc.download(jid,name,generation)
   return Response(data,headers={'X-Content-SHA256':meta['sha256'],'Content-Disposition':'attachment; filename="'+re.sub('[^A-Za-z0-9._-]','_',pathlib.Path(name).name)+'"'})
  except (FileNotFoundError,KeyError):raise HTTPException(404,'artifact not found')
  except Exception as e:raise HTTPException(409,str(e))
 a.state.service=svc
 return a
