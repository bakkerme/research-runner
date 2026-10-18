import argparse,ipaddress,json,os,pathlib,subprocess,sys
from .package import assemble,verify,deploy,digest
from .service import Service,StateLockedError

def resolve_host(host):
 if host!='tailscale':return host
 try:
  result=subprocess.run(['tailscale','ip','-4'],capture_output=True,text=True,check=True,timeout=5)
  address=ipaddress.IPv4Address(result.stdout.strip())
  if address.is_unspecified or address.is_loopback or address.is_multicast:raise ValueError('not a usable Tailscale address')
 except (OSError,subprocess.SubprocessError,ValueError) as e:
  raise ValueError('Cannot discover Tailscale IPv4 address. Check that Tailscale is installed and connected, or use serve --host 127.0.0.1 for local access.') from e
 return str(address)

def main():
 p=argparse.ArgumentParser(prog='research-runner');s=p.add_subparsers(dest='cmd',required=True)
 for cmd,args in [('assemble',['source','archive']),('verify',['archive']),('deploy',['archive','directory']),('submit',['archive']),('start',['job']),('stop',['job']),('finalize',['job']),('status',['job']),('events',['job']),('collect',['job','output']),('capabilities',[]),('schemas',[]),('download',['job','name','output']),('snapshot',['job','output']),('restore',['archive','directory'])]:
  x=s.add_parser(cmd)
  for arg in args:x.add_argument(arg)
  if cmd in ('submit','start','stop','finalize'):x.add_argument('--key',required=True)
  if cmd in ('submit','start','stop','finalize','collect'):x.add_argument('--apply',action='store_true',help='apply remote mutation; otherwise preview')
  if cmd=='submit':x.add_argument('--receipt',help='durable receipt path (required for remote submission)')
  if cmd in ('events',):x.add_argument('--after',type=int,default=0);x.add_argument('--limit',type=int,default=100)
  if cmd=='collect':x.add_argument('--generation',type=int)
  if cmd=='start':x.add_argument('--input')
 x=s.add_parser('serve');x.add_argument('--host',default='tailscale',help='bind address, or tailscale to discover this device\'s IPv4 address (default: tailscale)');x.add_argument('--port',type=int,default=8765)
 p.add_argument('--state',default=os.getenv('RESEARCH_RUNNER_STATE','./var'));p.add_argument('--engine',choices=['docker','host'],default='host',help='Codex execution location (default: host; independent of workload tool containers)');p.add_argument('--image',default='research-runner-worker:0.2.0')
 p.add_argument('--url',help='remote runner URL; token from RESEARCH_RUNNER_TOKEN')
 a=p.parse_args()
 if a.cmd=='assemble':print(assemble(a.source,a.archive));return
 if a.cmd=='verify':m,_=verify(a.archive);print(json.dumps(m,indent=2));return
 if a.cmd=='deploy':print(json.dumps(deploy(a.archive,a.directory)));return
 if a.cmd=='restore':print(Service.restore(a.archive,a.directory));return
 if a.cmd=='schemas':
  from .protocol import schemas
  print(json.dumps(schemas(),indent=2));return
 if a.url:
  from .client import Client
  from urllib.parse import quote
  client=Client(a.url)
  if a.cmd=='submit':
   if not a.receipt:p.error('remote submission requires --receipt')
   result=client.submit(a.archive,a.key,a.receipt,a.apply)
  elif a.cmd in ('start','stop','finalize'):result=client.control(a.job,a.cmd,a.key,a.apply,{'input':a.input} if a.cmd=='start' and a.input else {})
  elif a.cmd=='status':result=client.request('/api/jobs/'+quote(a.job,safe='')+'/status')
  elif a.cmd=='events':result=client.request('/api/jobs/'+quote(a.job,safe='')+f'/event-log?after={a.after}&limit={a.limit}')
  elif a.cmd=='capabilities':result=client.request('/api/capabilities')
  elif a.cmd=='collect':result=client.collect(a.job,a.output,a.generation,a.apply)
  else:p.error('command not supported over HTTP')
  print(json.dumps(result,indent=2));return
 if a.cmd=='capabilities':
  from .protocol import capabilities
  print(json.dumps(capabilities(),indent=2));return
 if a.cmd=='serve':
  try:host=resolve_host(a.host)
  except ValueError as e:p.error(str(e))
  import uvicorn
  from .web import app
  try:application=app(a.state,a.engine,a.image)
  except StateLockedError as e:p.error(str(e))
  url_host='['+host+']' if ':' in host else host
  application.state.service.set_dashboard_url(f'http://{url_host}:{a.port}')
  print(f'Dashboard: http://{url_host}:{a.port}',flush=True)
  uvicorn.run(application,host=host,port=a.port,timeout_graceful_shutdown=5);return
 try:svc=Service(a.state,a.engine,a.image)
 except StateLockedError as e:p.error(str(e))
 if a.cmd=='submit':print(svc.submit(a.archive,a.key)[0])
 elif a.cmd=='start':
  print(svc.launch(a.job,a.key,a.input),flush=True)
  import time
  while svc.store.get(a.job)['state'] in ('running','finalizing'):time.sleep(.2)
  print(svc.store.get(a.job)['state'])
 elif a.cmd=='stop':print(svc.stop(a.job,a.key))
 elif a.cmd=='finalize':print(svc.finalize(a.job,a.key))
 elif a.cmd=='status':print(json.dumps(svc.status(a.job),indent=2))
 elif a.cmd=='events':print(json.dumps(svc.store.events(a.job,a.after,a.limit),indent=2))
 elif a.cmd=='collect':p.error('collection requires --url; use download for offline artifacts')
 elif a.cmd=='download':
  data,meta=svc.download(a.job,a.name);pathlib.Path(a.output).write_bytes(data);print(meta['sha256'])
 elif a.cmd=='snapshot':print(svc.snapshot(a.job,a.output))
if __name__=='__main__':main()
