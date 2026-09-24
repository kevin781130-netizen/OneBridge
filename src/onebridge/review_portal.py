from __future__ import annotations

import json


_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OneBridge Review Portal</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1080px;margin:auto;padding:24px}
section{border:1px solid #8885;border-radius:12px;padding:14px;margin:14px 0}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select,textarea,button{font:inherit;padding:7px}
textarea{width:100%;min-height:220px;box-sizing:border-box;font-family:monospace}
table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid #8884;text-align:left}
pre{white-space:pre-wrap;max-height:420px;overflow:auto}
</style>
</head>
<body>
<h1>OneBridge Review Portal</h1>
<p>Task: <code id="task-id"></code></p>
<div class="row">
<input id="token" type="password" placeholder="API key（若啟用）">
<button id="refresh">重新整理</button>
<strong id="status"></strong><span id="progress"></span>
</div>
<p id="error"></p>

<section>
<h2>Artifacts</h2>
<table><thead><tr><th></th><th>Kind</th><th>Rev</th><th>Status</th><th>Producer</th></tr></thead><tbody id="rows"></tbody></table>
<div class="row">
<button id="approve">核准選取</button><button id="reject">退回選取</button>
<input id="reason" placeholder="原因（選填）">
</div>
</section>

<section>
<h2>Revision</h2>
<div class="row">
<select id="artifact"></select>
<input id="filename" placeholder="filename">
<input id="media-type" placeholder="media type">
<button id="load">載入</button><button id="save">建立新 revision</button>
</div>
<textarea id="content"></textarea>
</section>

<section>
<h2>Compare</h2>
<div class="row"><select id="left"></select><select id="right"></select><button id="compare">比較</button></div>
<pre id="diff"></pre>
</section>

<section>
<h2>Release</h2>
<div class="row"><button id="gate">檢查 Gate</button><button id="release">建立 Release</button><span id="release-result"></span></div>
</section>

<script>
"use strict";
const taskId = __TASK_JSON__;
document.getElementById("task-id").textContent = taskId;
const el = id => document.getElementById(id);
let artifacts = [];

function headers(jsonBody=false){
  const h = {};
  const token = el("token").value.trim();
  if(token) h.Authorization = token.toLowerCase().startsWith("bearer ") ? token : "Bearer " + token;
  if(jsonBody) h["Content-Type"] = "application/json";
  return h;
}
async function api(path, options={}){
  const response = await fetch(path, options);
  let payload = null;
  try{ payload = await response.json(); }catch(_){}
  if(!response.ok) throw new Error(String(response.status) + " " + ((payload && payload.detail) || response.statusText));
  return payload;
}
function taskPath(suffix=""){ return "/api/v1/tasks/" + encodeURIComponent(taskId) + suffix; }
function fillSelect(node, values){
  const before = node.value; node.replaceChildren();
  values.forEach(a => { const o=document.createElement("option"); o.value=a.artifact_id; o.textContent=a.kind+" r"+a.revision+" · "+a.status; node.appendChild(o); });
  if([...node.options].some(o=>o.value===before)) node.value=before;
}
function render(){
  const rows=el("rows"); rows.replaceChildren();
  artifacts.forEach(a => {
    const tr=document.createElement("tr");
    const check=document.createElement("input"); check.type="checkbox"; check.dataset.id=a.artifact_id;
    const td0=document.createElement("td"); td0.appendChild(check); tr.appendChild(td0);
    [a.kind,String(a.revision),a.status,a.producer_adapter+" @ "+a.producer_version].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.appendChild(td);});
    rows.appendChild(tr);
  });
  const editable=artifacts.filter(a=>a.media_type.startsWith("text/")||["application/json","application/xml"].includes(a.media_type));
  fillSelect(el("artifact"),editable); fillSelect(el("left"),artifacts); fillSelect(el("right"),artifacts);
  if(el("right").options.length>1) el("right").selectedIndex=el("right").options.length-1;
}
async function refresh(){
  el("error").textContent="";
  try{
    const results=await Promise.all([
      api(taskPath(),{headers:headers()}),
      api(taskPath("/progress"),{headers:headers()}),
      api(taskPath("/artifacts"),{headers:headers()})
    ]);
    el("status").textContent=results[0].status;
    el("progress").textContent=results[1].message||"";
    artifacts=results[2]||[]; render();
  }catch(e){el("error").textContent=String(e);}
}
function selected(){return [...document.querySelectorAll("#rows input:checked")].map(x=>x.dataset.id);}
async function review(decision){
  const ids=selected(); if(!ids.length)return;
  try{
    await api(taskPath("/approve"),{method:"POST",headers:headers(true),body:JSON.stringify({artifact_ids:ids,decision:decision,actor:"review-portal",reason:el("reason").value})});
    await refresh();
  }catch(e){el("error").textContent=String(e);}
}
async function loadContent(){
  const id=el("artifact").value;if(!id)return;
  try{
    const v=await api(taskPath("/artifacts/"+encodeURIComponent(id)+"/content"),{headers:headers()});
    el("content").value=v.content;el("filename").value=v.filename;el("media-type").value=v.media_type;
  }catch(e){el("error").textContent=String(e);}
}
async function saveRevision(){
  const id=el("artifact").value;if(!id)return;
  try{
    await api(taskPath("/artifacts/"+encodeURIComponent(id)+"/revisions"),{method:"POST",headers:headers(true),body:JSON.stringify({content:el("content").value,filename:el("filename").value,media_type:el("media-type").value,actor:"review-portal",reason:el("reason").value})});
    await refresh();
  }catch(e){el("error").textContent=String(e);}
}
async function compare(){
  const l=el("left").value,r=el("right").value;if(!l||!r)return;
  try{
    const v=await api(taskPath("/artifacts/"+encodeURIComponent(l)+"/compare/"+encodeURIComponent(r)),{headers:headers()});
    el("diff").textContent=v.diff||JSON.stringify(v,null,2);
  }catch(e){el("error").textContent=String(e);}
}
async function gate(){
  try{const v=await api(taskPath("/release-gate"),{headers:headers()});el("release-result").textContent=v.allowed?"Gate 通過":"Blocked: "+v.reasons.join(", ");}catch(e){el("error").textContent=String(e);}
}
async function release(){
  try{const v=await api(taskPath("/release"),{method:"POST",headers:headers()});el("release-result").textContent="Released: "+v.artifact_id;await refresh();}catch(e){el("error").textContent=String(e);}
}
el("refresh").onclick=refresh;el("approve").onclick=()=>review("approve");el("reject").onclick=()=>review("reject");
el("load").onclick=loadContent;el("save").onclick=saveRevision;el("compare").onclick=compare;el("gate").onclick=gate;el("release").onclick=release;
refresh();
</script>
</body>
</html>
"""


def render_review_portal(task_id: str) -> str:
    return _TEMPLATE.replace("__TASK_JSON__", json.dumps(str(task_id)))
