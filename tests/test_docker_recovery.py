"""Run with RR_DOCKER_TEST=1 after building the pinned local image."""
import json,os,pathlib,signal,subprocess,sys,time
import pytest
from runner.service import Service

@pytest.mark.skipif(os.environ.get('RR_DOCKER_TEST')!='1',reason='requires Docker and pinned local image')
def test_controller_death_restarts_running_container(tmp_path):
 from runner.package import assemble
 root=tmp_path/'state';archive=tmp_path/'sample.rrpkg'
 assemble(pathlib.Path(__file__).resolve().parents[1]/'sample',archive)
 code='''from runner.service import Service
import sys,time
s=Service(sys.argv[1],engine="docker")
j,_=s.submit(sys.argv[2],"death-submit")
s.launch(j,"death-launch")
print(j,flush=True)
time.sleep(60)
'''
 env=os.environ.copy();env['RR_FIXTURE_DELAY']='30'
 controller=subprocess.Popen([sys.executable,'-u','-c',code,str(root),str(archive)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 try:
  jid=controller.stdout.readline().strip();assert len(jid)==32,controller.stderr.read()
  for _ in range(100):
   q=subprocess.run(['docker','ps','-q','--filter','label=research-runner.root='+str(root.resolve())],capture_output=True,text=True)
   if q.stdout.strip():break
   time.sleep(.1)
  assert q.stdout.strip(),'container never started'
  controller.kill();controller.wait(timeout=10)
  s=Service(root,engine='docker')
  assert s.store.get(jid)['state']=='interrupted'
  q=subprocess.run(['docker','ps','-q','--filter','label=research-runner.root='+str(root.resolve())],capture_output=True,text=True)
  assert not q.stdout.strip(),'orphan container survived reconciliation'
  with pytest.raises(ValueError):s.store.append(jid,1,'stale','progress',{})
  s.launch(jid,'resume-after-death')
  for _ in range(100):
   if s.store.get(jid)['state']!='running':break
   time.sleep(.2)
  assert s.store.get(jid)['state']=='completed',s.store.get(jid)['error']
  assert s.store.get(jid)['attempt']==2
 finally:
  if controller.poll() is None:controller.kill();controller.wait(timeout=10)
