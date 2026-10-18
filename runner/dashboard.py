"""Bounded dashboard views over durable run state and event history."""
PAGE = r'''<!doctype html>
<html><head><meta charset="utf-8"><title>Research Runner</title><style>
body{font:15px system-ui;margin:2rem;max-width:1100px}button,input{padding:.5rem;margin:.25rem}
nav{display:flex;gap:2rem}section{border:1px solid #ccc;padding:1rem;margin:1rem 0;min-width:0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;background:#f6f6f6;padding:1rem}
.row{cursor:pointer;border-bottom:1px solid #ddd;padding:.5rem}.state{font-weight:bold}
#artifacts a{display:block;overflow-wrap:anywhere}#result{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:700px){nav{display:block}body{margin:1rem}}
</style></head><body><h1>Research Runner</h1><p>Local single host research jobs</p>
<section><input id="token" type="password" placeholder="Access token (if configured)"><button onclick="login()">Sign in</button></section>
<section><input id="pkg" type="file" accept=".rrpkg"><button onclick="submit()">Submit</button></section>
<nav><section style="min-width:250px"><h2>Jobs</h2><div id="jobs"></div></section>
<section style="flex:1"><h2 id="title">Select a job</h2><div id="status"></div><div id="result"></div><div id="controls"></div><div id="approvals"></div>
<h3>Progress and tools</h3><p id="history-note"></p><button id="older" onclick="olderEvents()" disabled>Older events</button><button onclick="latestEvents()">Latest events</button>
<pre id="events"></pre><h3>Findings</h3><div id="findings"></div><h3>Artifacts</h3><div id="artifacts"></div></section></nav>
<script>
let current=null,seen=0,es=null,lastState=null,selection=0,generation=null;
let logRows=[],logFrame=null,historyPage=null,viewingHistory=false,refreshing=false;
const PAGE_SIZE=100,LINE_CHARS=1000;
const $=s=>document.querySelector(s);
const key=()=>{if(typeof crypto.randomUUID==='function')return crypto.randomUUID();const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,opt={}){const r=await fetch(path,opt);const b=await r.json();if(!r.ok)throw Error(b.detail||r.status);return b;}
function fail(e){$('#history-note').textContent=e.message;}
async function login(){try{const r=await fetch('/api/session',{method:'POST',headers:{Authorization:'Bearer '+$('#token').value}});if(!r.ok)throw Error('Sign-in failed');$('#token').value='';await refresh();}catch(e){alert(e.message);}}
function showArtifacts(publication){
 const next=publication?publication.generation:0;
 if(generation===next)return;generation=next;
 const fragment=document.createDocumentFragment();
 for(const f of publication?.files||[]){const a=document.createElement('a');a.href=`/api/jobs/${current}/artifacts/${encodeURIComponent(f.name)}?generation=${next}`;a.textContent=f.name+' (SHA-256 '+f.sha256.slice(0,12)+'…)';fragment.append(a);}
 $('#artifacts').replaceChildren(fragment);
}
function showApprovals(requests){
 const fragment=document.createDocumentFragment();
 for(const d of requests){const p=document.createElement('p');p.textContent='Approval requested: '+(d.method||'')+' · attempt '+d.attempt+' · turn '+d.turn_id+' '+JSON.stringify(d.details||{})+' ';
 for(const choice of ['accept','decline']){const b=document.createElement('button');b.textContent=choice;b.onclick=()=>decide(d,choice);p.append(b);}fragment.append(p);}
 $('#approvals').replaceChildren(fragment);
}
async function refresh(){
 if(refreshing)return;refreshing=true;
 try{
  const jobs=await api('/api/jobs');$('#jobs').innerHTML=jobs.map(j=>`<div class="row" onclick="openJob('${j.id}')">${esc(j.name)} <span class="state">${esc(j.state)}</span><br><small>${j.id}</small></div>`).join('');
  const id=current,version=selection;if(!id)return;
  const s=await api(`/api/jobs/${id}/status`);if(id!==current||version!==selection)return;
  $('#title').textContent=s.name+' · '+s.state;
  $('#status').textContent=(s.outcome?'Outcome: '+s.outcome+' · ':'')+(s.remaining_seconds===null?'':'Budget remaining: '+Math.ceil(s.remaining_seconds)+'s · ')+(s.finalize_reason||'')+' · Validation: '+s.validation;
  $('#result').textContent=s.result?s.result.summary+(s.result.next_action?'\nNext: '+s.result.next_action:''):(s.publication?.checkpoint?.summary||'');
  if(lastState!==s.state){lastState=s.state;$('#controls').innerHTML=`<button onclick="act('start')" ${['queued','waiting','interrupted','failed'].includes(s.state)?'':'disabled'}>Start/resume</button><button onclick="act('stop')" ${['running','finalizing'].includes(s.state)?'':'disabled'}>Stop</button><button onclick="act('finalize')" ${s.state==='running'?'':'disabled'}>Finish up</button><input id="input" placeholder="Continuation input" ${s.state==='running'?'':'disabled'}><button onclick="act('input')" ${s.state==='running'?'':'disabled'}>Send input</button>`;}
  showArtifacts(s.publication);showApprovals(s.pending_approvals);
 }finally{refreshing=false;}
}
async function submit(){const f=$('#pkg').files[0];if(!f)return;try{const r=await api('/api/jobs',{method:'POST',headers:{'Idempotency-Key':key()},body:f});await refresh();await openJob(r.id);}catch(e){alert(e.message);}}
async function act(action){if(!current)return;try{await api(`/api/jobs/${current}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key()},body:JSON.stringify({input:$('#input').value})});await refresh();}catch(e){alert(e.message);}}
async function decide(d,decision){try{await api(`/api/jobs/${current}/approve`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key()},body:JSON.stringify({request_id:d.id,attempt:d.attempt,turn_id:d.turn_id,decision})});await refresh();}catch(e){alert(e.message);}}
function flushLog(){logFrame=null;$('#events').textContent=logRows.map(e=>{const text=JSON.stringify(e.data);return `#${e.seq} ${e.kind} ${text.slice(0,LINE_CHARS)}${text.length>LINE_CHARS?'… (full event in API)':''}`;}).join('\n');}
function appendLog(e){logRows.push(e);if(logRows.length>PAGE_SIZE)logRows.shift();if(logFrame===null)logFrame=requestAnimationFrame(flushLog);}
function finding(e){const p=document.createElement('p');p.textContent=e.data.claim+' — '+(e.data.evidence_url||'');$('#findings').append(p);while($('#findings').childNodes.length>PAGE_SIZE)$('#findings').firstChild.remove();}
function render(e){
 if(!viewingHistory)appendLog(e);
 if(e.kind==='finding')finding(e);
 // Current artifacts and approvals come from status, not historical event replay.
 if(['artifact','approval_request','approval_decision','finished','completed'].includes(e.kind))refresh().catch(fail);
}
async function loadEvents(before=null){
 const id=current,version=selection;if(!id)return;
 const query=before===null?'tail=true':`before=${before}`;
 const page=await api(`/api/jobs/${id}/event-log?limit=${PAGE_SIZE}&${query}`);
 if(id!==current||version!==selection)return;
 viewingHistory=before!==null;historyPage=page;logRows=page.events;flushLog();
 $('#older').disabled=!page.has_previous;$('#history-note').textContent=`Showing ${page.events.length} events${page.has_previous?' · earlier history available':''}. Long entries are shortened; full events remain available through the API.`;
 if(before===null){seen=page.next_cursor;$('#findings').replaceChildren();for(const e of page.events)if(e.kind==='finding')finding(e);return true;}
}
async function olderEvents(){try{if(historyPage?.previous_cursor)await loadEvents(historyPage.previous_cursor);}catch(e){fail(e);}}
async function latestEvents(){try{if(es)es.close();if(await loadEvents())connect();}catch(e){fail(e);}}
function connect(){
 const id=current,version=selection;es=new EventSource(`/api/jobs/${id}/events?after=${seen}`);
 es.onmessage=x=>{if(id!==current||version!==selection)return;const e=JSON.parse(x.data);if(e.seq>seen){seen=e.seq;render(e);}};
}
async function openJob(id){
 if(es)es.close();if(logFrame!==null)cancelAnimationFrame(logFrame);logFrame=null;
 current=id;selection++;seen=0;lastState=null;generation=null;viewingHistory=false;historyPage=null;logRows=[];
 for(const name of ['events','findings','artifacts','controls','approvals','status','result'])$('#'+name).replaceChildren();
 $('#title').textContent='Loading job…';$('#older').disabled=true;
 try{await Promise.all([refresh(),latestEvents()]);}catch(e){fail(e);}
}
setInterval(()=>refresh().catch(fail),3000);refresh().catch(fail);
</script></body></html>'''
