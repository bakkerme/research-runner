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
   page.locator('#submit-menu summary').click()
   page.set_input_files('#pkg',str(archive))
   page.get_by_role('button',name='Submit').click()
   page.locator('#action-menu summary').click()
   page.get_by_role('button',name='Start/resume').click()
   page.get_by_role('heading',name='blue-sky-sample · completed').wait_for(timeout=15000)
   assert page.get_by_role('button',name='Start/resume',include_hidden=True).is_disabled()
   assert page.get_by_role('button',name='Stop',include_hidden=True).is_disabled()
   assert page.get_by_role('button',name='Send input',include_hidden=True).is_disabled()
   assert 'Reading brief' in page.locator('#events').inner_text()
   assert 'Air molecules scatter' in page.locator('#findings').inner_text()
   page.get_by_role('tab',name='Files',exact=False).click()
   with page.expect_download() as result:page.get_by_role('link',name='Download outputs/report.md',exact=False).click()
   assert 'report.md' in result.value.suggested_filename
   page.reload()
   page.locator('.row').first.click()
   page.get_by_role('heading',name='blue-sky-sample · completed').wait_for()
   page.get_by_role('tab',name='Files',exact=False).click()
   page.get_by_role('link',name='Download outputs/report.md',exact=False).wait_for()
   assert 'Reading brief' in page.locator('#events').inner_text()
   assert len(keys)==2 and len(set(keys))==2
   assert all(uuid.UUID(key).version==4 for key in keys)
   # Failed submissions must show a useful error instead of rejecting unhandled.
   page.route('**/api/jobs',lambda route:route.fulfill(status=400,json={'detail':'Sample submission rejected'}) if route.request.method=='POST' else route.continue_())
   page.locator('#submit-menu summary').click()
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
   page.locator('#session-menu summary').click();page.locator('#token').fill('test-control');page.get_by_role('button',name='Sign in').click()
   page.locator('#submit-menu summary').click();page.set_input_files('#pkg',str(archive));page.get_by_role('button',name='Submit',exact=True).click()
   page.locator('#action-menu summary').click()
   page.get_by_role('button',name='Start/resume').click()
   page.get_by_role('tab',name='Files',exact=False).click()
   page.get_by_role('link',name='Download outputs/partial.txt',exact=False).wait_for(timeout=10000)
   page.locator('#action-menu summary').click()
   page.get_by_role('button',name='Finish up').click()
   page.get_by_role('heading',name='generic-lifecycle-fixture · finished').wait_for(timeout=10000)
   assert 'Outcome: partial' in page.locator('#status').inner_text()
   assert page.get_by_role('button',name='Finish up',include_hidden=True).is_disabled()
   assert page.get_by_role('button',name='Stop',exact=True,include_hidden=True).is_disabled()
   page.get_by_role('tab',name='Files',exact=False).click()
   with page.expect_download() as download:page.get_by_role('link',name='Download outputs/handoff.md',exact=False).click()
   assert download.value.suggested_filename=='handoff.md'
   page.reload();page.locator('.row').first.click()
   page.get_by_role('heading',name='generic-lifecycle-fixture · finished').wait_for()
   assert 'Outcome: partial' in page.locator('#status').inner_text()
   assert not errors
   browser.close()
 finally:
  try:next(fixture)
  except StopIteration:pass


