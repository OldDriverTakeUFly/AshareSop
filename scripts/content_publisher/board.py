#!/usr/bin/env python3
# content_publisher/board.py — 发稿池 Web 管理台(FastAPI,只读两个台账 + 写操作经 queue.py CLI 闸门)
# 用法: .venv/bin/python scripts/content_publisher/board.py   → http://127.0.0.1:8765
# 数据: publish_queue/re publish_log(content_publisher.db,可用 PUBLISHER_DB 注入)
#      cards(content_cards.db,cardgen 台账,只读展示时效)
# 写操作一律 subprocess 调 queue.py——review/schedule/mark 的状态机与时效硬闸不另写一份。
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent

# 防遮蔽:本目录 queue.py(发稿池)与 stdlib queue 同名,脚本方式启动时目录在 sys.path[0],
# anyio 的 `from queue import Queue` 会误 import 到它——移除该路径项(发稿池经 importlib 显式加载,不受影响)
sys.path = [p for p in sys.path if p != str(ROOT)]


def _load_queue():
    """加载同目录 queue.py(读其模块级 DB,随 PUBLISHER_DB 环境变量)。"""
    spec = importlib.util.spec_from_file_location("publisher_queue", ROOT / "queue.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_QUEUE = _load_queue()
_CARDGEN_DB = REPO_ROOT / "storage" / "database" / "content_cards.db"

# 远程访问(ZeroTier/局域网):BOARD_HOST=0.0.0.0 时必须设 BOARD_TOKEN,
# 所有请求(含页面)须带 ?token=<值>,否则 401——管理台写操作可发帖,不能裸奔。
_TOKEN = os.environ.get("BOARD_TOKEN", "")
_HOST = os.environ.get("BOARD_HOST", "127.0.0.1")
_PORT = int(os.environ.get("BOARD_PORT", "8765"))

app = FastAPI(title="发稿池管理台", version="0.1")


@app.middleware("http")
async def _auth(request, call_next):
    if _TOKEN and request.query_params.get("token") != _TOKEN:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("token 无效或缺失:?token=<BOARD_TOKEN>", status_code=401)
    return await call_next(request)


# ── 读接口 ──
def _queue_rows(status: str | None) -> list[dict]:
    q = ("SELECT id,created_at,source,title,tags,images,platform,status,"
         "scheduled_at,published_at,release_expires,scan_result FROM publish_queue")
    params: tuple = ()
    if status:
        q += " WHERE status=?"
        params = (status,)
    with _QUEUE._conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC", params).fetchall()]


@app.get("/api/queue")
def api_queue(status: str | None = None) -> list[dict]:
    return _queue_rows(status)


@app.get("/api/due")
def api_due() -> list[dict]:
    now = datetime.now().isoformat(timespec="minutes")
    with _QUEUE._conn() as c:
        rows = c.execute(
            "SELECT id,title,scheduled_at,release_expires FROM publish_queue "
            "WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at", (now,)).fetchall()
    today = now[:10]
    return [{**dict(r), "stale": bool(r["release_expires"] and r["release_expires"] < today)}
            for r in rows]


@app.get("/api/cards")
def api_cards() -> list[dict]:
    if not _CARDGEN_DB.exists():
        return []
    conn = sqlite3.connect(_CARDGEN_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT topic,current_version,status,as_of,expires_at,updated_at FROM cards "
            "ORDER BY updated_at DESC").fetchall()
    finally:
        conn.close()
    today = datetime.now().strftime("%Y-%m-%d")
    return [{**dict(r), "expired": bool(r["expires_at"] and r["expires_at"] < today)}
            for r in rows]


# ── 写接口(经 queue.py CLI,闸门唯一真相源)──
class ScheduleBody(BaseModel):
    at: str  # "YYYY-MM-DD HH:MM"


class MarkBody(BaseModel):
    status: str  # published / failed / ...


def _cli(*args: str) -> JSONResponse:
    proc = subprocess.run([sys.executable, str(ROOT / "queue.py"), *args],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    ok = proc.returncode == 0
    return JSONResponse({"ok": ok, "stdout": proc.stdout.strip(),
                         "stderr": proc.stderr.strip()}, status_code=200 if ok else 400)


@app.post("/api/queue/{qid}/review")
def api_review(qid: int) -> JSONResponse:
    return _cli("review", str(qid))


@app.post("/api/queue/{qid}/schedule")
def api_schedule(qid: int, body: ScheduleBody) -> JSONResponse:
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", body.at.strip()):
        return JSONResponse({"ok": False, "stderr": "at 须为 YYYY-MM-DD[ HH:MM]"}, status_code=400)
    return _cli("schedule", str(qid), "--at", body.at.strip())


@app.post("/api/queue/{qid}/mark")
def api_mark(qid: int, body: MarkBody) -> JSONResponse:
    if body.status not in _QUEUE.STATUSES:
        return JSONResponse({"ok": False, "stderr": f"非法状态 {body.status}"}, status_code=400)
    return _cli("mark", str(qid), body.status)


@app.post("/api/scan")
def api_scan() -> JSONResponse:
    return _cli("scan")


# ── 页面 ──
_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>发稿池管理台</title><style>
body{font-family:system-ui,'PingFang SC',sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
header{padding:14px 22px;background:#1e293b;display:flex;gap:18px;align-items:center}
header h1{font-size:17px;margin:0}
button{background:#334155;color:#e2e8f0;border:1px solid #475569;border-radius:6px;
padding:4px 10px;cursor:pointer;font-size:12px}
button:hover{background:#475569}
button.primary{background:#2563eb;border-color:#3b82f6}
button.danger{background:#b91c1c;border-color:#dc2626}
main{padding:16px 22px;display:grid;grid-template-columns:2fr 1fr;gap:18px}
table{width:100%;border-collapse:collapse;font-size:13px;background:#1e293b;border-radius:8px}
th,td{padding:7px 9px;border-bottom:1px solid #334155;text-align:left;vertical-align:top}
th{color:#94a3b8;font-weight:600;font-size:12px}
.chip{padding:1px 8px;border-radius:9px;font-size:11px}
.s-draft{background:#78350f}.s-reviewed{background:#1d4ed8}.s-scheduled{background:#065f46}
.s-published{background:#374151}.s-failed{background:#7f1d1d}
.exp-ok{color:#94a3b8}.exp-warn{color:#fbbf24}.exp-bad{color:#f87171;font-weight:700}
.due-item{background:#1e293b;border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:13px}
h2{font-size:14px;color:#94a3b8;margin:0 0 10px}
#toast{position:fixed;bottom:18px;right:18px;background:#334155;padding:10px 16px;border-radius:8px;
font-size:13px;display:none;max-width:480px;white-space:pre-wrap}
</style></head><body>
<header><h1>📕 发稿池管理台</h1>
<select id="f" onchange="load()"><option value="">全部状态</option>
<option>draft</option><option>reviewed</option><option>scheduled</option>
<option>published</option><option>failed</option></select>
<button onclick="api('/api/scan','POST')">重扫 draft</button>
<span id="ts" style="margin-left:auto;font-size:12px;color:#64748b"></span></header>
<main><div>
<table><thead><tr><th>#</th><th>标题</th><th>状态</th><th>数据有效期</th><th>排期</th><th>操作</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<div><h2>⏰ 到点待发布</h2><div id="due"></div><h2 style="margin-top:18px">🗂 cardgen 工程</h2>
<table><thead><tr><th>工程</th><th>状态</th><th>数据截至/过期</th></tr></thead><tbody id="cards"></tbody></table>
</div></main><div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const chip=s=>`<span class="chip s-${s}">${s}</span>`;
const esc=t=>String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function expCls(e){if(!e)return'exp-ok';const d=(new Date(e)-new Date(new Date().toISOString().slice(0,10)))/864e5;
return d<0?'exp-bad':d<=3?'exp-warn':'exp-ok'}
const TK=new URLSearchParams(location.search).get('token');
const withT=u=>u+(u.includes('?')?'&':'?')+(TK?'token='+encodeURIComponent(TK):'');
async function api(url,method,body){const r=await fetch(withT(url),{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
const j=await r.json();toast((j.ok?'✓ ':'✗ ')+(j.stdout||j.stderr||''));await load();return j}
function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',5000)}
async function load(){
const qs=$('f').value?`?status=${$('f').value}`:'';
const rows=await(await fetch(withT('/api/queue'+qs))).json();
$('rows').innerHTML=rows.map(r=>{
const acts=[];
if(r.status==='draft')acts.push(`<button class="primary" onclick="api('/api/queue/${r.id}/review','POST')">审核通过</button>`);
if(r.status==='reviewed')acts.push(`<button class="primary" onclick="sched(${r.id})">排期</button>`);
if(r.status==='scheduled')acts.push(`<button onclick="api('/api/queue/${r.id}/mark','POST',{status:'published'})">标记已发</button>`,
`<button class="danger" onclick="api('/api/queue/${r.id}/mark','POST',{status:'failed'})">失败</button>`);
return`<tr><td>${r.id}</td><td>${esc(r.title)}<div style="color:#64748b;font-size:11px">${esc(r.source||'')}</div></td>
<td>${chip(r.status)}</td><td class="${expCls(r.release_expires)}">${r.release_expires?('至 '+r.release_expires):'—'}</td>
<td>${esc(r.scheduled_at||'')}</td><td style="white-space:nowrap">${acts.join(' ')}</td></tr>`}).join('')
||'<tr><td colspan="6" style="color:#64748b">空</td></tr>';
const due=await(await fetch(withT('/api/due'))).json();
$('due').innerHTML=due.map(d=>`<div class="due-item">${chip('scheduled')} #${d.id} @ ${esc(d.scheduled_at)} ${esc(d.title)}`
+(d.stale?'<span class="exp-bad"> ⚠️数据已过期</span>':'')).join('')||'<div class="due-item" style="color:#64748b">无到点项</div>';
const cards=await(await fetch(withT('/api/cards'))).json();
$('cards').innerHTML=cards.map(c=>`<tr><td>${esc(c.topic)} v${c.current_version}</td><td>${esc(c.status)}</td>
<td class="${c.expired?'exp-bad':'exp-ok'}">${esc(c.as_of||'—')} / ${esc(c.expires_at||'—')}${c.expired?' 已过期':''}</td></tr>`).join('')
||'<tr><td colspan="3" style="color:#64748b">无工程</td></tr>';
$('ts').textContent='刷新于 '+new Date().toLocaleTimeString()}
function sched(id){const at=prompt('排期时间(YYYY-MM-DD HH:MM):');if(!at)return;api(`/api/queue/${id}/schedule`,'POST',{at})}
load();setInterval(load,30000)
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE


if __name__ == "__main__":
    import uvicorn
    print(f"发稿池 DB: {_QUEUE.DB}")
    print(f"cardgen 台账: {_CARDGEN_DB}{' (不存在,忽略)' if not _CARDGEN_DB.exists() else ''}")
    if _HOST != "127.0.0.1" and not _TOKEN:
        sys.exit("BOARD_HOST 非 127.0.0.1 时必须设置 BOARD_TOKEN(远程裸奔拒绝启动)")
    uvicorn.run(app, host=_HOST, port=_PORT)
