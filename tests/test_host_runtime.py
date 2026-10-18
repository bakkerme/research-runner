"""The agent runs natively by default; tool containers are independent."""
from pathlib import Path
import sys

import pytest

from runner.package import assemble, verify
from runner.service import Service
from runner.store import Store
from runner.web import app
from fastapi.testclient import TestClient


def test_native_default_needs_no_docker_image_or_auth_projection(tmp_path,monkeypatch):
 # A stale container-auth setting is irrelevant to native execution.
 monkeypatch.setenv('RESEARCH_RUNNER_CODEX_AUTH_FILE','/does/not/exist')
 source=Path(__file__).resolve().parents[1]/'sample-mvp'
 archive=tmp_path/'host.rrpkg';assemble(source,archive)
 manifest,_=verify(archive)
 assert manifest['runtime']=={'sdk':'openai-codex==0.159.0'}
 service=Service(tmp_path/'state')
 assert service.engine=='host' and service.auth_file is None
 jid,_=service.submit(archive,'s');commands=[]
 monkeypatch.setenv('RESEARCH_RUNNER_CONTROL_TOKEN','must-not-reach-worker')
 monkeypatch.setenv('RESEARCH_RUNNER_READ_TOKEN','also-private')
 monkeypatch.setenv('RESEARCH_RUNNER_CODEX_BIN','/operator/installed/codex')
 def start(cmd,**kwargs):
  commands.append(cmd)
  assert cmd[0]==sys.executable and 'docker' not in cmd
  assert not {'RESEARCH_RUNNER_READ_TOKEN','RESEARCH_RUNNER_CONTROL_TOKEN','RESEARCH_RUNNER_CODEX_AUTH_FILE'}&kwargs['env'].keys()
  assert kwargs['env']['RESEARCH_RUNNER_CODEX_BIN']=='/operator/installed/codex'
  raise OSError('stop before execution')
 monkeypatch.setattr('runner.service.subprocess.Popen',start)
 with pytest.raises(OSError):service.launch(jid,'r')
 assert commands and service.status(jid)['execution_engine']=='host'
 assert service.status(jid)['session_store']=='host_codex_home'


def test_resumption_cannot_silently_switch_native_session_into_container(tmp_path):
 store=Store(tmp_path/'db');jid='a'*32
 store.submit(jid,'generic','codex','s','0'*64)
 a,t,_,_=store.start_attempt(jid,'host-start',engine='host')
 store.finish(jid,a,t,'interrupted')
 with pytest.raises(ValueError,match='cannot change'):store.start_attempt(jid,'docker-start',engine='docker')
 assert store.get(jid)['execution_engine']=='host'


def test_capabilities_identify_native_execution_without_container_limit_claims(tmp_path):
 with TestClient(app(tmp_path/'state')) as client:
  caps=client.get('/api/capabilities').json()
  assert caps['engine']=='host' and caps['default_engine']=='host'
  assert caps['resource_enforcement']=='deadline_and_owned_processes'
