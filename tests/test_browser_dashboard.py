"""Real Chromium dashboard flow; run with RR_BROWSER_TEST=1."""
import os,pathlib,socket,subprocess,sys,time,uuid
import pytest

@pytest.mark.skipif(os.environ.get('RR_BROWSER_TEST')!='1',reason='requires local Chromium')
@pytest.mark.parametrize('hostname',['127.0.0.1','runner.test'])
def test_dashboard_submit_reconnect_download_and_states(tmp_path,hostname):
 from playwright.sync_api import sync_playwright
 from runner.package import assemble
 archive=tmp_path/'sample.rrpkg';assemble(pathlib.Path(__file__).resolve().parents[1]/'sample',archive)
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 env=os.environ.copy();root=tmp_path/'state'
 server=subprocess.Popen([sys.executable,'-m','runner.cli','--state',str(root),'--engine','host','serve','--host','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
 try:
  import urllib.request
  for _ in range(100):
   try:
    urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=.2).read();break
   except Exception:time.sleep(.1)
  else:raise AssertionError(server.stderr.read())
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('RR_BROWSER_EXECUTABLE','/usr/bin/chromium'),args=['--no-sandbox','--no-proxy-server','--host-resolver-rules=MAP runner.test 127.0.0.1'])
   page=browser.new_page(accept_downloads=True)
   errors=[];keys=[]
   page.on('pageerror',lambda error:errors.append(str(error)))
   page.on('request',lambda request:keys.append(request.headers['idempotency-key']) if 'idempotency-key' in request.headers else None)
   page.goto(f'http://{hostname}:{port}/')
   if hostname=='runner.test':
    assert page.evaluate('window.isSecureContext') is False
    assert page.evaluate('typeof crypto.randomUUID')=='undefined'
   page.set_input_files('#pkg',str(archive))
   page.get_by_role('button',name='Submit').click()
   page.get_by_role('button',name='Start/resume').click()
   page.get_by_role('heading',name='blue-sky-sample · completed').wait_for(timeout=15000)
   assert page.get_by_role('button',name='Start/resume').is_disabled()
   assert page.get_by_role('button',name='Stop').is_disabled()
   assert page.get_by_role('button',name='Send input').is_disabled()
   assert 'Reading brief' in page.locator('#events').inner_text()
   assert 'Air molecules scatter' in page.locator('#findings').inner_text()
   with page.expect_download() as result:page.get_by_role('link',name='outputs/report.md',exact=False).click()
   assert 'report.md' in result.value.suggested_filename
   page.reload()
   page.locator('.row').first.click()
   page.get_by_role('heading',name='blue-sky-sample · completed').wait_for()
   page.get_by_role('link',name='outputs/report.md',exact=False).wait_for()
   assert 'Reading brief' in page.locator('#events').inner_text()
   assert len(keys)==2 and len(set(keys))==2
   assert all(uuid.UUID(key).version==4 for key in keys)
   # Failed submissions must show a useful error instead of rejecting unhandled.
   page.route('**/api/jobs',lambda route:route.fulfill(status=400,json={'detail':'Sample submission rejected'}) if route.request.method=='POST' else route.continue_())
   page.set_input_files('#pkg',str(archive))
   messages=[]
   def dismiss(dialog):
    messages.append(dialog.message);dialog.accept()
   page.on('dialog',dismiss)
   with page.expect_event('dialog'):
    page.get_by_role('button',name='Submit').click()
   assert messages==['Sample submission rejected']
   assert not errors
   browser.close()
 finally:
  server.terminate()
  try:server.wait(timeout=5)
  except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)


@pytest.mark.skipif(os.environ.get('RR_BROWSER_TEST')!='1',reason='requires local Chromium')
def test_mvp_dashboard_signin_finalize_and_published_partial(tmp_path):
 from playwright.sync_api import sync_playwright
 from test_http_client import server as server_fixture
 from test_mvp import package
 # Reuse the same real authenticated server fixture setup without a second port helper.
 fixture=server_fixture.__wrapped__(tmp_path)
 control,reader=next(fixture)
 try:
  archive=package(tmp_path,seconds=20,budget=10)
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('RR_BROWSER_EXECUTABLE','/usr/bin/chromium'),args=['--no-sandbox'])
   page=browser.new_page(accept_downloads=True);errors=[]
   page.on('pageerror',lambda error:errors.append(str(error)))
   page.goto(control.url)
   page.locator('#token').fill('test-control');page.get_by_role('button',name='Sign in').click()
   page.set_input_files('#pkg',str(archive));page.get_by_role('button',name='Submit',exact=True).click()
   page.get_by_role('button',name='Start/resume').click()
   page.get_by_role('link',name='outputs/partial.txt',exact=False).wait_for(timeout=10000)
   page.get_by_role('button',name='Finish up').click()
   page.get_by_role('heading',name='generic-lifecycle-fixture · finished').wait_for(timeout=10000)
   assert 'Outcome: partial' in page.locator('#status').inner_text()
   assert page.get_by_role('button',name='Finish up').is_disabled()
   assert page.get_by_role('button',name='Stop',exact=True).is_disabled()
   with page.expect_download() as download:page.get_by_role('link',name='outputs/handoff.md',exact=False).click()
   assert download.value.suggested_filename=='handoff.md'
   page.reload();page.locator('.row').first.click()
   page.get_by_role('heading',name='generic-lifecycle-fixture · finished').wait_for()
   assert 'Outcome: partial' in page.locator('#status').inner_text()
   assert not errors
   browser.close()
 finally:
  try:next(fixture)
  except StopIteration:pass
