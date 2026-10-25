"""Disposable worker; stdout is a bounded JSONL protocol."""
import argparse
import json
import os
import pathlib
import signal
import shutil
import sys
import threading
import time
import uuid

from .protocol import is_mvp
from .events import approval_should_wait, encode_event


def emit(kind,data):
 print(encode_event(kind,data),flush=True)


def atomic(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.replace(path)


def fixture(root,m,requests):
 emit('progress',{'message':'Reading brief'})
 if not is_mvp(m):
  time.sleep(float(os.getenv('FIXTURE_DELAY','0.1')))
  (root/'outputs/report.md').write_text('# Why the sky is blue\n\nShorter blue wavelengths scatter more strongly in the atmosphere.\n\nSources: https://science.nasa.gov/earth/why-is-the-sky-blue/ and https://www.noaa.gov/jetstream/atmosphere/why-sky-blue/\n')
  atomic(root/'outputs/findings.json',[{'claim':'Air molecules scatter short blue wavelengths more strongly','evidence_url':'https://science.nasa.gov/earth/why-is-the-sky-blue/'}])
  emit('progress',{'message':'Outputs written'});return
 # Deterministic fixture adapter, not model output. A fixture input selects a scenario.
 config=json.loads((root/'inputs/fixture.json').read_text())
 atomic(root/'outputs/checkpoint.json',{'format':'runner-checkpoint/v1','summary':config.get('summary','Fixture started'),'next_action':'Finish fixture','blockers':[]})
 (root/'outputs/partial.txt').write_text(config.get('text','Retained fixture work'))
 duration=config.get('seconds',.1);start=time.monotonic();mode=config.get('mode','complete')
 while time.monotonic()-start<duration:
  if (requests/'finalize.json').exists() and mode!='unresponsive':
   control=json.loads((requests/'finalize.json').read_text())
   emit('finalize_acknowledged',control)
   outcome='partial';reason=control['reason'];break
  time.sleep(.02)
 else:
  outcome='blocked' if mode=='blocked' else 'complete'
  reason='no_actionable_work' if mode=='blocked' else 'scope_complete'
 blockers=['Fixture requires an unavailable input'] if outcome=='blocked' else []
 (root/'outputs/handoff.md').write_text(config.get('summary','Fixture outcome')+'\n')
 atomic(root/'outputs/result.json',{'format':'runner-result/v1','outcome':outcome,'reason':reason,'summary':config.get('summary','Fixture outcome'),'blockers':blockers,'next_action':'Supply missing input' if blockers else ''})
 if 'research' in config:atomic(root/'outputs/research.json',config['research'])
 emit('progress',{'message':'Outputs written by fixture adapter'})


def main():
 p=argparse.ArgumentParser()
 for name in ('work','session'):p.add_argument('--'+name,required=True)
 for name in ('thread','input','request-dir','input-dir'):p.add_argument('--'+name)
 p.add_argument('--deadline',type=float);p.add_argument('--attempt',type=int,default=0);a=p.parse_args()
 root=pathlib.Path(a.work);m=json.loads((root/'package.json').read_text())['manifest']
 if is_mvp(m) and a.deadline:
  # Independent fallback if the controller dies. Docker stops container children
  # when PID 1 exits; native host workers own a session/process group.
  def expire():
   try:
    if os.getsid(0)==os.getpid():os.killpg(os.getpgrp(),signal.SIGKILL)
   finally:os._exit(124)
  safety=threading.Timer(max(.001,a.deadline-time.time()),expire);safety.daemon=True;safety.start()
 pathlib.Path(a.session).mkdir(parents=True,exist_ok=True);(root/'outputs').mkdir(exist_ok=True)
 requests=pathlib.Path(a.request_dir)
 if m['adapter']=='fixture':return fixture(root,m,requests)
 from openai_codex import Codex,CodexConfig,ApprovalMode,Sandbox
 prompt=(root/m['brief']).read_text()+'\n\n'+(a.input or 'Write all required outputs.')
 if is_mvp(m):
  prompt+='''\nRunner MVP contract: Follow the brief within the allotted scope. You may finish early when complete or when no allowed actionable path remains. Do not repeat unchanged failures to fill time. Preserve useful partial outputs and explain blockers. Atomically replace outputs/checkpoint.json after meaningful results with {"format":"runner-checkpoint/v1","summary":"...","next_action":"...","blockers":[]}. Before ending, write outputs/handoff.md and atomically replace outputs/result.json with {"format":"runner-result/v1","outcome":"complete|partial|blocked","reason":"scope_complete|budget_exhausted|no_actionable_work|user_requested","summary":"...","next_action":"...","blockers":[]}. Complete requires scope_complete and all required outputs; blocked requires no_actionable_work and nonempty blockers. A runner finalize message requires stopping new substantive work and wrapping up partial outputs. Bound each tool invocation to fit the remaining time; use only bounded owned operations. Dependency containers must be managed by workload tooling with explicit ownership, deadlines and cleanup; never leave untracked detached or remote work. Check /requests/finalize.json between operations. Domain conclusions remain your claims, not runner-validated facts.\n'''
  prompt+=f'Absolute hard deadline (Unix UTC seconds): {a.deadline}. Finalization reserve: {m["policy"]["finalization_seconds"]} seconds. Termination begins {m["policy"]["termination_seconds"]} seconds before that deadline.\n'
 prompt=prompt.replace('/requests/finalize.json',str(requests/'finalize.json'))
 done=threading.Event();finalizing=threading.Event()
 # Native execution uses the operator's installed Codex and normal credential /
 # session store. Container execution has CODEX_HOME=/session and bundled CLI.
 codex_bin=os.environ.get('RESEARCH_RUNNER_CODEX_BIN') or (shutil.which('codex') if os.environ.get('CODEX_HOME')!='/session' else None)
 with Codex(CodexConfig(cwd=str(root),codex_bin=codex_bin)) as client:
  current_turn={'id':''}
  def approval(method,params):
   if method not in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):return {}
   if finalizing.is_set() or (requests/'finalize.json').exists():return {'decision':'decline'}
   rid=uuid.uuid4().hex
   req={'id':rid,'attempt':a.attempt,'turn_id':(params or {}).get('turnId') or current_turn['id'],'method':method,'details':params or {}}
   if not approval_should_wait(req):
    emit('progress',{'message':'approval declined; the request does not fit in one event','id':rid})
    return {'decision':'decline'}
   emit('approval_request',req);response=requests/(rid+'.json')
   while not done.is_set():
    if (requests/'finalize.json').exists():return {'decision':'decline'}
    if response.exists():
     d=json.loads(response.read_text());response.unlink();return {'decision':d['decision']}
    time.sleep(.1)
   return {'decision':'decline'}
  # The pinned SDK currently exposes approval dispatch on its client.
  client._client._approval_handler=approval
  thread=client.thread_resume(a.thread,cwd=str(root),approval_mode=ApprovalMode.auto_review,sandbox=Sandbox.workspace_write) if a.thread else client.thread_start(cwd=str(root),approval_mode=ApprovalMode.auto_review,sandbox=Sandbox.workspace_write)
  emit('thread',{'id':thread.id})
  initial=requests/'finalize.json'
  if initial.exists():prompt+='\nFinalize immediately: '+initial.read_text();finalizing.set()
  turn=thread.turn(prompt,approval_mode=ApprovalMode.auto_review,sandbox=Sandbox.workspace_write)
  current_turn['id']=turn.id;emit('turn',{'id':turn.id})
  def controls():
   while not done.wait(.1):
    try:
     request=requests/'finalize.json'
     if request.exists() and not finalizing.is_set():
      control=json.loads(request.read_text())
      turn.steer('RUNNER FINALIZE: Stop substantive work now. Preserve partial outputs, record unresolved items, and write result.json/handoff.md before termination. Reason: '+control['reason'])
      finalizing.set();emit('finalize_acknowledged',control)
     if finalizing.is_set():continue
     for f in sorted(pathlib.Path(a.input_dir).glob('*.json')):
      text=json.loads(f.read_text())['input'];turn.steer(text);f.unlink()
      emit('progress',{'message':'User input sent to active turn'})
    except Exception as e:
     emit('error',{'message':'control delivery failed: '+str(e)})
     # Failed delivery is not acknowledged or claimed as graceful shutdown.
     return
  control_thread=threading.Thread(target=controls,daemon=True);control_thread.start()
  try:
   for event in turn.stream():
    data=event.payload.model_dump(mode='json',exclude_none=True) if hasattr(event.payload,'model_dump') else getattr(event.payload,'params',{})
    emit('codex',{'method':event.method,'payload':data})
    if event.method=='turn/completed' and data.get('turn',{}).get('status')!='completed':
     raise RuntimeError('Codex turn ended: '+str(data.get('turn',{}).get('status')))
  finally:done.set()


if __name__=='__main__':
 try:main()
 except BaseException as e:
  emit('error',{'message':str(e),'type':type(e).__name__});sys.exit(1)
