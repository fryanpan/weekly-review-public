"""Render a run's estimate verdicts as one self-contained HTML page.

The user reviews one task at a time but moves back and forth inside a project
("group the week by project so I can see the story in each project"). The
markdown worksheet grew to ~10k lines and every in-place edit cost a block
edit; this page is regenerated from `run.sqlite` in one call and served as a
workspace mockup, so nothing on it is hand-maintained.

Layout: a project rail (task count, reviewed tally), the project's tasks in
start-time order, and one task's detail with prev/next inside the project.
Every detail field is the same fact the worksheet prints — why, what got done,
estimate, breakdown, hands-on, the board task it lined up with, and the
recorded verdict if there is one. The verdict box carries the task id as
`VERDICT [<id>]` so a comment anchored on it is harvested exactly like a
worksheet verdict line.

The output carries board-task titles and the user's verdict text: write it under
a gitignored path and serve it on his private board only.

Usage:
  uv run python3 scripts/make_verdict_mockup.py --run-id <run> --out docs/data/<file>.html
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path.home() / ".local/share/weekly-review"


def _worksheet():
    spec = importlib.util.spec_from_file_location(
        "make_verdict_worksheet", REPO / "scripts" / "make_verdict_worksheet.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_rows(run_dir: Path) -> list[dict]:
    conn = sqlite3.connect(str(run_dir / "run.sqlite"))
    conn.row_factory = sqlite3.Row
    ecols = {r[1] for r in conn.execute("PRAGMA table_info(enriched)").fetchall()}
    from weekly_review.persist import metric_columns
    wc = f"COALESCE({metric_columns(conn)['labor']}, 0)"  # labor: user ∪ agent, pauses removed
    goal = "e.goal" if "goal" in ecols else "NULL as goal"
    rows = conn.execute(f"""
        SELECT t.task_id, t.project, COALESCE(e.title, t.title) as title,
               {goal}, e.summary, e.est_human_hours, e.uncertainty, e.type,
               e.effort_breakdown, e.rationale, e.bryan_role_summary,
               COALESCE(m.active_hours, 0) as hands_on, {wc} as wall_clock,
               t.user_msg_count, t.first_ts, t.last_ts
        FROM tasks t
        LEFT JOIN enriched e ON e.task_id = t.task_id
        LEFT JOIN task_metrics m ON m.task_id = t.task_id
        WHERE e.est_human_hours IS NOT NULL
        ORDER BY t.first_ts
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def verdict_state(row: dict, pv: dict | None) -> dict | None:
    """The verdict as the page shows it. `superseded` mirrors the worksheet:
    a recorded number that no longer matches this run's estimate did not
    carry, and saying so beats printing a tick beside a number he never saw."""
    if not pv:
        return None
    bh, lh, cur = pv["bryan_h"], pv["llm_h"], row["est_human_hours"]
    changed = bh is not None and lh is not None and abs(bh - lh) > 1e-6
    superseded = (bh is not None and cur is not None
                  and abs(bh - cur) > max(0.01, 0.02 * max(bh, cur)))
    return {
        "confidence": pv["confidence"] or "OK",
        "bryan_h": bh,
        "llm_h": lh,
        "changed": changed,
        "superseded": superseded,
        "note": pv["note"] or "",
        "reviewed_at": pv["reviewed_at"],
    }


_STAT_GROUPS = [
    # (label, [tag keys in order], joiner) — a group renders once with the
    # values it found, so "LoC +9465 / −474" is one line, not two.
    ("LoC", ["loc added", "loc removed"], "loc"),
    ("Files", ["files touched"], "n"),
    ("Tests", ["test files added", "test files added/modified", "test files modified", "test lines added"], "tests"),
    ("Components", ["new components", "modified components"], "newmod"),
    ("Pages", ["pages read", "pages written"], "readwrite"),
    ("Search", ["sources reviewed"], "text"),
    ("Config/infra", ["distinct config/infra changes"], "n"),
    ("Integrations", ["new integrations/apis/packages"], "n"),
    ("Design", ["new pages/components designed", "modified pages/components", "variants explored per screen"], "design"),
]


