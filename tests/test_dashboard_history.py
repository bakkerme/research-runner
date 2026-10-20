"""Large-history inspection and controller ownership regressions."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runner.package import assemble
from runner.service import Service,StateLockedError
from runner.web import app


def test_tail_and_older_pages_preserve_forward_replay(tmp_path):
 application=app(tmp_path/'state');store=application.state.service.store
 jid,_=store.submit('a'*32,'history','fixture','s','0'*64)
 other,_=store.submit('b'*32,'other','fixture','t','1'*64)
 with store.db() as c:
  for i in range(25):
   store.event(c,jid,0,'progress',{'n':i})
   store.event(c,other,0,'progress',{'n':i})
 expected=store.events(jid)
 with TestClient(application) as client:
  forward=client.get(f'/api/jobs/{jid}/event-log?limit=5').json()
  assert forward['events']==expected[:5] and forward['has_more']
  tail=client.get(f'/api/jobs/{jid}/event-log?tail=true&limit=5').json()
  assert tail['events']==expected[-5:] and tail['has_previous'] and not tail['has_more']
  accumulated=tail['events']
  while tail['has_previous']:
   tail=client.get(f'/api/jobs/{jid}/event-log?before={tail["previous_cursor"]}&limit=5').json()
   accumulated=tail['events']+accumulated
  assert accumulated==expected
  assert client.get(f'/api/jobs/{jid}/event-log?before=0').status_code==422


def test_state_lock_identifies_owner_and_releases_without_removing_file(tmp_path):
 state=tmp_path/'state';service=Service(state);url='http://127.0.0.1:8765'
 service.set_dashboard_url(url)
 inode=(state/'service.lock').stat().st_ino
 with pytest.raises(StateLockedError) as error:Service(state)
 assert str(state) in str(error.value) and str(os.getpid()) in str(error.value)
 assert url in str(error.value) and 'HTTP API' in str(error.value)
 assert json.loads((state/'service.lock').read_text())['pid']==os.getpid()
 service._owner.close()
 replacement=Service(state)
 assert (state/'service.lock').stat().st_ino==inode
 replacement._owner.close()


def test_pending_approval_survives_history_window(tmp_path):
 service=Service(tmp_path/'state');store=service.store
 jid,_=store.submit('a'*32,'approvals','fixture','s','0'*64)
 attempt,token,_,_=store.start_attempt(jid)
 request={'id':'request','turn_id':'turn','attempt':attempt,'method':'exec','details':{'command':'inspect'}}
 store.request_approval(jid,attempt,token,request)
 with store.db() as c:
  for i in range(200):store.event(c,jid,attempt,'progress',{'n':i})
 assert service.status(jid)['pending_approvals']==[request]


def test_activity_pages_skip_file_publication_flood_without_changing_raw_history(tmp_path):
 application=app(tmp_path/'state');store=application.state.service.store
 jid,_=store.submit('c'*32,'large review','fixture','s','0'*64)
 with store.db() as c:
  store.event(c,jid,1,'progress',{'message':'Useful command result'})
  for i in range(387):store.event(c,jid,1,'artifact',{'name':f'outputs/evidence/{i}.json'})
  store.event(c,jid,1,'finished',{'result':{'summary':'Review complete'}})
 expected=[e for e in store.events(jid) if e['kind']!='artifact']
 with TestClient(application) as client:
  raw=client.get(f'/api/jobs/{jid}/event-log?tail=true&limit=100').json()
  assert sum(e['kind']=='artifact' for e in raw['events'])==99
  page=client.get(f'/api/jobs/{jid}/event-log?tail=true&limit=2&activity=true').json()
  assert page['events']==expected[-2:] and page['has_previous']
  older=client.get(f'/api/jobs/{jid}/event-log?before={page["previous_cursor"]}&limit=2&activity=true').json()
  assert older['events']+page['events']==expected
  assert not older['has_previous']
  assert len(store.events(jid))==390
