"""Dashboard and JSON API; SSE replays durable event sequence."""
import asyncio,json,os,pathlib,re,tempfile,uuid
from typing import Annotated
from fastapi import FastAPI,Header,HTTPException,Query,Request
from fastapi.responses import HTMLResponse,JSONResponse,Response,StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from .service import Service

class Control(BaseModel):
 input: str|None=None
 request_id: str|None=None
 attempt: int|None=None
 turn_id: str|None=None
 decision: str|None=None

from .dashboard import PAGE

def app(root,engine='host',image='research-runner-worker:0.2.0'):
 from .protocol import capabilities,schemas
 from .package import hash_file,MAX_ARCHIVE
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
 def event_log(jid,after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=1000),before:int|None=Query(None,ge=1),tail:bool=False,activity:bool=False):
  if not svc.store.get(jid):raise HTTPException(404,'job not found')
  backward=tail or before is not None
  items=svc.store.events(jid,after,limit+1,before=before,newest_first=backward,activity=activity);page=[];size=0
  for item in items[:limit]:
   item_size=len(json.dumps(item))
   if page and size+item_size>2*1024*1024:break
   page.append(item);size+=item_size
  if backward:page.reverse()
  return {'events':page,'next_cursor':page[-1]['seq'] if page else after,
          'previous_cursor':page[0]['seq'] if page else None,
          'has_more':not backward and len(items)>len(page),
          'has_previous':backward and len(items)>len(page)}
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
  with tempfile.NamedTemporaryFile(suffix='.rrpkg',dir=svc.root) as f:
   size=0
   async for chunk in request.stream():
    size+=len(chunk)
    if size>MAX_ARCHIVE:raise HTTPException(413,'package too large')
    await run_in_threadpool(f.write,chunk)
   await run_in_threadpool(f.flush)
   package_sha256=await run_in_threadpool(hash_file,f.name)
   try:jid,created=await run_in_threadpool(svc.submit,f.name,idempotency_key)
   except Exception as e:raise HTTPException(400,str(e))
  return {'format':'runner-submission/v1','id':jid,'created':created,'package_sha256':package_sha256,'api_url':str(request.base_url).rstrip('/')+'/api/jobs/'+jid,'dashboard_url':str(request.base_url)}
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
  if not await run_in_threadpool(svc.store.get,jid):raise HTTPException(404,'job not found')
  try:cursor=max(after,int(last_event_id)) if last_event_id else after
  except ValueError:raise HTTPException(400,'invalid Last-Event-ID')
  async def stream():
   nonlocal cursor
   while True:
    for e in await run_in_threadpool(svc.store.events,jid,cursor,100):
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
