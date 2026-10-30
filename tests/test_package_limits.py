"""Streaming input quotas and extraction failure cleanup."""
import io
import shutil
import tarfile
from pathlib import Path

import pytest

from runner import package


def test_input_file_and_total_quotas_include_envelope(tmp_path, monkeypatch):
 source=tmp_path/'payload'
 shutil.copytree(Path(__file__).resolve().parents[1]/'sample-mvp',source)
 archive=tmp_path/'payload.rrpkg'
 monkeypatch.setattr(package,'INPUT_MAX_FILE',1)
 with pytest.raises(ValueError,match='oversize input'):package.assemble(source,archive)
 monkeypatch.setattr(package,'INPUT_MAX_FILE',8*1024**3)
 monkeypatch.setattr(package,'INPUT_MAX_TOTAL',1)
 with pytest.raises(ValueError,match='oversize package'):package.assemble(source,archive)
 monkeypatch.setattr(package,'INPUT_MAX_TOTAL',64*1024**3)
 package.assemble(source,archive)
 _,files=package.verify(archive)
 monkeypatch.setattr(package,'INPUT_MAX_TOTAL',sum(f['size'] for f in files.values()))
 with pytest.raises(ValueError,match='package limits'):package.assemble(source,archive)


def test_extraction_cleans_failed_hash_and_rejects_oversize_header(tmp_path, monkeypatch):
 source=Path(__file__).resolve().parents[1]/'sample-mvp'
 original=tmp_path/'original.rrpkg';package.assemble(source,original)
 with tarfile.open(original) as t:
  files={x.name:t.extractfile(x).read() for x in t}
 files['brief.md']=b'tampered'
 bad=tmp_path/'bad.rrpkg'
 with tarfile.open(bad,'w:gz') as t:
  for name,data in files.items():
   item=tarfile.TarInfo(name);item.size=len(data);t.addfile(item,io.BytesIO(data))
 with pytest.raises(ValueError,match='hash mismatch'):package.deploy(bad,tmp_path/'extracted')
 assert not (tmp_path/'extracted').exists()
 monkeypatch.setattr(package,'INPUT_MAX_FILE',64)
 oversized=tmp_path/'oversized.rrpkg'
 with tarfile.open(oversized,'w:gz') as t:
  item=tarfile.TarInfo('disc.bin');item.size=65;t.addfile(item,io.BytesIO(b'x'*65))
 with pytest.raises(ValueError,match='unsafe archive member'):package.verify(oversized)


def test_archive_entry_cap_stays_at_512(tmp_path):
 archive=tmp_path/'many.rrpkg'
 with tarfile.open(archive,'w:gz') as t:
  for i in range(package.MAX_FILES+1):
   item=tarfile.TarInfo(f'f{i}.txt');item.size=1;t.addfile(item,io.BytesIO(b'x'))
 with pytest.raises(ValueError,match='too many entries'):package.verify(archive)
 assert package.MAX_FILES==512 and package.MAX_OUTPUT_FILES==4096 and package.MAX_OUTPUT_FILES>package.MAX_FILES


def test_http_stream_upload_limit_cleans_temporary_file(tmp_path, monkeypatch):
 from fastapi.testclient import TestClient
 from runner.web import app
 monkeypatch.setattr(package,'MAX_ARCHIVE',64)
 with TestClient(app(tmp_path/'state')) as client:
  response=client.post('/api/jobs',content=b'x'*65,headers={'Idempotency-Key':'over-limit'})
  assert response.status_code==413
 assert not list((tmp_path/'state').glob('*.rrpkg'))
 assert not list((tmp_path/'state').glob('jobs/*'))


def test_monitoring_stays_available_during_package_processing(tmp_path, monkeypatch):
 import threading
 from concurrent.futures import ThreadPoolExecutor
 from fastapi.testclient import TestClient
 from runner.web import app
 application=app(tmp_path/'state')
 entered=threading.Event();release=threading.Event()
 def slow_submit(*args):
  entered.set()
  if not release.wait(5):raise RuntimeError('test processing timed out')
  return 'a'*32,True
 monkeypatch.setattr(application.state.service,'submit',slow_submit)
 with TestClient(application) as client,ThreadPoolExecutor() as executor:
  future=executor.submit(client.post,'/api/jobs',content=b'input',headers={'Idempotency-Key':'slow'})
  try:
   assert entered.wait(2)
   assert client.get('/api/jobs').status_code==200
   assert client.get('/api/capabilities').status_code==200
  finally:release.set()
  assert future.result(timeout=2).status_code==200
