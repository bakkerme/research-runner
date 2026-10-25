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


def followup_prompt(status, failed, mvp, finalizing):
 """Instruction for one more turn on the same thread.

 MVP closeout files are not the v1 contract. A retried v1 turn must keep
 writing the manifest's required outputs.
 """
 lead=('RUNNER: the previous turn ended with status '+str(status)+'. ') if failed else 'RUNNER: '
 if mvp:
  task=('Write outputs/result.json and outputs/handoff.md now.' if finalizing
        else 'Continue the brief; result.json/handoff.md closeout is still required.')
 else:
  task='Continue the brief and write the manifest required outputs.'
 return lead+task


def attempt_time_left(deadline, time_seconds, now, started):
 """Seconds until this attempt's controller deadline.

 Non-MVP workers are not passed --deadline. Their budget is the manifest
 time limit measured from process start, which is when the controller's
 hard stop begins.
 """
 end=deadline if deadline else started+float(time_seconds)
 return end-now


def turn_followup(status, saw_completed, missing_closeout, retries_left, time_left, reserve=15):
 """Return accept, retry, or fail after one Codex turn stream ends.

 A stream that ends without turn/completed is not itself a failure. An
 explicit non-completed status still fails once retries or the time reserve
 run out, including non-MVP jobs and turns that already wrote closeout files.
 Missing MVP closeout is retried, then left for the controller to reject.
 """
 failed=saw_completed and status!='completed'
 if not failed and not missing_closeout:return 'accept'
 if retries_left>0 and time_left>=reserve:return 'retry'
 return 'fail' if failed else 'accept'


def inbox_text(path):
 try:text=json.loads(path.read_text())['input']
 except (ValueError,KeyError,OSError,TypeError) as error:raise ValueError(str(error)) from error
 if not isinstance(text,str):raise ValueError('input must be a string')
 return text


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
  holder={}
  def start_turn(text):
   t=thread.turn(text,approval_mode=ApprovalMode.auto_review,sandbox=Sandbox.workspace_write)
   holder['turn']=t;current_turn['id']=t.id;emit('turn',{'id':t.id});return t
  start_turn(prompt)
  def controls():
   reported=set()
   while not done.wait(.1):
    try:
     request=requests/'finalize.json'
     if request.exists() and not finalizing.is_set():
      control=json.loads(request.read_text())
      holder['turn'].steer('RUNNER FINALIZE: Stop substantive work now. Preserve partial outputs, record unresolved items, and write result.json/handoff.md before termination. Reason: '+control['reason'])
      finalizing.set();emit('finalize_acknowledged',control)
     if finalizing.is_set():continue
     for f in sorted(pathlib.Path(a.input_dir).glob('*.json')):
      try:text=inbox_text(f)
      except ValueError as bad:
       emit('error',{'message':'discarding unreadable input '+f.name+': '+str(bad),'fatal':False});f.unlink(missing_ok=True);continue
      holder['turn'].steer(text);f.unlink()
      emit('progress',{'message':'User input sent to active turn'})
    except Exception as e:
     # Transient steer failures (turn boundary, SDK hiccup) are retried next tick.
     message='control delivery failed: '+str(e)
     if message not in reported:reported.add(message);emit('error',{'message':message,'fatal':False})
  control_thread=threading.Thread(target=controls,daemon=True);control_thread.start()
  retries=int(os.environ.get('RESEARCH_RUNNER_TURN_RETRIES','2'))
  started_at=time.time()
  def closeout_missing():
   return is_mvp(m) and not ((root/'outputs/result.json').exists() and (root/'outputs/handoff.md').exists())
  try:
   while True:
    status=None;saw=False
    for event in holder['turn'].stream():
     data=event.payload.model_dump(mode='json',exclude_none=True) if hasattr(event.payload,'model_dump') else getattr(event.payload,'params',{})
     emit('codex',{'method':event.method,'payload':data})
     if event.method=='turn/completed':
      saw=True;turn=data.get('turn') if isinstance(data,dict) else None
      status=turn.get('status') if isinstance(turn,dict) else None
    missing=closeout_missing()
    time_left=attempt_time_left(a.deadline,m['resources']['time_seconds'],time.time(),started_at)
    action=turn_followup(status,saw,missing,retries,time_left)
    if action=='accept':break
    if action=='fail':raise RuntimeError('Codex turn ended: '+str(status))
    retries-=1
    failed=saw and status!='completed'
    emit('progress',{'message':'Continuing after turn status '+str(status)+('; closeout missing' if missing else '')})
    start_turn(followup_prompt(status,failed,is_mvp(m),finalizing.is_set() or (requests/'finalize.json').exists()))
  finally:done.set()

if __name__=='__main__':
 try:main()
 except BaseException as e:
  emit('error',{'message':str(e),'type':type(e).__name__,'fatal':True});sys.exit(1)
