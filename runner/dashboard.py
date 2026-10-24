"""Bounded dashboard views over durable run state and event history."""
PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Research Runner</title>
<script>
try{const theme=localStorage.getItem('rr-theme');if(['light','dark'].includes(theme))document.documentElement.dataset.theme=theme;}catch{}
</script>
<style>
:root{color-scheme:light;--bg:#f4f6f8;--panel:#fff;--soft:#f7f9fb;--ink:#1c2d40;--muted:#5d6e80;--line:#dce3eb;--accent:#235bd8;--tint:#edf3ff;--good:#187352;--warning:#916100;--bad:#b63746;--shadow:0 4px 20px #19344d06}

@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#101720;--panel:#18222e;--soft:#1e2a38;--ink:#e4edf8;--muted:#a2b2c5;--line:#304052;--accent:#92b5ff;--tint:#233650;--good:#75d4b0;--warning:#edc471;--bad:#ff9aa5;--shadow:none}

}

:root[data-theme="dark"]{color-scheme:dark;--bg:#101720;--panel:#18222e;--soft:#1e2a38;--ink:#e4edf8;--muted:#a2b2c5;--line:#304052;--accent:#92b5ff;--tint:#233650;--good:#75d4b0;--warning:#edc471;--bad:#ff9aa5;--shadow:none}


*{box-sizing:border-box}
 [hidden]{display:none!important}

body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 system-ui,-apple-system,sans-serif;height:100dvh;overflow:hidden}

