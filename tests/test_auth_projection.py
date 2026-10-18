"""Explicit auth projection must never become a workload-selected credential."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from runner.package import assemble
from runner.service import Service


def test_auth_file_is_opt_in_and_mounted_read_only_outside_workspace(tmp_path,monkeypatch):
 auth=tmp_path/'private/auth.json';auth.parent.mkdir();auth.write_text('{"dummy":"not-a-real-credential"}')
 monkeypatch.setenv('RESEARCH_RUNNER_CODEX_AUTH_FILE',str(auth))
 source=tmp_path/'source';shutil.copytree(Path(__file__).resolve().parents[1]/'sample-mvp',source)
 manifest=json.loads((source/'manifest.json').read_text());manifest['adapter']='codex';manifest['runtime'].update(image='test-image',image_id='sha256:'+'0'*64)
 (source/'manifest.json').write_text(json.dumps(manifest));archive=tmp_path/'job.rrpkg';assemble(source,archive)
 service=Service(tmp_path/'state','host');service.auth_file=auth.resolve();jid,_=service.submit(archive,'s');service.engine='docker'
 def inspect(cmd,**kwargs):
  if cmd[:3]==['docker','image','inspect']:return SimpleNamespace(returncode=0,stdout=manifest['runtime']['image_id'])
  return SimpleNamespace(returncode=0,stdout='["name=rootless"]')
 commands=[]
 def launch(cmd,**kwargs):
  commands.append(cmd);raise OSError('test stops before launching')
 monkeypatch.setattr('runner.service.subprocess.run',inspect)
 monkeypatch.setattr('runner.service.subprocess.Popen',launch)
 with pytest.raises(OSError):service.launch(jid,'r')
 assert str(auth)+':/session/auth.json:ro' in commands[0]
 assert not list((service.path(jid)/'work').rglob('auth.json'))
 assert not (service.path(jid)/'session/auth.json').exists()
 assert auth.read_text()=='{"dummy":"not-a-real-credential"}'


def test_auth_projection_rejects_nonfile(tmp_path,monkeypatch):
 monkeypatch.setenv('RESEARCH_RUNNER_CODEX_AUTH_FILE',str(tmp_path))
 with pytest.raises(ValueError,match='regular host file'):Service(tmp_path/'state','docker')


def test_docker_reconciliation_removes_stale_runtime_markers_only_after_cleanup(tmp_path,monkeypatch):
 root=tmp_path/'state';marker=root/'jobs'/('a'*32)/'runtime.json';marker.parent.mkdir(parents=True)
 marker.write_text('{"pid":99999999,"attempt":1}')
 service=Service.__new__(Service);service.root=root;service.engine='docker'
 monkeypatch.setattr('runner.service.subprocess.run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='container-id\n',stderr=''))
 def refused(cid):raise RuntimeError('container cleanup unconfirmed')
 monkeypatch.setattr(service,'_remove_container',refused)
 with pytest.raises(RuntimeError):service._kill_orphans()
 assert marker.exists()
 cleaned=[];monkeypatch.setattr(service,'_remove_container',lambda cid:cleaned.append(cid))
 service._kill_orphans()
 assert cleaned==['container-id'] and not marker.exists()