@pytest.mark.skipif(os.environ.get('RR_BROWSER_TEST')!='1',reason='requires local Chromium')
def test_completed_large_history_opens_without_replay_and_stays_bounded(tmp_path):
 import json
 from playwright.sync_api import sync_playwright
 from test_http_client import server as server_fixture
 from runner.package import assemble
 from runner.store import Store
 fixture=server_fixture.__wrapped__(tmp_path);control,_=next(fixture)
 try:
  archive=tmp_path/'history.rrpkg';assemble(pathlib.Path(__file__).resolve().parents[1]/'sample-mvp',archive)
  jid=control.submit(archive,'history',tmp_path/'receipt.json',True)['id']
  control.control(jid,'start','history-start',True)
  for _ in range(100):
   status=control.request(f'/api/jobs/{jid}/status')
   if status['state']=='finished':break
   time.sleep(.025)
  assert status['state']=='finished'
  store=Store(tmp_path/'state/state.sqlite3')
  publication=status['publication'];original_files=publication['files']
  publication['files']=[dict(original_files[0],name=f'outputs/final/file-{i}.txt') for i in range(387)]+original_files
  with store.db() as c:
   c.execute('BEGIN IMMEDIATE')
   for i in range(12326):store.event(c,jid,1,'progress',{'n':i,'text':'x'*2000})
   c.execute('UPDATE publications SET manifest=? WHERE job_id=? AND generation=?',(json.dumps(publication),jid,publication['generation']))
   c.execute('COMMIT')
  last=store.events(jid,limit=1,newest_first=True)[0]['seq']
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('RR_BROWSER_EXECUTABLE','/usr/bin/chromium'),args=['--no-sandbox'])
   page=browser.new_page(accept_downloads=True);errors=[];streams=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('request',lambda r:streams.append(r.url) if '/events?' in r.url else None)
   page.goto(control.url);page.locator('#session-menu summary').click();page.locator('#token').fill('test-control');page.get_by_role('button',name='Sign in').click()
   page.locator('.row').first.click()
   page.get_by_role('heading',name='generic-lifecycle-fixture · finished').wait_for(timeout=10000)
   page.wait_for_function('document.querySelector("#events").textContent.length>0')
   assert page.locator('#artifacts a').count()==len(publication['files'])
   assert page.evaluate('logRows.length')==100
   assert len(page.locator('#events').text_content())<110000
   assert streams and streams[0].endswith(f'after={last}')
   page.get_by_role('tab',name='Activity',exact=True).click()
   page.get_by_role('button',name='Older events').click()
   page.wait_for_function('viewingHistory===true')
   assert page.evaluate('logRows[logRows.length-1].seq')==last-100
   page.get_by_role('button',name='Latest events',exact=True).click()
   page.wait_for_function('viewingHistory===false')
   assert page.evaluate('seen')==last
   # Live messages retain the same DOM/log bounds.
   with store.db() as c:
    c.execute('BEGIN IMMEDIATE')
    for i in range(300):store.event(c,jid,1,'progress',{'n':i,'text':'x'*2000})
    c.execute('COMMIT')
   page.wait_for_function(f'seen==={last+300}',timeout=10000)
   assert page.evaluate('logRows.length')==100
   assert len(page.locator('#events').text_content())<110000
   page.get_by_role('tab',name='Files',exact=False).click()
   with page.expect_download() as download:page.get_by_role('link',name='Download outputs/handoff.md',exact=False).click()
   assert download.value.suggested_filename=='handoff.md'
   assert not errors
   browser.close()
 finally:
  try:next(fixture)
  except StopIteration:pass