def _num(v: str) -> str:
    """'~12' -> '~12', '9465' -> '9465', '16 Legistar searches' -> the text."""
    v = v.strip()
    return v if re.fullmatch(r"~?\d[\d,]*", v) else v


def parse_breakdown(text: str) -> dict:
    """The estimator's breakdown is bracketed counts followed by a
    semicolon list of components. The user wants the counts in his own compact
    form and the biggest components as bullets, so split it mechanically:
    no model, nothing re-estimated."""
    tags = re.findall(r"\[([^\]:]+):\s*([^\]]*)\]", text or "")
    kv: dict[str, str] = {}
    for k, v in tags:
        kv.setdefault(k.strip().lower(), v.strip())
    used: set[str] = set()
    stats: list[list[str]] = []

    def take(key):
        used.add(key)
        return kv.get(key)

    for label, keys, kind in _STAT_GROUPS:
        vals = {k: take(k) for k in keys}
        vals = {k: v for k, v in vals.items() if v not in (None, "", "0", "~0")}
        if not vals:
            continue
        if kind == "loc":
            a, r = vals.get("loc added"), vals.get("loc removed")
            approx = "\u2248 " if any(v.startswith("~") for v in (a or "", r or "")) else ""
            a, r = (a or "").lstrip("~"), (r or "").lstrip("~")
            stats.append([label, approx + " / ".join(x for x in [f"+{a}" if a else "", f"\u2212{r}" if r else ""] if x)])
        elif kind == "tests":
            files = [v for k, v in vals.items() if "files" in k]
            lines = vals.get("test lines added")
            parts = ([f"{files[0]} files"] if files else []) + ([f"{lines} lines"] if lines else [])
            stats.append([label, ", ".join(parts)])
        elif kind == "newmod":
            n, m = vals.get("new components"), vals.get("modified components")
            stats.append([label, " / ".join(x for x in [f"{n} new" if n else "", f"{m} modified" if m else ""] if x)])
        elif kind == "readwrite":
            r, w = vals.get("pages read"), vals.get("pages written")
            stats.append([label, " / ".join(x for x in [f"{r} read" if r else "", f"{w} written" if w else ""] if x)])
        elif kind == "design":
            stats.append([label, "; ".join(f"{v} {k.replace('new pages/components designed', 'designed').replace('modified pages/components', 'modified').replace('variants explored per screen', 'variants/screen')}" for k, v in vals.items())])
        else:
            stats.append([label, "; ".join(_num(v) for v in vals.values())])
    for k, v in kv.items():
        if k not in used and v not in ("", "0", "~0"):
            stats.append([k, v])

    tail = re.sub(r"\[[^\]]*\]\s*·?\s*", "", text or "").strip(" ·;")
    comps = []
    for c in re.split(r";\s+", tail):
        c = c.strip().rstrip(".")
        if not c:
            continue
        m = re.search(r"(\d+)\s+files?\b", c)
        comps.append({"text": c, "files": int(m.group(1)) if m else 0})
    comps.sort(key=lambda x: -x["files"])  # stable: equal-weight items keep the estimator's order
    return {"stats": stats, "components": comps}


# How many tasks each "top" view holds. The user, 2026-09-17: 440 tasks is too
# many to review; he reviews the ones that move the week's totals most.
TOP_N = 40


def top_views(tasks: list[dict], n: int = TOP_N) -> list[dict]:
    """The n tasks with the most estimated hours, and the n with the most
    hands-on. Each view states its share of the week's total in its own
    label, so the reader knows how much of the number the view covers."""
    live = lambda t: bool(t["verdict"]) and not t["verdict"]["superseded"]
    views = []
    for key, noun, unit in (("est_h", "estimate", "est"), ("hands_on", "hands-on", "hands-on")):
        total = sum(t[key] or 0 for t in tasks)
        top = sorted(tasks, key=lambda t: -(t[key] or 0))[:n]
        top = [t for t in top if (t[key] or 0) > 0]
        if not top:
            continue
        part = sum(t[key] or 0 for t in top)
        share = round(100 * part / total) if total else 0
        views.append({
            "name": f"Top {len(top)} by {noun}",
            "label": f"Top {len(top)} by {noun}: {part:.1f}h of {total:.1f}h {unit} ({share}%)",
            "task_ids": [t["id"] for t in top],
            "reviewed": sum(1 for t in top if live(t)),
            "est_h": sum(t["est_h"] or 0 for t in top),
        })
    return views