button,input,select,textarea{font:inherit;color:inherit;border:1px solid var(--line);border-radius:5px;background:var(--panel);padding:5px 8px;min-width:0}
button,summary,select{cursor:pointer}
button:hover:not(:disabled),summary:hover{background:var(--tint)}
button:disabled{opacity:.4;cursor:default}
button.primary{background:var(--accent);color:var(--panel)}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
h1,h2,h3,p{margin:0}
h1{font-size:16px;letter-spacing:-.4px}
h2{font-size:15px}
h3{font-size:13px}
.muted,small{color:var(--muted)}
.shell{height:100%;display:flex;flex-direction:column}
.topbar{height:45px;flex-shrink:0;padding:6px 12px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line);background:var(--panel)}
.brand{display:flex;gap:8px;align-items:center;margin-right:auto}
.mark{font-size:12px;background:var(--ink);color:var(--panel);border-radius:5px;padding:2px 6px;font-weight:750}
.theme-control{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted)}
.topbar details{position:relative}
.topbar summary{list-style:none;border:1px solid var(--line);border-radius:5px;padding:5px 8px}
.popover{position:absolute;right:0;top:34px;width:370px;max-width:calc(100vw - 24px);z-index:30;padding:12px;border:1px solid var(--line);border-radius:7px;background:var(--panel);box-shadow:0 10px 35px #0003}
.popover p{margin-bottom:8px;color:var(--muted)}
.inline{display:flex;gap:6px;align-items:center}
.inline input{flex:1}
.workspace{flex:1;min-height:0;display:grid;grid-template-columns:210px minmax(0,1fr)}
.sidebar{min-height:0;display:flex;flex-direction:column;border-right:1px solid var(--line);background:var(--panel)}
.sidebar .heading{padding:9px 10px;display:flex;justify-content:space-between;border-bottom:1px solid var(--line)}
.count{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
#jobs{flex:1;min-height:0;overflow:auto;padding:4px}
.row{display:block;text-align:left;width:100%;border:1px solid transparent;border-radius:4px;padding:7px 6px;margin-bottom:2px;background:transparent}
.row.selected{background:var(--tint);border-color:var(--accent)}
.job-name{display:block;font-size:12px;font-weight:600;overflow-wrap:anywhere}
.job-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:3px}
.state{font-weight:600;text-transform:capitalize}
.state.running,.state.completed,.state.finished{color:var(--good)}
.state.failed,.state.interrupted{color:var(--bad)}
.jobs-hidden .workspace{grid-template-columns:minmax(0,1fr)}
.jobs-hidden .sidebar{display:none}
.main{display:flex;flex-direction:column;min-width:0;min-height:0}
.job-overview{position:relative;background:var(--panel);padding:8px 12px;flex-shrink:0;border-bottom:1px solid var(--line)}
.job-heading{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
#title{overflow-wrap:anywhere}
#status{display:flex;gap:10px;font-size:11px;color:var(--muted);flex-wrap:wrap}
#controls:not(:empty){display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-top:7px}
#controls button,#input{font-size:11px;padding:4px 7px}
#input{flex:1;max-width:420px}
#approvals:not(:empty){padding:6px 12px;border-bottom:1px solid var(--line);max-height:140px;overflow:auto}
#approvals p{overflow-wrap:anywhere}
#approvals button{margin-left:5px}
.tabs{display:flex;gap:2px;padding:0 10px;background:var(--panel);border-bottom:1px solid var(--line);flex-shrink:0}
.tab{border:0;border-bottom:2px solid transparent;border-radius:0;padding:7px 12px;background:transparent;font-size:12px}
.tab[aria-selected="true"]{color:var(--accent);border-color:var(--accent);font-weight:650}
.panel{flex:1;min-height:0;display:flex;flex-direction:column}
.toolbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--soft);flex-shrink:0}
.toolbar input[type="search"]{width:230px;flex:1;max-width:420px}
.toolbar select,.toolbar input,.toolbar button{font-size:12px}
.follow{display:flex;align-items:center;gap:4px;font-size:11px;margin-left:auto;color:var(--muted)}
.history{display:flex;gap:8px;align-items:center;padding:5px 10px;border-bottom:1px solid var(--line);font-size:10px;color:var(--muted);flex-shrink:0}
.history p{margin-right:auto}
.history button{padding:2px 6px;font-size:11px}
.live{font-size:10px;color:var(--muted);white-space:nowrap}
.split{display:grid;grid-template-columns:minmax(260px,42%) minmax(0,1fr);flex:1;min-height:0;background:var(--panel)}
.list{min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable;border-right:1px solid var(--line)}
.event{display:grid;grid-template-columns:58px 112px minmax(0,1fr);gap:7px;align-items:center;min-height:31px;width:100%;text-align:left;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:5px 9px;background:transparent}
.event.selected{background:var(--tint);box-shadow:inset 2px 0 var(--accent)}
.event-meta{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
.event-kind{font-size:10px;color:var(--accent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.event.issue .event-kind{color:var(--bad)}
.event.tool .event-kind{color:var(--warning)}
.event-body{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}
.reader{min-width:0;min-height:0;display:flex;flex-direction:column}
.reader-head{display:flex;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid var(--line);flex-shrink:0;overflow-wrap:anywhere}
.reader-head h3{flex:1;min-width:0}
.reader-content{flex:1;min-height:0;overflow:auto;padding:12px 16px}
.reader-text{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.6}
pre.reader-text{font:12px/1.55 ui-monospace,SFMono-Regular,monospace;margin:0}
details.raw{margin-top:20px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}
details.raw pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.5 ui-monospace,monospace;color:var(--muted);margin-top:10px}
.reader small{font-size:10px}
.back{display:none}
.empty{padding:24px 16px;color:var(--muted);font-size:12px}
.empty strong{display:block;color:var(--ink);margin-bottom:4px}
.file-row{display:grid;grid-template-columns:minmax(0,1fr) 55px 22px;align-items:center;gap:4px;border-bottom:1px solid var(--line);min-height:29px;padding:0 7px}
.file-row.selected{background:var(--tint)}
.file-row button{border:0;background:transparent;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:11px ui-monospace,monospace;padding:6px 2px}
.file-row small{text-align:right;font-size:10px}
.file-row a{text-align:center;text-decoration:none}
.text-panel{padding:16px 20px;overflow:auto;background:var(--panel)}
#result{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;max-width:1000px}
.text-panel h3{margin-bottom:12px}
.finding{padding:8px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.placeholder{color:var(--muted)}
.notice{padding:6px 12px;color:var(--bad);background:var(--tint);border-bottom:1px solid var(--line);font-size:12px;flex-shrink:0}

#action-menu{margin-left:auto;font-size:11px}
#action-menu summary{padding:3px 7px;border:1px solid var(--line);border-radius:4px;list-style:none}
.action-popover{position:absolute;right:8px;top:38px;width:570px;max-width:calc(100vw - 24px);z-index:25;border:1px solid var(--line);border-radius:6px;background:var(--panel);padding:10px;box-shadow:0 8px 30px #0003}
#controls:not(:empty){margin:0}
.collection{border-top:1px solid var(--line);margin-top:9px;padding-top:9px}
.collection p{font-size:11px;color:var(--muted);margin-top:5px}
#collection-command{display:block;width:100%;margin-top:7px;font:11px/1.5 ui-monospace,monospace;resize:vertical;overflow-wrap:anywhere}
.command{font-size:11px;color:var(--muted);margin-bottom:14px}
.command summary{overflow-wrap:anywhere}
.command pre,.tool-output{font:12px/1.55 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0}
.tool-output{color:var(--ink)}

@media(max-width:1050px){.workspace{grid-template-columns:180px minmax(0,1fr)}
.split{grid-template-columns:minmax(250px,45%) minmax(0,1fr)}
.event{grid-template-columns:52px 88px minmax(0,1fr);gap:5px}
.theme-control span{display:none}
}

@media(max-width:700px){.topbar{gap:5px;padding:5px 8px;height:42px}
.mark{display:none}
h1{font-size:13px}
.topbar summary,.topbar select{font-size:11px;padding:4px}
.theme-control{font-size:0}
.theme-control select{max-width:76px}
.workspace{grid-template-columns:minmax(0,1fr)}
.sidebar{display:none}
.jobs-open .sidebar{display:flex;position:absolute;left:0;top:42px;bottom:0;width:240px;z-index:25;box-shadow:10px 0 30px #0004}
.job-overview{padding:7px 9px}
.job-heading{gap:3px}
h2{font-size:13px}
#status{font-size:10px;gap:6px}
#controls{gap:4px}
#input{width:110px}
.tab{padding:7px 8px;font-size:11px}
.tabs{padding:0 3px}
.toolbar{gap:5px;padding:6px}
.toolbar input[type="search"]{width:110px}
.toolbar select{max-width:110px}
.follow{font-size:10px}
.history{gap:4px;padding:5px 6px;flex-wrap:wrap}
.history .live{display:none}
.split{display:flex;flex-direction:column}
.split>.list{flex:1;border:0}
.split>.reader{display:none}
.split.detail-open>.list{display:none}
.split.detail-open>.reader{display:flex;flex:1}
.back{display:inline-block}
.reader-content{padding:10px 12px}
.event{grid-template-columns:50px 95px minmax(0,1fr);min-height:33px}
.popover{position:fixed;left:10px;right:10px;top:45px;width:auto}
.brand{gap:5px}
#toggle-jobs{padding:3px 5px}
.reader-head{padding:7px 9px}
.reader-head small{display:none}
}

</style></head><body><div class="shell">
<header class="topbar"><div class="brand"><button id="toggle-jobs" aria-label="Toggle jobs" onclick="toggleJobs()">☰</button><span class="mark" aria-hidden="true">R</span><h1>Research Runner</h1></div>
<details id="session-menu"><summary>Session</summary><div class="popover"><p>Access token, if required by this runner.</p><div class="inline"><input id="token" type="password" aria-label="Access token" placeholder="Access token" autocomplete="current-password"><button onclick="login()">Sign in</button></div></div></details>
<details id="submit-menu"><summary>New job</summary><div class="popover"><p>Submit a research package.</p><div class="inline"><input id="pkg" type="file" aria-label="Research package" accept=".rrpkg"><button class="primary" onclick="submit()">Submit</button></div></div></details>
<label class="theme-control"><span>Appearance</span><select id="theme" aria-label="Appearance"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label></header>
<div id="notice" class="notice" role="status" hidden></div>
<main class="workspace"><aside class="sidebar"><div class="heading"><h2>Jobs</h2><span id="job-count" class="count">0</span></div><div id="jobs"><p class="empty">Loading jobs…</p></div></aside>
<div class="main"><section class="job-overview"><div class="job-heading"><h2 id="title">Select a job</h2><div id="status"></div><details id="action-menu"><summary>Actions</summary><div class="action-popover"><div id="controls"></div><div class="collection"><button id="copy-collection" onclick="copyCollection()" disabled>Copy collection command</button><p>Download this publication with hash verification. Run from your project with the Research Runner CLI installed. Interrupted downloads can be retried.</p><p id="collection-feedback" role="status" aria-live="polite"></p><textarea id="collection-command" aria-label="Collection command" rows="4" readonly hidden></textarea></div></div></details></div></section><div id="approvals"></div>
<div class="tabs" role="tablist" aria-label="Job views"><button class="tab" id="tab-activity" role="tab" aria-controls="panel-activity" aria-selected="true" onclick="showTab('activity')">Activity</button><button class="tab" id="tab-files" role="tab" aria-controls="panel-files" aria-selected="false" onclick="showTab('files')">Files <span id="file-count" class="count">0</span></button><button class="tab" id="tab-findings" role="tab" aria-controls="panel-findings" aria-selected="false" onclick="showTab('findings')">Findings</button><button class="tab" id="tab-summary" role="tab" aria-controls="panel-summary" aria-selected="false" onclick="showTab('summary')">Summary</button></div>
<section id="panel-activity" class="panel" role="tabpanel" aria-labelledby="tab-activity"><div class="toolbar"><input id="log-search" type="search" aria-label="Search events on this page" placeholder="Search this page…"><select id="log-filter" aria-label="Filter activity"><option value="all">All activity</option><option value="message">Messages</option><option value="tool">Tools</option><option value="issue">Errors &amp; approvals</option></select><label class="follow"><input id="follow" type="checkbox"> Follow live</label></div>
<div class="history"><p id="history-note">Select a job to view activity.</p><span id="connection" class="live" role="status">No job selected</span><button id="older" onclick="olderEvents()" disabled>Older events</button><button id="latest" onclick="latestEvents()" disabled>Latest events</button></div>
<div class="split" id="activity-split"><div id="events" class="list" role="region" aria-label="Job activity" tabindex="0"><div class="empty">Select a job to review its activity.</div></div><section id="event-reader" class="reader" aria-label="Event reader"><div class="empty">Select an event to read its full message or tool output.</div></section></div></section>
<section id="panel-files" class="panel" role="tabpanel" aria-labelledby="tab-files" hidden><div class="toolbar"><input id="file-search" type="search" aria-label="Search files" placeholder="Filter  files by path…"><span id="file-note" class="count"></span></div><div class="split" id="file-split"><div id="artifacts" class="list" aria-label="Published files"><div class="empty">No published files yet.</div></div><section class="reader" id="file-reader" aria-label="File preview"><div class="empty">Select a file to preview its contents. Use ↓ to download.</div></section></div></section>
<section id="panel-findings" class="panel text-panel" role="tabpanel" aria-labelledby="tab-findings" hidden><h3>Findings from the latest activity</h3><div id="findings"><span class="placeholder">No findings yet.</span></div></section>
<section id="panel-summary" class="panel text-panel" role="tabpanel" aria-labelledby="tab-summary" hidden><h3>Result &amp; next action</h3><div id="result">Choose a job to review its result.</div></section></div></main></div>
<script>
let current=null,seen=0,es=null,lastState=null,selection=0,generation=null,collectionText='';
let logRows=[],logFrame=null,historyPage=null,viewingHistory=false,refreshing=false,jobsMarkup=null;
const PAGE_SIZE=100;
let selectedEvent=null,publicationFiles=[],selectedFile=null,fileVersion=0,readerSignature=null;
const $=s=>document.querySelector(s);
const key=()=>{if(typeof crypto.randomUUID==='function')return crypto.randomUUID();const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,opt={}){const r=await fetch(path,opt);const b=await r.json();if(!r.ok)throw Error(b.detail||r.status);return b;}
function fail(e){$('#notice').textContent=e.message;$('#notice').hidden=false;}
function clearNotice(){$('#notice').hidden=true;}
const themeSelect=$('#theme');themeSelect.value=document.documentElement.dataset.theme||'system';
themeSelect.onchange=()=>{const value=themeSelect.value;if(value==='system')delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=value;try{localStorage.setItem('rr-theme',value);}catch{}};
$('#log-search').oninput=flushLog;$('#log-filter').onchange=flushLog;
$('#follow').onchange=()=>{if($('#follow').checked)$('#events').scrollTop=$('#events').scrollHeight;};
$('#events').addEventListener('scroll',()=>{const el=$('#events');if(el.scrollHeight-el.scrollTop-el.clientHeight>50)$('#follow').checked=false;},{passive:true});
async function login(){try{const r=await fetch('/api/session',{method:'POST',headers:{Authorization:'Bearer '+$('#token').value}});if(!r.ok)throw Error('Sign-in failed');$('#token').value='';$('#session-menu').open=false;clearNotice();await refresh();}catch(e){alert(e.message);}}
function toggleJobs(){document.body.classList.toggle(matchMedia('(max-width:700px)').matches?'jobs-open':'jobs-hidden');}
function showTab(name){
 for(const tab of document.querySelectorAll('.tab')){const active=tab.id==='tab-'+name;tab.setAttribute('aria-selected',active);$('#'+tab.getAttribute('aria-controls')).hidden=!active;}
}
for(const tab of document.querySelectorAll('.tab'))tab.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=[...document.querySelectorAll('.tab')];let i=tabs.indexOf(tab);i=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[i].click();tabs[i].focus();};
function backToList(which){$('#'+which+'-split').classList.remove('detail-open');}
function sizeLabel(n){return n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?(n/1024).toFixed(1)+' KB':n+' B';}
function fileUrl(file){return `/api/jobs/${current}/artifacts/${encodeURIComponent(file.name)}?generation=${generation}`;}
function shellQuote(value){return "'"+String(value).replace(/'/g,"'\"'\"'")+"'";}
function collectionCommand(){
 if(!current||!Number.isSafeInteger(generation)||generation<1||!publicationFiles.length)return '';
 const destination=`runs/review-${current}-g${generation}`;
 return `research-runner --url ${shellQuote(location.origin)} collect ${shellQuote(current)} ${shellQuote(destination)} --generation ${generation} --apply`;
}
function updateCollection(){
 const command=collectionCommand();$('#copy-collection').disabled=!command;
 $('#copy-collection').title=command?'Copy a download command for generation '+generation:'Available when this job has published files';
 if(collectionText!==command){collectionText=command;$('#collection-command').value=command;$('#collection-command').hidden=true;$('#collection-feedback').textContent='';}
}
async function copyCollection(){
 const command=collectionCommand(),version=selection;if(!command)return;
 let copied=false;
 try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(command);copied=true;}}catch{}
 if(version!==selection)return;
 if(!copied){
  const field=$('#collection-command');field.value=command;field.hidden=false;field.focus();field.select();
  try{copied=document.execCommand('copy');}catch{}
  if(copied)field.hidden=true;
 }
 $('#collection-feedback').textContent=copied?'Collection command copied.':'Select and copy the command below.';
}
function showArtifacts(publication){
 const next=publication?publication.generation:0;if(generation===next)return;generation=next;
 fileVersion++;selectedFile=null;publicationFiles=publication?.files||[];updateCollection();$('#file-count').textContent=publicationFiles.length;renderFiles();
 $('#file-reader').innerHTML='<div class="empty">Select a file to preview its contents. Use ↓ to download.</div>';
}
function renderFiles(){
 const query=$('#file-search').value.toLowerCase(),files=publicationFiles.filter(f=>f.name.toLowerCase().includes(query));const fragment=document.createDocumentFragment();
 for(const file of files){const row=document.createElement('div');row.className='file-row'+(selectedFile===file.name?' selected':'');
 const preview=document.createElement('button');preview.textContent=file.name;preview.title=file.name;preview.onclick=()=>previewFile(file);row.append(preview);
 const size=document.createElement('small');size.textContent=sizeLabel(file.size||0);row.append(size);
 const download=document.createElement('a');download.href=fileUrl(file);download.textContent='↓';download.setAttribute('aria-label','Download '+file.name);download.title='Download '+file.name;row.append(download);fragment.append(row);}
 $('#artifacts').replaceChildren(fragment);if(!files.length)$('#artifacts').innerHTML='<div class="empty">'+(publicationFiles.length?'No files match this path.':'No published files yet.')+'</div>';
 $('#file-note').textContent=files.length+' of '+publicationFiles.length+' files';
}
$('#file-search').oninput=renderFiles;
async function previewFile(file){
 selectedFile=file.name;renderFiles();const version=++fileVersion;$('#file-split').classList.add('detail-open');
 const reader=$('#file-reader');reader.innerHTML='<div class="reader-head"><button class="back" onclick="backToList(\'file\')">← Files</button><h3></h3><a>Download</a></div><div class="reader-content"><pre class="reader-text"></pre></div>';
 reader.querySelector('h3').textContent=file.name;reader.querySelector('a').href=fileUrl(file);const content=reader.querySelector('pre');
 if(!/\.(md|txt|json|jsonl|py|js|ts|csv|log|xml|yaml|yml|toml|sh|cue)$/i.test(file.name)||file.size>1024*1024){content.textContent='Preview available for text files up to 1 MB. Download this file to inspect it.';return;}
 content.textContent='Loading file…';
 try{const response=await fetch(fileUrl(file));if(!response.ok)throw Error('Could not load file ('+response.status+')');const text=await response.text();if(version!==fileVersion)return;content.textContent=text;}catch(e){if(version===fileVersion)content.textContent=e.message;}
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
  const jobs=await api('/api/jobs');$('#job-count').textContent=jobs.length;
  const markup=jobs.map(j=>`<button class="row ${j.id===current?'selected':''}" data-job="${esc(j.id)}" aria-pressed="${j.id===current}"><span class="job-name">${esc(j.name)}</span><span class="job-meta"><span class="state ${esc(j.state)}">${esc(j.state)}</span><span>${esc(j.id.slice(0,8))}</span></span></button>`).join('')||'<div class="empty"><strong>No jobs yet</strong>Submit a package to get started.</div>';
  if(markup!==jobsMarkup){jobsMarkup=markup;$('#jobs').innerHTML=markup;for(const row of $('#jobs').children)if(row.dataset.job)row.onclick=()=>openJob(row.dataset.job);}
  const id=current,version=selection;if(!id)return;
  const s=await api(`/api/jobs/${id}/status`);if(id!==current||version!==selection)return;
  $('#title').textContent=s.name+' · '+s.state;
  const info=[s.outcome?'Outcome: '+s.outcome:s.state,s.remaining_seconds==null?null:'Budget remaining: '+Math.ceil(s.remaining_seconds)+'s',s.finalize_reason,'Validation: '+s.validation].filter(Boolean);
  $('#status').innerHTML=info.map(value=>`<span>${esc(value)}</span>`).join('');
  $('#result').textContent=s.result?s.result.summary+(s.result.next_action?'\nNext: '+s.result.next_action:''):(s.publication?.checkpoint?.summary||'');
  if(lastState!==s.state){lastState=s.state;$('#controls').innerHTML=`<button onclick="act('start')" ${['queued','waiting','interrupted','failed'].includes(s.state)?'':'disabled'}>Start/resume</button><button onclick="act('stop')" ${['running','finalizing'].includes(s.state)?'':'disabled'}>Stop</button><button onclick="act('finalize')" ${s.state==='running'?'':'disabled'}>Finish up</button><input id="input" aria-label="Continuation input" placeholder="Continuation input" ${s.state==='running'?'':'disabled'}><button onclick="act('input')" ${s.state==='running'?'':'disabled'}>Send input</button>`;}
  showArtifacts(s.publication);showApprovals(s.pending_approvals);
 }finally{refreshing=false;}
}
async function submit(){const f=$('#pkg').files[0];if(!f)return;try{const r=await api('/api/jobs',{method:'POST',headers:{'Idempotency-Key':key()},body:f});$('#submit-menu').open=false;await refresh();await openJob(r.id);}catch(e){alert(e.message);}}
async function act(action){if(!current)return;try{await api(`/api/jobs/${current}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key()},body:JSON.stringify({input:$('#input').value})});$('#action-menu').open=false;await refresh();}catch(e){alert(e.message);}}
async function decide(d,decision){try{await api(`/api/jobs/${current}/approve`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key()},body:JSON.stringify({request_id:d.id,attempt:d.attempt,turn_id:d.turn_id,decision})});await refresh();}catch(e){alert(e.message);}}
function readable(e){
 const d=e.data||{},p=d.payload||{},item=p.item||{};
 const method=d.method||e.kind;
 let label=(item.type?method.replace(/^item/,item.type):method).replace(/^item\//,'').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[_/.-]/g,' ').replace(/^./,c=>c.toUpperCase());
 if(item.type==='commandExecution')label='Command '+(d.method==='item/completed'?'finished':'started');
 if(item.type==='agentMessage')label='Message '+(d.method==='item/completed'?'finished':'started');
 const category=/error|failed|interrupt|approval/i.test(method+' '+(item.status||''))||p.error?'issue':/command|tool|fileChange|file_change|exec|diff/i.test(method+' '+(item.type||''))?'tool':/message|progress|finding|reasoning/i.test(method+' '+(item.type||''))?'message':'other';
 const parts=[d.message,d.text,d.claim,d.error,d.reason,p.delta,p.text,p.message,item.text,item.command,item.aggregated_output,item.aggregatedOutput,p.output,p.diff,d.result?.summary,d.checkpoint?.summary,...(p.turn?.items||[]).map(v=>v.text),p.error?.message,p.turn?.error?.message,d.input,d.name,...(item.summary||[]),...(item.content||[]).map(v=>typeof v==='string'?v:v.text),...(item.changes||[]).map(v=>v.path)].filter(v=>typeof v==='string'&&v.length);
 if(e.kind==='asset_progress'){
  const count=Number.isFinite(d.bytes_completed)?' · '+d.bytes_completed.toLocaleString()+(Number.isFinite(d.bytes_total)?' / '+d.bytes_total.toLocaleString():'')+' bytes':'';
  return {label:'Asset preparation',category:'message',body:(d.asset_type||'')+' · '+(d.asset_id||'')+' → '+(d.destination||'')+'\n'+(d.message||'Preparing')+count};
 }
 const body=[...new Set(parts)].join('\n\n')||Object.entries(d).filter(([k,v])=>typeof v==='string'&&!['method','id','turn_id'].includes(k)).map(([k,v])=>k.replace(/_/g,' ')+': '+v).join(' · ')||'Event recorded. Open details to inspect the payload.';
 return {label,category,body,command:item.command,output:item.aggregated_output??item.aggregatedOutput};
}
// Keep native streaming chunks together within the bounded history page.
function activityRows(){
 const rows=[];
 for(const e of logRows){
  const previous=rows[rows.length-1],p=e.data?.payload,method=e.data?.method;
  const item=p?.item_id??p?.itemId,turn=p?.turn_id??p?.turnId;
  if(previous&&e.kind==='codex'&&/delta$/i.test(method||'')&&typeof p?.delta==='string'&&item&&previous.method===method&&previous.item===item&&previous.turn===turn){
   previous.e={...e,data:{...e.data,payload:{...p,delta:previous.e.data.payload.delta+p.delta}}};previous.sources.push(e);
  }else rows.push({e,method,item,turn,sources:[e]});
 }
 return rows.map(r=>({...r,...readable(r.e)}));
}
function readEvent(r){
 selectedEvent=r.sources[0].seq;
 for(const row of $('#events').querySelectorAll('.event')){const active=row.dataset.key===String(selectedEvent);row.classList.toggle('selected',active);row.setAttribute('aria-pressed',active);}
 const signature=JSON.stringify(r.e);if(signature===readerSignature)return;readerSignature=signature;
 const reader=$('#event-reader');reader.innerHTML='<div class="reader-head"><button class="back" onclick="backToList(\'activity\')">← Events</button><h3></h3><small></small></div><div class="reader-content"><div class="reader-text"></div><details class="raw"><summary>Raw event data</summary></details></div>';
 reader.querySelector('h3').textContent=r.label;reader.querySelector('small').textContent='#'+r.sources[0].seq+(r.sources.length>1?'–'+r.e.seq:'')+' · Attempt '+(r.e.attempt??0)+(r.e.ts?' · '+new Date(r.e.ts*1000).toLocaleString():'');
 const text=reader.querySelector('.reader-text');
 if(r.command){
  const command=document.createElement(r.command.length>200?'details':'div');command.className='command';
  if(r.command.length>200){const heading=document.createElement('summary');heading.textContent='Command · '+r.command.split('\n')[0].slice(0,100)+'…';command.append(heading);}
  const code=document.createElement('pre');code.textContent=r.command;command.append(code);text.append(command);
  const output=document.createElement('pre');output.className='tool-output';output.textContent=r.output||'No output recorded.';text.append(output);
 }else text.textContent=r.body;

 const details=reader.querySelector('details');details.ontoggle=()=>{if(details.open&&!details.querySelector('pre')){const pre=document.createElement('pre');pre.textContent=JSON.stringify(r.sources.length>1?r.sources:r.e,null,2);details.append(pre);}};
}
function flushLog(){
 logFrame=null;const container=$('#events'),scroll=container.scrollTop;
 const query=$('#log-search').value.toLowerCase(),filter=$('#log-filter').value;
 const rows=activityRows().filter(r=>(filter==='all'||r.category===filter)&&(!query||[r.body,r.label,JSON.stringify(r.e.data)].join(' ').toLowerCase().includes(query)));
 const fragment=document.createDocumentFragment();
 for(const r of rows){
  const {e,label,category,body,sources}=r;const row=document.createElement('button');row.className='event '+category;row.dataset.key=sources[0].seq;
  const time=e.ts?new Date(e.ts*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'#'+e.seq;
  row.innerHTML=`<span class="event-meta">${esc(time)}</span><span class="event-kind"></span><span class="event-body"></span>`;
  row.querySelector('.event-kind').textContent=label;row.querySelector('.event-body').textContent=body.slice(0,600);row.title=label+' · #'+e.seq;row.onclick=()=>{readEvent(r);$('#activity-split').classList.add('detail-open');};fragment.append(row);
 }
 if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=!current?'Select a job to review its activity.':logRows.length?'No matching events on this page.':'No activity yet.';fragment.append(empty);}
 container.replaceChildren(fragment);container.scrollTop=$('#follow').checked&&!viewingHistory?container.scrollHeight:scroll;
 const selected=$('#follow').checked?rows[rows.length-1]:rows.find(r=>r.sources[0].seq===selectedEvent)||rows[0];
 if(selected)readEvent(selected);else{selectedEvent=null;readerSignature=null;$('#event-reader').innerHTML='<div class="empty">Select an event to read its full contents.</div>';}
 if(current)$('#history-note').textContent=`${viewingHistory?'History':'Latest'} · ${rows.length} entries / ${logRows.length} events${historyPage?.has_previous?' · more history':''}`;
}
function appendLog(e){logRows.push(e);if(logRows.length>PAGE_SIZE)logRows.shift();if(logFrame===null)logFrame=requestAnimationFrame(flushLog);}
function finding(e){if($('#findings .placeholder'))$('#findings').replaceChildren();const p=document.createElement('p');p.className='finding';p.textContent=e.data.claim+' — '+(e.data.evidence_url||'');$('#findings').append(p);while($('#findings').childNodes.length>PAGE_SIZE)$('#findings').firstChild.remove();}
function render(e){
 if(!viewingHistory&&e.kind!=='artifact')appendLog(e);
 if(e.kind==='finding')finding(e);
 // Current artifacts and approvals come from status, not historical event replay.
 if(['artifact','approval_request','approval_decision','finished','completed'].includes(e.kind))refresh().catch(fail);
}
async function loadEvents(before=null){
 const id=current,version=selection;if(!id)return;
 const query=(before===null?'tail=true':`before=${before}`)+'&activity=true';
 const page=await api(`/api/jobs/${id}/event-log?limit=${PAGE_SIZE}&${query}`);
 if(id!==current||version!==selection)return;
 viewingHistory=before!==null;historyPage=page;logRows=page.events;selectedEvent=null;readerSignature=null;flushLog();
 $('#older').disabled=!page.has_previous;$('#latest').disabled=false;$('#connection').textContent=viewingHistory?'Browsing history':'Connecting…';
 if(before===null){seen=page.next_cursor;$('#findings').replaceChildren();for(const e of page.events)if(e.kind==='finding')finding(e);if(!$('#findings').childNodes.length)$('#findings').innerHTML='<span class="placeholder">No findings in the latest activity.</span>';return true;}
}
async function olderEvents(){try{if(historyPage?.previous_cursor)await loadEvents(historyPage.previous_cursor);}catch(e){fail(e);}}
async function latestEvents(){try{if(es)es.close();if(await loadEvents())connect();}catch(e){fail(e);}}
function connect(){
 const id=current,version=selection;es=new EventSource(`/api/jobs/${id}/events?after=${seen}`);
 es.onopen=()=>{if(id===current&&version===selection)$('#connection').textContent=viewingHistory?'Browsing history':'Live updates connected';};
 es.onerror=()=>{if(id===current&&version===selection)$('#connection').textContent=viewingHistory?'Browsing history · reconnecting…':'Reconnecting…';};
 es.onmessage=x=>{if(id!==current||version!==selection)return;const e=JSON.parse(x.data);if(e.seq>seen){seen=e.seq;render(e);}};
}
async function openJob(id){
 if(es)es.close();if(logFrame!==null)cancelAnimationFrame(logFrame);logFrame=null;
 clearNotice();showTab('activity');document.body.classList.remove('jobs-open');$('#file-search').value='';fileVersion++;selectedEvent=null;readerSignature=null;selectedFile=null;publicationFiles=[];$('#file-count').textContent='0';$('#activity-split').classList.remove('detail-open');$('#file-split').classList.remove('detail-open');$('#event-reader').innerHTML='';$('#file-reader').innerHTML='';$('#log-search').value='';$('#log-filter').value='all';$('#follow').checked=false;current=id;selection++;seen=0;lastState=null;generation=null;viewingHistory=false;historyPage=null;logRows=[];updateCollection();
 for(const name of ['events','findings','artifacts','controls','approvals','status','result'])$('#'+name).replaceChildren();
 $('#title').textContent='Loading job…';$('#older').disabled=true;$('#latest').disabled=true;$('#connection').textContent='Connecting…';
 try{await Promise.all([refresh(),latestEvents()]);}catch(e){fail(e);}
}
setInterval(()=>refresh().catch(fail),3000);refresh().catch(fail);
</script></body></html>'''