@pytest.mark.skipif(os.environ.get('RR_BROWSER_TEST')!='1',reason='requires local Chromium')
def test_compact_reader_files_themes_and_mobile():
 """Verify review density, long output, file navigation, and small-screen reading."""
 from playwright.sync_api import sync_playwright, expect
 from runner.dashboard import PAGE
 job={'id':'a'*32,'name':'memcard-summary-SLUS-00439','state':'finished'}
 body='<img src=x onerror="window.injected=true">\n'+'Evidence from the save file. '*80+'END OF FULL MESSAGE'
 events=[{'seq':i,'attempt':1,'ts':1792300000+i,'kind':'codex','data':{'method':'item/completed','payload':{'item':{'type':'commandExecution','command':f'python inspect.py --case {i}','aggregated_output':body}}}} for i in range(1,101)]
 files=[{'name':'outputs/handoff.md','size':4404,'sha256':'a'*64}]+[{'name':f'outputs/research/native-evidence/case-{i:03}.json','size':1200,'sha256':'b'*64} for i in range(386)]
 status=dict(job,outcome='complete',remaining_seconds=0,validation='not_run',finalize_reason=None,result={'summary':'Completed the scoped save summary. '*20,'next_action':'Review the handoff.'},publication={'generation':43,'files':files},pending_approvals=[])
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('RR_BROWSER_EXECUTABLE','/usr/bin/chromium'),args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1280,'height':720},color_scheme='light')
  errors=[];requests=[];page.on('pageerror',lambda error:errors.append(str(error)))
  def respond(route):
   path=route.request.url.split('runner.test',1)[1];requests.append(path)
   if path=='/':route.fulfill(content_type='text/html',body=PAGE)
   elif path=='/api/jobs':route.fulfill(json=[job])
   elif path.endswith('/status'):route.fulfill(json=status)
   elif '/event-log?' in path:route.fulfill(json={'events':events,'next_cursor':100,'previous_cursor':1,'has_previous':False})
   elif '/events?' in path:route.fulfill(content_type='text/event-stream',body=': connected\n\n')
   elif '/artifacts/' in path:route.fulfill(body='# Research handoff\nFull file contents for review.',content_type='text/plain')
   else:route.fulfill(status=404)
  page.route('**/*',respond);page.goto('http://runner.test/');page.locator('.row').click()
  expect(page.locator('.event')).to_have_count(100)
  assert any('activity=true' in path for path in requests)
  assert page.locator('#events').bounding_box()['height']>=720*.70
  assert page.locator('.event').first.bounding_box()['height']<=33
  assert page.locator('.event').first.bounding_box()['y']<200
  expect(page.locator('#event-reader .reader-text')).to_contain_text('END OF FULL MESSAGE')
  assert page.locator('#event-reader img').count()==0 and page.evaluate('window.injected') is None
  assert page.locator('#event-reader details.raw pre').count()==0
  page.locator('#event-reader details.raw summary').click()
  expect(page.locator('#event-reader details.raw pre')).to_contain_text('aggregated_output')
  page.locator('#log-search').fill('--case 99')
  expect(page.locator('.event')).to_have_count(1)
  page.locator('#log-search').fill('no match');expect(page.locator('#events')).to_contain_text('No matching')
  page.locator('#log-search').fill('');page.locator('#log-filter').select_option('issue')
  expect(page.locator('.event')).to_have_count(0)
  page.locator('#log-filter').select_option('all')
  # Browser retains a readable selection as live events arrive.
  page.locator('.event').nth(2).click()
  page.evaluate('appendLog({seq:101,attempt:1,kind:"progress",data:{message:"New evidence"}})')
  expect(page.locator('.event.selected')).to_contain_text('--case 3')
  page.locator('#follow').check()
  page.evaluate('appendLog({seq:102,attempt:1,kind:"progress",data:{message:"Latest evidence"}})')
  expect(page.locator('#event-reader .reader-text')).to_have_text('Latest evidence')
  page.locator('#events').evaluate('(el)=>el.scrollTop=0');expect(page.locator('#follow')).not_to_be_checked()
  page.get_by_role('tab',name='Files',exact=False).click()
  expect(page.locator('#artifacts a')).to_have_count(387)
  page.locator('#file-search').fill('handoff');expect(page.locator('.file-row')).to_have_count(1)
  page.get_by_role('button',name='outputs/handoff.md',exact=True).click()
  expect(page.locator('#file-reader pre')).to_contain_text('Full file contents')
  page.locator('#file-search').fill('case-385');expect(page.locator('.file-row')).to_have_count(1)
  page.get_by_role('tab',name='Summary',exact=True).click()
  expect(page.locator('#result')).to_contain_text('Review the handoff.')
  page.get_by_role('tab',name='Activity',exact=True).click()
  light=page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundColor')
  page.locator('#theme').select_option('dark');dark=page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundColor');assert dark!=light
  page.reload();expect(page.locator('#theme')).to_have_value('dark');page.locator('.row').click()
  page.locator('#theme').select_option('system');page.emulate_media(color_scheme='dark')
  assert page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundColor')==dark
  page.emulate_media(color_scheme='light')
  assert page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundColor')==light
  width=page.locator('.main').bounding_box()['width'];page.locator('#toggle-jobs').click()
  assert page.locator('.main').bounding_box()['width']>width+150
  # Narrow layouts switch between the dense list and full-width reader.
  for width in [768,390,320]:
   page.set_viewport_size({'width':width,'height':720})
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
   assert page.evaluate('document.documentElement.scrollHeight<=innerHeight'),width
  page.locator('.event').first.click();expect(page.locator('#event-reader')).to_be_visible()
  expect(page.locator('#event-reader .reader-text')).to_contain_text('END OF FULL MESSAGE')
  page.get_by_role('button',name='← Events').click();expect(page.locator('#events')).to_be_visible()
  # Streaming chunks occupy one row while retaining full raw source events.
  page.evaluate('''() => {logRows=[
   {seq:110,kind:'codex',data:{method:'item/agentMessage/delta',payload:{item_id:'m',turn_id:'t',delta:'The checksum '}}},
   {seq:111,kind:'codex',data:{method:'item/agentMessage/delta',payload:{item_id:'m',turn_id:'t',delta:'matches.'}}}
  ];flushLog();}''')
  expect(page.locator('.event')).to_have_count(1)
  page.locator('.event').click();expect(page.locator('#event-reader .reader-text')).to_have_text('The checksum matches.')
  assert not errors
  browser.close()


@pytest.mark.skipif(os.environ.get('RR_BROWSER_TEST')!='1',reason='requires local Chromium')
def test_asset_progress_shows_download_byte_counts(tmp_path):
 """Exercise the shipped renderer with a real persisted preparation event."""
 from playwright.sync_api import sync_playwright
 from test_http_client import server as server_fixture
 from runner.store import Store
 fixture=server_fixture.__wrapped__(tmp_path)
 control,reader=next(fixture)
 try:
  store=Store(tmp_path/'state/state.sqlite3')
  jid='d'*32
  store.submit(jid,'asset-download','fixture','asset-progress','0'*64)
  with store.db() as connection:
   store.event(connection,jid,1,'asset_progress',{'asset_type':'model','asset_id':'weights/v1','destination':'inputs/model','message':'Downloading model weights','bytes_completed':1048576,'bytes_total':8388608})
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('RR_BROWSER_EXECUTABLE','/usr/bin/chromium'),args=['--no-sandbox'])
   page=browser.new_page();errors=[]
   page.on('pageerror',lambda error:errors.append(str(error)))
   page.goto(control.url)
   page.locator('#session-menu summary').click();page.locator('#token').fill('test-read');page.get_by_role('button',name='Sign in').click()
   page.locator('.row').first.click()
   page.get_by_role('heading',name='asset-download · queued').wait_for()
   page.locator('#events .event').filter(has_text='Downloading model weights').wait_for()
   text=page.locator('#events').inner_text()
   assert 'weights/v1' in text and 'inputs/model' in text
   assert '1,048,576 / 8,388,608 bytes' in text
   assert not errors
   browser.close()
 finally:
  try:next(fixture)
  except StopIteration:pass
