// Say Why lower third. Every pixel is a pure function of the goToTime timestamp and the
// action schedule; the timings and curves below are the ones in motion.json (a test holds
// them together). No timers, no randomness, no animation objects.

const CURVES = { out: [0.16, 1, 0.3, 1], exhale: [0.45, 0, 0.2, 1], inOut: [0.65, 0, 0.35, 1] };
const BUILD = { rule: 480, plate: 300, text: 360, textStart: 120, stagger: 80, rise: 14 };
const STOP = 500;

// Cubic-bezier easing (CSS order), solved by bisection so it is exact and history-free.
function bezier([x1, y1, x2, y2], x) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const at = (a, b, s) => 3 * a * s * (1 - s) * (1 - s) + 3 * b * s * s * (1 - s) + s * s * s;
  let lo = 0, hi = 1;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (at(x1, x2, mid) < x) lo = mid; else hi = mid;
  }
  return at(y1, y2, (lo + hi) / 2);
}
const ease = (name, ms, start, dur) => bezier(CURVES[name], (ms - start) / dur);
const esc = (s) => String(s ?? "");

class SayWhyLowerThird extends HTMLElement {
  constructor() {
    super();
    this._schedule = [];
    this._state = { name: "", role: "", title: "", accentColor: "#c8923a" };
    this._ms = 0;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host{position:absolute;inset:0;display:block;overflow:hidden}
      .plate{position:absolute;background:#1a1412}
      .rule{position:absolute;background:#c8923a}
      .tx{position:absolute;white-space:nowrap;overflow:hidden}
      .name{color:#faf8f5;font-family:'EB Garamond','Source Serif 4',Georgia,serif;font-weight:600}
      .role{color:#a89e93;font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;text-transform:uppercase;letter-spacing:.14em}
      .title{color:#c8923a;font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;text-transform:uppercase;letter-spacing:.14em}
    </style>
    <div class="plate" data-layer="plate"></div><div class="rule" data-layer="rule"></div>
    <div class="tx name" data-layer="name"></div><div class="tx role" data-layer="role"></div><div class="tx title" data-layer="title"></div>`;
    for (const k of ["plate", "rule", "name", "role", "title"]) this["_" + k] = root.querySelector("." + k);
  }
  async load(p) { this._state = { ...this._state, ...(p?.data ?? {}) }; this._ms = 0; this._setFrame(0); return { statusCode: 200 }; }
  async dispose() { return { statusCode: 200 }; }
  async playAction() { return { statusCode: 200 }; }
  async stopAction() { return { statusCode: 200 }; }
  async customAction() { return { statusCode: 200 }; }
  async updateAction(p) {
    this._state = { ...this._state, ...(p?.data ?? {}) }; // the baseline every seek rebuilds from
    this._setFrame(this._ms, this._schedule);
    return { statusCode: 200 };
  }
  async setActionsSchedule(p) {
    this._schedule = (p?.schedule ?? []).slice().sort((a, b) => a.timestamp - b.timestamp);
    return { statusCode: 200 };
  }
  async goToTime(p) {
    const seconds = p.timestamp / 1000; // the host sends milliseconds
    this._setFrame(seconds * 1000, this._schedule); // replays the stored schedule on every seek
    return { statusCode: 200 };
  }

  _setFrame(ms, schedule = this._schedule) {
    this._ms = ms;
    const due = schedule.filter((e) => e.timestamp <= ms);
    const data = { ...this._state };
    for (const e of due) if (e.action.type === "updateAction") Object.assign(data, e.action.params?.data);
    const play = due.filter((e) => e.action.type === "playAction").pop();
    const stop = play && due.find((e) => e.action.type === "stopAction" && e.timestamp > play.timestamp);
    const W = this.clientWidth || window.innerWidth, H = this.clientHeight || window.innerHeight;
    const port = H > W;
    const s = port ? W / 1080 : H / 1080;
    const hasTitle = esc(data.title) !== "";
    const pad = 28 * s, ruleW = 8 * s;
    const pw = port ? W * 0.88 : 780 * s;
    const lines = [{ k: "title", h: 22 * s, on: hasTitle }, { k: "name", h: 64 * s, on: true }, { k: "role", h: 28 * s, on: true }]
      .filter((l) => l.on);
    const gap = 8 * s, ph = lines.reduce((a, l) => a + l.h + gap, 0) - gap + pad * 2;
    const left = port ? W * 0.06 : W * 0.07;
    const bottom = port ? H * 0.74 : H * 0.88; // portrait sits above the platform UI band
    const top = bottom - ph;

    let t = 0, a = 0, ruleP = 0, rise = [0, 0, 0];
    let show = Boolean(play);
    if (show) {
      t = ms - play.timestamp;
      a = ease("inOut", t, 0, BUILD.plate);
      ruleP = ease("out", t, 0, BUILD.rule);
    }
    const lineP = (i) => ease("out", t, BUILD.textStart + i * BUILD.stagger, BUILD.text);
    let textA = [0, 1, 2].map(lineP);
    rise = textA.map((p) => (1 - p) * BUILD.rise * s);
    if (stop) {
      const u = ms - stop.timestamp;
      const fade = 1 - ease("inOut", u, 0, STOP);
      a = Math.min(a, fade);
      textA = textA.map((p) => Math.min(p, fade));
      ruleP = Math.min(ruleP, 1 - ease("exhale", u, 0, STOP));
    }
    const px = (n) => n.toFixed(3) + "px";
    Object.assign(this._plate.style, { left: px(left), top: px(top), width: px(pw), height: px(ph), opacity: a });
    Object.assign(this._rule.style, { left: px(left), top: px(top), width: px(ruleW), height: px(ph * ruleP), background: esc(data.accentColor) || "#c8923a", opacity: show ? 1 : 0 });
    let y = top + pad;
    const order = { title: 0, name: 1, role: 2 };
    for (const l of lines) {
      const el = this["_" + l.k], i = order[l.k];
      el.textContent = esc(data[l.k]);
      Object.assign(el.style, {
        display: "block", left: px(left + ruleW + pad), top: px(y + rise[i]), width: px(pw - ruleW - pad * 2), height: px(l.h),
        lineHeight: px(l.h), fontSize: px(l.k === "name" ? 52 * s : l.k === "role" ? 20 * s : 17 * s), opacity: textA[i],
      });
      y += l.h + gap;
    }
    if (!hasTitle) { this._title.textContent = ""; this._title.style.display = "none"; }
  }
}
export default SayWhyLowerThird;
