"""rpstills.review: the review sheet, one vanilla HTML file per shoot.

Everything the sheet needs is embedded (index subset, clusters, proposal);
the thumbnails are the proxies next to it, so the file opens from the
shoot folder with no server. Keep and reject toggles and the name per run
persist in localStorage and export as selects.json through a download,
which later stages read. The proposal is never changed by the sheet.
"""

import html
import json
import os

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#141516;--fg:#e8e6e1;--mut:#9a9a94;--card:#1e1f21;--line:#2c2d30;--keep:#7fc97f;--drop:#c96b6b;--pick:#e9c46a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0 12px 0 0;font-weight:600}
button{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px;cursor:pointer}
button:hover{border-color:var(--mut)}label.tog{display:flex;gap:6px;align-items:center;color:var(--mut)}
main{padding:12px 16px}
.seg{margin:18px 0 8px;color:var(--mut);font-size:13px;text-transform:uppercase;letter-spacing:.06em}
.run{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0}
.run.hide{display:none}
.runhead{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.runhead .cls{font-weight:600;text-transform:capitalize}.runhead .meta{color:var(--mut)}
.runhead input{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;min-width:200px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px}
.fr{position:relative;border:3px solid transparent;border-radius:8px;overflow:hidden;background:#000;cursor:pointer;aspect-ratio:1/1}
.fr img{width:100%;height:100%;object-fit:contain;display:block;opacity:.92}
.fr.pick{border-color:var(--pick)}.fr.keep{border-color:var(--keep)}.fr.keep img{opacity:1}.fr.drop img{opacity:.35}
.fr .tag{position:absolute;left:6px;bottom:6px;background:rgba(0,0,0,.65);padding:2px 6px;border-radius:4px;font-size:11px;color:#fff}
.fr .sc{position:absolute;right:6px;top:6px;background:rgba(0,0,0,.65);padding:2px 6px;border-radius:4px;font-size:11px;color:#fff}
.fr .warn{position:absolute;left:6px;top:6px;background:rgba(201,107,107,.85);padding:2px 6px;border-radius:4px;font-size:11px;color:#fff}
.fr.onlyhide{display:none}
.legend{color:var(--mut);font-size:12px}.legend b{color:var(--fg)}
footer{padding:20px 16px;color:var(--mut);font-size:12px}
</style></head><body>
<header><h1>__TITLE__</h1>
<span id="count" class="legend"></span>
<label class="tog"><input type="checkbox" id="onlypicks"> proposed and kept only</label>
<button id="reset">Reset to proposal</button>
<button id="export">Export selects.json</button>
<span class="legend"><b style="color:var(--pick)">yellow</b> proposed · <b style="color:var(--keep)">green</b> kept · click to keep, click again to drop, third click clears</span>
</header>
<main id="main"></main>
<footer>Scores compare only within a run. Names are yours to fill; the pipeline never guesses one. selects.json goes beside this file.</footer>
<script>
const DATA = __DATA__;
const KEY = "rpstills:" + DATA.session;
let state = load();
function load(){ try{ return JSON.parse(localStorage.getItem(KEY)) || {marks:{}, names:{}}; }catch(e){ return {marks:{}, names:{}}; } }
function save(){ try{ localStorage.setItem(KEY, JSON.stringify(state)); }catch(e){} }
const proposed = new Set(); DATA.proposal.forEach(p => p.picks.forEach(f => proposed.add(f)));
const frames = Object.fromEntries(DATA.frames.map(f => [f.id, f]));
const main = document.getElementById("main");
function statusOf(id){ return state.marks[id] || (proposed.has(id) ? "pick" : ""); }
function kept(){ const out=[]; for (const id in frames){ const s=statusOf(id); if (s==="keep"||s==="pick") out.push(id);} return out; }
function render(){
  main.innerHTML = "";
  let seg = null;
  for (const p of DATA.proposal){
    const run = DATA.runs[p.run];
    if (run.segment !== seg){ seg = run.segment; const s = DATA.segments[seg];
      const h = document.createElement("div"); h.className = "seg";
      h.textContent = `${seg}: ${s.start} to ${s.end}, ${s.frames} frames`; main.appendChild(h); }
    const box = document.createElement("section"); box.className = "run"; box.dataset.run = p.run;
    const head = document.createElement("div"); head.className = "runhead";
    head.innerHTML = `<span class="cls">${run.class}</span><span class="meta">${p.run} · ${run.frames.length} frames · ${(run.start||"").slice(11,19)} to ${(run.end||"").slice(11,19)} · ${p.picks.length} proposed</span>`;
    const name = document.createElement("input"); name.placeholder = run.class === "solo" ? "name (optional)" : "label (optional)";
    name.value = state.names[p.run] || ""; name.addEventListener("input", () => { state.names[p.run] = name.value; save(); });
    head.appendChild(name); box.appendChild(head);
    const grid = document.createElement("div"); grid.className = "grid";
    for (const id of p.ranked){
      const f = frames[id]; if (!f) continue;
      const el = document.createElement("div"); el.className = "fr " + statusOf(id); el.dataset.id = id;
      const sc = f.score != null ? f.score.toFixed(2) : "";
      el.innerHTML = `<img loading="lazy" src="${f.proxy}" alt="${id}"><span class="tag">${id}</span>` +
        (sc ? `<span class="sc">${sc}</span>` : "") + (f.eyes === 0 ? `<span class="warn">eyes</span>` : "") +
        (f.faces === 0 ? `<span class="warn">no face</span>` : "");
      el.addEventListener("click", () => { const cur = state.marks[id];
        state.marks[id] = cur === "keep" ? "drop" : cur === "drop" ? undefined : "keep";
        if (state.marks[id] === undefined) delete state.marks[id]; save(); el.className = "fr " + statusOf(id); applyFilter(); count(); });
      grid.appendChild(el);
    }
    box.appendChild(grid); main.appendChild(box);
  }
  applyFilter(); count();
}
function applyFilter(){ const only = document.getElementById("onlypicks").checked;
  document.querySelectorAll(".fr").forEach(el => { const s = statusOf(el.dataset.id); el.classList.toggle("onlyhide", only && !(s==="pick"||s==="keep")); });
  document.querySelectorAll(".run").forEach(r => { r.classList.toggle("hide", only && !r.querySelector(".fr:not(.onlyhide)")); }); }
function count(){ const k = kept().length; document.getElementById("count").textContent = `${k} kept of ${DATA.frames.length} frames · ${Object.keys(state.marks).length} edits`; }
document.getElementById("onlypicks").addEventListener("change", applyFilter);
document.getElementById("reset").addEventListener("click", () => { if (confirm("Drop every edit and go back to the proposal?")) { state.marks = {}; save(); render(); } });
document.getElementById("export").addEventListener("click", () => {
  const runs = DATA.proposal.map(p => ({run: p.run, class: DATA.runs[p.run].class, name: state.names[p.run] || null,
    keep: p.ranked.filter(id => { const s = statusOf(id); return s === "keep" || s === "pick"; }),
    dropped_from_proposal: p.picks.filter(id => state.marks[id] === "drop")}));
  const doc = {session: DATA.session, exported: new Date().toISOString(), source: DATA.source, runs,
    keep: runs.flatMap(r => r.keep)};
  const blob = new Blob([JSON.stringify(doc, null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "selects.json"; a.click(); });
render();
</script></body></html>
"""


def build(out_dir, session, source, rows, clusters, cull, title=None):
    scores = cull["scores"]
    frames = []
    for r in rows:
        if r.get("error"):
            continue
        s = scores.get(r["id"], {})
        frames.append({"id": r["id"], "proxy": r["proxy"], "time": r.get("time"),
                       "score": s.get("score"), "eyes": s.get("eyes"), "faces": len(r.get("faces") or [])})
    data = {"session": session, "source": source, "frames": frames,
            "segments": {s["id"]: {"start": s["start"], "end": s["end"], "frames": len(s["frames"])} for s in clusters["segments"]},
            "runs": {r["id"]: r for r in clusters["runs"]},
            "proposal": cull["proposal"]}
    page = (TEMPLATE.replace("__TITLE__", html.escape(title or f"Review: {session}"))
            .replace("__DATA__", json.dumps(data, sort_keys=True).replace("</", "<\\/")))
    path = os.path.join(out_dir, "review.html")
    with open(path, "w") as f:
        f.write(page)
    return path
