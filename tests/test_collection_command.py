"""Collection-command discovery on secure and plain-HTTP dashboards."""
import os
import shlex

import pytest


@pytest.mark.skipif(os.environ.get('RR_BROWSER_TEST')!='1',reason='requires local Chromium')
@pytest.mark.parametrize('origin,clipboard',[
 ('https://runner.test','modern'),
 ('http://runner.test:8765','legacy'),
 ('https://runner.test','denied'),
])
def test_copy_collection_command_pins_selected_publication(origin,clipboard):
 from playwright.sync_api import sync_playwright,expect
 from runner.dashboard import PAGE
 jid='a'*32;queued='b'*32
 jobs=[{'id':jid,'name':'Research result','state':'finished'},{'id':queued,'name':'Queued research','state':'queued'}]
 publication={'generation':43,'files':[{'name':'outputs/handoff.md','size':10,'sha256':'a'*64}]}
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('RR_BROWSER_EXECUTABLE','/usr/bin/chromium'),args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1280,'height':720});errors=[];methods=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  if clipboard=='modern':
   page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copied=text;}}});")
  elif clipboard=='legacy':
   page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:undefined});document.execCommand=command=>{window.copied=document.activeElement.value;return command==='copy';};")
  else:
   page.add_init_script("Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw Error('Denied');}}});document.execCommand=()=>false;")
  def respond(route):
   path=route.request.url.removeprefix(origin);methods.append(route.request.method)
   if path=='/':route.fulfill(content_type='text/html',body=PAGE)
   elif path=='/api/jobs':route.fulfill(json=jobs)
   elif path.endswith('/status'):
    job=jobs[1] if queued in path else jobs[0]
    route.fulfill(json=dict(job,outcome='complete' if job['id']==jid else None,remaining_seconds=None,validation='not_run',finalize_reason=None,result=None,publication=publication if job['id']==jid else None,pending_approvals=[]))
   elif '/event-log?' in path:route.fulfill(json={'events':[],'next_cursor':0,'previous_cursor':None,'has_previous':False})
   elif '/events?' in path:route.fulfill(content_type='text/event-stream',body=': connected\n\n')
   else:route.fulfill(status=404)
  page.route('**/*',respond);page.goto(origin+'/');page.locator('.row').first.click()
  page.locator('#action-menu summary').click();button=page.get_by_role('button',name='Copy collection command')
  expect(button).to_be_enabled();button.click()
  expected=['research-runner','--url',origin,'collect',jid,f'runs/review-{jid}-g43','--generation','43','--apply']
  if clipboard=='denied':
   expect(page.locator('#collection-feedback')).to_have_text('Select and copy the command below.')
   field=page.get_by_role('textbox',name='Collection command');expect(field).to_be_visible()
   command=field.input_value();assert field.evaluate('(el)=>el.selectionEnd-el.selectionStart')==len(command)
  else:
   expect(page.locator('#collection-feedback')).to_have_text('Collection command copied.')
   command=page.evaluate('window.copied');expect(page.locator('#collection-command')).to_be_hidden()
  assert shlex.split(command)==expected
  # A new publication changes the copied generation and suggested review directory.
  publication['generation']=44;page.evaluate('refresh()')
  expect(page.locator('#collection-feedback')).to_have_text('')
  assert shlex.split(page.evaluate('collectionCommand()'))[-3:]==['--generation','44','--apply']
  # Shell quoting preserves literals, including quotes and shell substitutions.
  value="a'b $(touch /tmp/should-not-run)"
  assert shlex.split(page.evaluate('(value)=>shellQuote(value)',value))==[value]
  page.set_viewport_size({'width':320,'height':720})
  if clipboard=='denied':button.click();expect(page.locator('#collection-command')).to_be_visible()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  # Switching to an unpublished job clears the old command immediately.
  page.evaluate(f"openJob('{queued}')")
  expect(button).to_be_disabled();expect(page.locator('#collection-command')).to_be_hidden()
  assert page.evaluate('collectionCommand()')==''
  assert set(methods)=={'GET'} and not errors
  browser.close()