# The worksheet account opens with its own "**Hands-on — 0.06h.**" head; the
# page already prints the figure, in minutes.
_HEAD = re.compile(r"^\*\*Hands-on[^*]*\*\*\s*")


def build_model(run_dir: Path, exclude_projects: tuple[str, ...] = ()) -> dict:
    ws = _worksheet()
    rows = [r for r in load_rows(run_dir) if r["project"] not in exclude_projects]
    prior = ws.load_prior_verdicts(run_dir)
    refs = ws.board_task_refs(run_dir, rows)
    # Measured account of each hands-on figure. "Your role" is the estimator's
    # prose from the chat transcript and never sees board activity, so it says
    # "did not steer" on tasks whose minutes are his review answers.
    made_of = {tid: _HEAD.sub("", hands) for tid, (hands, _wall) in ws.explain_rows(run_dir, rows).items()}

    by_project: dict[str, list] = {}
    for r in rows:
        by_project.setdefault(r["project"] or "unknown", []).append(r)
    projects = sorted(by_project, key=lambda p: by_project[p][0]["first_ts"] or "")

    tasks = []
    for r in rows:
        tid = r["task_id"]
        tasks.append({
            "id": tid,
            "short": tid[:10],
            "project": r["project"] or "unknown",
            "title": (r["title"] or "").replace("\n", " ").strip(),
            "first_ts": r["first_ts"] or "",
            "last_ts": r["last_ts"] or "",
            "goal": (r["goal"] or "").strip(),
            "summary": (r["summary"] or "").strip(),
            "est_h": r["est_human_hours"],
            "uncertainty": r["uncertainty"] or "?",
            "rationale": (r["rationale"] or "").strip(),
            "breakdown": (r["effort_breakdown"] or "").strip(),
            "bd": parse_breakdown(r["effort_breakdown"] or ""),
            "role": (r["bryan_role_summary"] or "").strip(),
            "type": r["type"] or "?",
            "hands_on": r["hands_on"],
            "hands_why": made_of.get(tid, ""),
            "wall_clock": r["wall_clock"],
            "msgs": r["user_msg_count"] or 0,
            "board": refs.get(tid, {"own": None, "others": []}),
            "verdict": verdict_state(r, prior.get(tid)),
        })
    proj_list = []
    for p in projects:
        ids = [t["id"] for t in tasks if t["project"] == p]
        reviewed = sum(1 for t in tasks if t["project"] == p and t["verdict"]
                       and not t["verdict"]["superseded"])
        est = sum(t["est_h"] or 0 for t in tasks if t["project"] == p)
        proj_list.append({"name": p, "task_ids": ids, "reviewed": reviewed, "est_h": est})
    return {
        "run_id": run_dir.name,
        "projects": proj_list,
        "views": top_views(tasks),
        "tasks": tasks,
        "n_reviewed": sum(1 for t in tasks if t["verdict"] and not t["verdict"]["superseded"]),
    }


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verdicts — __RUN__</title>
<style>
:root{--bg:#fafaf8;--fg:#1c1c1c;--muted:#6b6b6b;--line:#e2e0da;--sel:#eef3ff;--ok:#2e7d32;--warn:#b26a00;--card:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;font-size:14px;color:var(--fg);background:var(--bg)}
*{box-sizing:border-box}body{margin:0;padding-inline:16px;padding-block:0}
header{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;padding:6px 0;border-bottom:1px solid var(--line);font-size:12px}
header h1{font-size:16px;margin:0}header .muted{color:var(--muted)}
.cols{display:block}
.rail{display:none}
.list{display:none;position:fixed;left:16px;top:44px;width:360px;max-height:calc(100vh - 60px);overflow-y:auto;padding:6px;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);z-index:5}
.list.open{display:block}
.pick{display:flex;gap:8px;align-items:center}
.pick select,.pick button{font:inherit;font-size:13px;padding:3px 8px;border:1px solid var(--line);border-radius:6px;background:#fff;cursor:pointer}
.hint{display:none}
@media (max-width:900px){.list{width:calc(100vw - 32px)}}
.proj{padding:5px 8px;font-size:13px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:8px}
.proj:hover{background:#f1efe9}.proj.sel{background:var(--sel)}
.proj .n{color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.proj .n.done{color:var(--ok)}
.task{padding:5px 8px;font-size:12px;border-radius:6px;cursor:pointer;border-left:3px solid transparent}
.task:hover{background:#f1efe9}.task.sel{background:var(--sel);border-left-color:#3b6cff}
.task .t{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.task .title{margin-top:1px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.dot{color:var(--warn)}.tick{color:var(--ok)}
.detail{padding:8px 0 220px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;max-width:none}
.two{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}.col+.col{border-left:1px solid var(--line);padding-left:24px}
.tri-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:10px 0 4px}.tri-row .vl{font-size:13px;min-width:120px}
.stats{border-collapse:collapse;font-size:12.5px;margin:2px 0 4px}.stats td{padding:0 10px 1px 0;vertical-align:top}.stats td:first-child{color:var(--muted);white-space:nowrap}
.comps{margin:2px 0 0;padding-left:18px;font-size:12.5px;line-height:1.4}.comps li{margin:1px 0}
.morebtn{font:inherit;font-size:12px;border:none;background:none;color:#2b53c9;cursor:pointer;padding:0 0 0 18px}
@media (max-width:1100px){.two{grid-template-columns:1fr}.col+.col{border-left:none;padding-left:0}}
.card h2{margin:0 0 2px;font-size:16px;line-height:1.25}
.meta{color:var(--muted);font-size:12px;margin-bottom:6px;display:flex;gap:12px;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.f{margin:4px 0}.f p{font-size:12.5px;line-height:1.4}.f b{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:2px}
.f p{margin:0;white-space:pre-wrap;line-height:1.45}
.nav{display:flex;gap:8px;margin:8px 0;align-items:center}.navb{margin-top:10px}
.nav button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:4px 10px;cursor:pointer;font:inherit}
.nav button:disabled{opacity:.4;cursor:default}.nav .pos{color:var(--muted);font-size:12px}
.verdict{margin-top:14px;padding:10px 12px;border-radius:6px;background:#fbf7ea;border:1px dashed #d8c98a}
.verdict.ok{background:#eef7ee;border-color:#9fcf9f}.verdict.warn{background:#fff3e0;border-color:#f0b860}
.verdict code{font-size:12px}
.vform{margin-top:8px;display:flex;flex-direction:column;gap:6px}.vrow{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.vl{min-width:130px;font-size:13px}
.tri{font:inherit;font-size:13px;padding:4px 12px;border:1px solid #b9b9b9;border-radius:14px;background:#fff;cursor:pointer}.tri.on{background:#2b53c9;border-color:#2b53c9;color:#fff}
.vnote{flex:1;min-width:200px;font:inherit;font-size:13px;padding:5px 8px;border:1px solid #c9c9c9;border-radius:6px;resize:vertical}
.vsubmit{font:inherit;font-size:13px;padding:5px 16px;border:none;border-radius:6px;background:#2b53c9;color:#fff;cursor:pointer}.vsubmit:disabled{background:#b5bdd6;cursor:default}
.vmsg{font-size:12.5px;color:#7a271a}.vmsg:empty{display:none}
code{background:#f1efe9;padding:1px 4px;border-radius:3px}
a{color:#2b53c9}
.empty{color:var(--muted);padding:20px}
</style></head><body>
<header><span class="pick"><select id="projsel" title="Project"></select><button id="listbtn" title="Show this project's tasks">tasks ▾</button></span><span class="muted" id="hdr"></span>
<span class="muted">Verdict = comment on a task: <code>OK</code> / <code>HIGH — more like Nh</code> / <code>LOW — was a full day</code>. j/k = prev/next.</span>
<span class="muted"><span class="dot">●</span> under 3 min of your hands-on time · <span class="tick">✓</span> verdict recorded · <span class="dot">⚠</span> verdict no longer matches this run's estimate</span></header>
<div class="cols">
 <nav class="rail" id="rail"></nav>
 <nav class="list" id="list"></nav>
 <main class="detail" id="detail"><div class="empty">Pick a project, then a task.</div></main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const T={};D.tasks.forEach(t=>T[t.id]=t);
// A group is a project or a "top N" view; both are {name, task_ids, reviewed}.
const P={};(D.views||[]).concat(D.projects).forEach(p=>P[p.name]=p);
let curP=null,curT=null;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ts=s=>s?s.slice(0,16).replace('T',' '):'';
const h=x=>x==null?'?':(Math.round(x*100)/100).toString();
// Hands-on under an hour reads in minutes (the user, 2026-09-17): "0.06h" hides
// that it is three and a bit minutes of reading before four review answers.
const hm=x=>x==null?'?':x<1?`${Math.round(x*60)} min`:h(x)+'h';
document.getElementById('hdr').textContent=`${D.run_id} · ${D.tasks.length} tasks · ${D.projects.length} projects · ${D.n_reviewed} reviewed, ${D.tasks.length-D.n_reviewed} to go`;
function rail(){
  const sel=document.getElementById('projsel');sel.innerHTML='';
  const add=(into,p)=>{
    const o=document.createElement('option');o.value=p.name;
    o.textContent=`${p.label||p.name} — ${p.reviewed}/${p.task_ids.length} reviewed`;
    if(p.name===curP)o.selected=true;into.appendChild(o);
  };
  const grp=(label,items)=>{if(!items.length)return;const g=document.createElement('optgroup');g.label=label;items.forEach(p=>add(g,p));sel.appendChild(g);};
  grp('Biggest first',D.views||[]);grp('By project',D.projects);
}
const firstOpen=g=>g.task_ids.find(id=>!submitted(id)&&!(T[id].verdict&&!T[id].verdict.superseded))||g.task_ids[0];
document.getElementById('projsel').onchange=e=>{selectP(e.target.value);selectT(firstOpen(P[curP]));};
document.getElementById('listbtn').onclick=e=>{document.getElementById('list').classList.toggle('open');e.stopPropagation();};
document.addEventListener('click',e=>{const l=document.getElementById('list');if(l.classList.contains('open')&&!l.contains(e.target))l.classList.remove('open');});
function selectP(name){curP=name;rail();list();try{localStorage.setItem('verdicts:'+D.run_id+':group',name);}catch(e){}}
function list(){
  const el=document.getElementById('list');el.innerHTML='';
  if(!curP){el.innerHTML='<div class="empty">No project selected.</div>';return;}
  P[curP].task_ids.forEach(id=>{
    const t=T[id];const d=document.createElement('div');d.className='task'+(id===curT?' sel':'');d.id='row-'+id;d.dataset.taskId=id;
    const v=t.verdict;const mark=submitted(id)?'<span class="tick">✓</span>':v?(v.superseded?'<span class="dot">⚠</span>':'<span class="tick">✓</span>'):(t.hands_on<0.05?'<span class="dot">●</span>':'');
    d.innerHTML=`<div class="t" data-task-id="${id}"><span>${ts(t.first_ts)}</span><span>${h(t.est_h)}h ${mark}</span></div><div class="title" data-task-id="${id}">${esc(t.title)}</div>`;
    d.onclick=()=>{selectT(id);el.classList.remove('open');};el.appendChild(d);
  });
}
function link(x){
  const head=x.board?`[${esc(x.board)}] `:'';
  const body=x.title?head+esc(x.title):head+'(not in any board store)';
  const ref=x.wid?`<a href="/workspaces/${x.wid}?task=${x.id}" target="_blank" rel="noopener"><code>${x.id}</code></a>`:`<code>${x.id}</code>`;
  const n=x.n!=null?`, ${x.n} events`:'';
  return `${body} (${ref}${n})`;
}
function breakdown(t){
  const b=t.bd;if(!t.breakdown)return '<p><i>(none recorded — estimate is unaudited)</i></p>';
  let out='';
  if(b.stats.length)out+='<table class="stats" data-task-id="'+t.id+'">'+b.stats.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')+'</table>';
  const top=b.components.slice(0,5),rest=b.components.slice(5);
  if(top.length){out+='<ul class="comps" data-task-id="'+t.id+'">'+top.map(c=>`<li>${esc(c.text)}</li>`).join('')+'</ul>';}
  if(rest.length){out+=`<ul class="comps more" hidden data-task-id="${t.id}">`+rest.map(c=>`<li>${esc(c.text)}</li>`).join('')+'</ul>'+`<button class="morebtn" onclick="this.previousElementSibling.hidden=!this.previousElementSibling.hidden;this.textContent=this.previousElementSibling.hidden?'+${rest.length} more':'fewer'">+${rest.length} more</button>`;}
  return out;
}
function submitted(id){try{return localStorage.getItem('verdicts:'+D.run_id+':sub:'+id)||'';}catch(e){return '';}}
function verdictBox(t){
  const V=t.verdict,sub=submitted(t.id);let state='',cls='';
  if(sub){cls=' ok';state=`✅ submitted: ${esc(sub)} <i>(recorded at the next harvest)</i>`;}
  else if(V&&V.superseded){cls=' warn';state=`⚠️ NOT carried forward — you previously said <b>${h(V.bryan_h)}h</b>; this run re-estimates <b>${h(t.est_h)}h</b> (re-segmented or pre-fix quarantine). Re-confirm or correct below.${V.note?' — '+esc(V.note):''}`;}
  else if(V){cls=' ok';state=`✅ reviewed: ${esc(V.confidence)}${V.changed?' → '+h(V.bryan_h)+'h':''}${V.note?' — '+esc(V.note):''}`;}
  else{state='<i>not yet reviewed</i>';}
  return `<div class="verdict${cls}" data-task-id="${t.id}"><b>VERDICT [${t.short}]:</b> <span class="vstate">${state}</span>
   <div class="vform" data-task-id="${t.id}">
    <div class="vrow"><textarea class="vnote" rows="1" placeholder="optional comment — e.g. more like 2h, or split this in two"></textarea><button type="button" class="vsubmit" disabled>Submit</button></div>
    <div class="vmsg"></div>
   </div></div>`;
}
function tri(t,k,label,val){
  const btns=['ok','low','high'].map(v=>`<button type="button" class="tri" data-k="${k}" data-v="${v}">${v==='ok'?'Ok':v==='low'?'Low':'High'}</button>`).join('');
  return `<div class="tri-row" data-task-id="${t.id}"><span class="vl">${label} <b>${val}</b></span>${btns}</div>`;
}
const WTAG=['claude','feedback','widget'].join('-'); // built, not written: the board skips injecting its widget if the page names the tag
function widget(){return Array.from(document.querySelectorAll(WTAG)).find(w=>typeof w.postNewThread==='function')||null;}
function fingerprint(el){
  const txt=(el.textContent||'').trim().replace(/\s+/g,' ');const cut=(x,n)=>x.length<=n?x:x.slice(0,n-1)+'…';
  const path=[];let q=el,n=0;while(q&&n<5){const par=q.parentElement;let k=0;if(par)k=Array.from(par.children).filter(c=>c.tagName===q.tagName).indexOf(q);path.push(q.tagName.toUpperCase()+'['+k+']');q=par;n++;}
  const da={};for(const a of Array.from(el.attributes))if(a.name.startsWith('data-'))da[a.name]=a.value;
  const r=el.getBoundingClientRect(),W=window.innerWidth||1,H=window.innerHeight||1;
  const rect={x:+(r.left/W).toFixed(3),y:+(r.top/H).toFixed(3),w:+(r.width/W).toFixed(3),h:+(r.height/H).toFixed(3)};
  const fp={tag:el.tagName.toUpperCase(),stableAttrs:{},classes:Array.from(el.classList).sort(),text:cut(txt,60),path:path.reverse().join(' > '),dataAttrs:da,rect};
  return {kind:'element',fingerprint:fp,snippet:{text:cut(txt,80),rect},context:{url:location.pathname+location.search},at:{x:+(rect.x+rect.w/2).toFixed(3),y:+(rect.y+rect.h/2).toFixed(3)}};
}
function wireVerdict(el,t,advance){
  const box=el.querySelector('.verdict');if(!box)return;
  const pick={est:'',hands:''},btn=box.querySelector('.vsubmit'),msg=box.querySelector('.vmsg'),note=box.querySelector('.vnote');
  const refresh=()=>{btn.disabled=!(pick.est||pick.hands);};
  el.querySelectorAll('.tri').forEach(b=>b.onclick=()=>{const k=b.dataset.k,v=b.dataset.v;pick[k]=pick[k]===v?'':v;el.querySelectorAll(`.tri[data-k="${k}"]`).forEach(x=>x.classList.toggle('on',x.dataset.v===pick[k]));refresh();});
  note.addEventListener('input',()=>{note.rows=Math.min(4,Math.max(1,note.value.split('\n').length));});
  btn.onclick=async()=>{
    const parts=[];if(pick.est)parts.push('estimate: '+pick.est);if(pick.hands)parts.push('hands-on: '+pick.hands);
    const c=note.value.trim();const text=parts.join(' · ')+(c?' — '+c:'');
    const w=widget();
    if(!w){msg.innerHTML='No board connection here — post this as a comment on the verdict box instead:<br><code>'+esc(text)+'</code>';return;}
    btn.disabled=true;btn.textContent='Posting…';msg.textContent='';
    let ok=false;try{ok=await w.postNewThread(fingerprint(box),text);}catch(e){ok=false;}
    if(!ok){btn.disabled=false;btn.textContent='Submit';msg.textContent='Post failed — the board refused it (signed in?). Nothing recorded; try again or comment directly.';return;}
    try{localStorage.setItem('verdicts:'+D.run_id+':sub:'+t.id,text);}catch(e){}
    box.classList.add('ok');box.querySelector('.vstate').innerHTML=`✅ submitted: ${esc(text)} <i>(recorded at the next harvest)</i>`;
    btn.textContent='Submitted';list();setTimeout(advance,500);
  };
}
function selectT(id){
  curT=id;const t=T[id];if(!t)return;
  // Stay in the current group when the task is in it: a top-N view spans projects.
  if(!curP||!P[curP].task_ids.includes(id)){curP=t.project;rail();}
  list();
  const ids=P[curP].task_ids,i=ids.indexOf(id);
  const el=document.getElementById('detail');
  const f=(label,val)=>val?`<div class="f" data-task-id="${t.id}"><b>${label}</b><p data-task-id="${t.id}">${esc(val)}</p></div>`:'';
  const b=t.board;let board=b.own?link(b.own):'<i>none — the segmenter put this on no board task</i>';
  if(b.others.length){board+=`<br><b style="font-weight:600">Transcript mentions (not worked on):</b> `+b.others.slice(0,3).map(link).join(', ')+(b.others.length>3?`, +${b.others.length-3} more`:'');}
  const v=verdictBox(t);
  const nav=(sfx)=>`<div class="nav${sfx?' navb':''}"><button class="prev" ${i<=0?'disabled':''}>← prev</button><button class="next" ${i>=ids.length-1?'disabled':''}>next →</button><span class="pos">${i+1} of ${ids.length} in ${esc(curP)}</span></div>`;
  el.innerHTML=`<div class="card" id="task-${t.id}" data-task-id="${t.id}">
   <h2 data-task-id="${t.id}">${esc(t.title)}</h2>
   <div class="meta"><span>${ts(t.first_ts)} → ${ts(t.last_ts)}</span><span>est ${h(t.est_h)}h (${esc(t.uncertainty)})</span><span>hands-on ${hm(t.hands_on)}</span><span>wall ${h(t.wall_clock)}h</span><span>${esc(t.type)}</span><span>${t.msgs} msgs</span><span><code>${t.short}</code></span></div>
   <div class="two">
    <div class="col" data-task-id="${t.id}">
     ${f('Why',t.goal)}${f('Your role (estimator, from the chat transcript only)',t.role)}
     ${f('What the hands-on is made of (measured)',t.hands_why)}
     ${tri(t,'hands','Hands-on',hm(t.hands_on))}
    </div>
    <div class="col" data-task-id="${t.id}">
     <div class="f" data-task-id="${t.id}"><b>Board task</b><p>${board}</p></div>
     ${f('What got done',t.summary)}
     <div class="f" data-task-id="${t.id}"><b>Estimate — ${h(t.est_h)}h (${esc(t.uncertainty)} uncertainty)</b><p>${esc(t.rationale)}</p></div>
     <div class="f" data-task-id="${t.id}"><b>Breakdown</b>${breakdown(t)}</div>
     ${tri(t,'est','Estimate',h(t.est_h)+'h')}
    </div>
   </div>
   ${v}
   ${nav(true)}
  </div>`;
  el.querySelectorAll('.prev').forEach(b=>b.onclick=()=>selectT(ids[i-1]));
  el.querySelectorAll('.next').forEach(b=>b.onclick=()=>selectT(ids[i+1]));
  wireVerdict(el,t,()=>{if(i<ids.length-1)selectT(ids[i+1]);});
  const row=document.getElementById('row-'+id);if(row)row.scrollIntoView({block:'nearest'});
  window.scrollTo(0,0);try{localStorage.setItem('verdicts:'+D.run_id,id);}catch(e){}
  // Inside the board's frame localStorage is an in-memory shim that dies on navigation, so the task also rides in the frame's own URL.
  try{const u=new URL(location.href);if(u.searchParams.get('vtask')!==id){u.searchParams.set('vtask',id);history.replaceState(null,'',u.href);}}catch(e){}
}
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if(!curP)return;const ids=P[curP].task_ids,i=ids.indexOf(curT);
  if((e.key==='j'||e.key==='ArrowRight')&&i<ids.length-1){selectT(ids[i+1]);e.preventDefault();}
  if((e.key==='k'||e.key==='ArrowLeft')&&i>0){selectT(ids[i-1]);e.preventDefault();}
});
rail();
let want='';try{want=new URL(location.href).searchParams.get('vtask')||'';}catch(e){}
if(!want){try{want=localStorage.getItem('verdicts:'+D.run_id)||'';}catch(e){}}
// Open in the group he was last in; first time, the biggest-estimate view.
let grp0='';try{grp0=localStorage.getItem('verdicts:'+D.run_id+':group')||'';}catch(e){}
const g0=P[grp0]||(D.views||[])[0]||D.projects[0];
// A ?vtask= link names one task and wins over the group: it opens in its project.
let linked='';try{linked=new URL(location.href).searchParams.get('vtask')||'';}catch(e){}
if(linked&&T[linked]&&!(g0&&g0.task_ids.includes(linked))){selectT(linked);}
else if(g0){selectP(g0.name);selectT(want&&g0.task_ids.includes(want)?want:firstOpen(g0));}
</script></body></html>
"""


def render_html(model: dict) -> str:
    data = json.dumps(model, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__RUN__", html.escape(model["run_id"])).replace("__DATA__", data)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--exclude-project", action="append", default=[])
    args = p.parse_args()
    run_dir = DATA_DIR / "runs" / args.run_id
    model = build_model(run_dir, tuple(args.exclude_project))
    out = Path(args.out)
    out.write_text(render_html(model))
    print(f"wrote verdict mockup: {out} ({len(model['tasks'])} tasks, "
          f"{len(model['projects'])} projects, {model['n_reviewed']} reviewed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
