// Hook card. Every pixel is a pure function of the goToTime timestamp and the action
// schedule; the timings and curves below are the ones in motion.json (a test holds them
// together). No timers, no randomness, no animation objects. The text is whatever the
// editor types into the two slots; the template carries only placeholder lines.

const CURVES = { out: [0.16, 1, 0.3, 1], exhale: [0.45, 0, 0.2, 1], inOut: [0.65, 0, 0.35, 1] };
const BUILD = { rule: 250, plate: 167, text: 208, textStart: 42, stagger: 42, rise: 14 };
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

class HookCard extends HTMLElement {
  constructor() {
    super();
    this._schedule = [];
    this._state = { line1: "", line2: "", accentColor: "#c8923a" };
    this._ms = 0;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host{position:absolute;inset:0;display:block;overflow:hidden}
      .plate{position:absolute;background:#1a1412}
      .rule{position:absolute;background:#c8923a}
      .tx,.m{font-family:'EB Garamond','Source Serif 4',Georgia,serif;font-weight:700;text-transform:uppercase;letter-spacing:.02em;white-space:nowrap}
      .tx{position:absolute;overflow:hidden;color:#faf8f5}
      .m{position:absolute;visibility:hidden}
    </style>
    <div class="plate" data-layer="plate"></div><div class="rule" data-layer="rule"></div>
    <div class="tx line1" data-layer="line1"></div><div class="tx line2" data-layer="line2"></div><span class="m"></span>`;
    for (const k of ["plate", "rule", "line1", "line2", "m"]) this["_" + k] = root.querySelector("." + k);
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
    // One plate width for both shapes: 8% side gaps clear the 6% portrait and 5% square margins.
    const pw = W * 0.84, left = (W - pw) / 2;
    const pad = 36 * s, ruleW = 8 * s, lh = 88 * s, gap = 8 * s;
    const texts = [esc(data.line1).trim(), esc(data.line2).trim()];
    const lines = [0, 1].filter((i) => i === 0 || texts[i] !== "");
    const ph = lines.length * lh + (lines.length - 1) * gap + pad * 2;
    const top = (port ? H * 0.30 : H * 0.5) - ph / 2; // portrait sits in the band between the top and bottom UI margins
    const tw = pw - ruleW - pad * 2;
    const room = tw * 0.97; // a little slack so the longest line never meets the clip edge

    // Shrink both lines together until the longer one fits the plate.
    const base = 76 * s;
    this._m.style.fontSize = base + "px";
    let widest = 0;
    for (const i of lines) { this._m.textContent = texts[i]; widest = Math.max(widest, this._m.getBoundingClientRect().width); }
    const fit = widest > room ? base * room / widest : base;

    let t = 0, a = 0, ruleP = 0;
    const show = Boolean(play);
    if (show) {
      t = ms - play.timestamp;
      a = ease("inOut", t, 0, BUILD.plate);
      ruleP = ease("out", t, 0, BUILD.rule);
    }
    let textA = [0, 1].map((i) => ease("out", t, BUILD.textStart + i * BUILD.stagger, BUILD.text));
    const rise = textA.map((p) => (1 - p) * BUILD.rise * s);
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
    for (const i of [0, 1]) {
      const el = this["_line" + (i + 1)];
      if (!lines.includes(i)) { el.textContent = ""; el.style.display = "none"; continue; }
      el.textContent = texts[i];
      Object.assign(el.style, {
        display: "block", left: px(left + ruleW + pad), top: px(y + rise[i]), width: px(tw), height: px(lh),
        lineHeight: px(lh), fontSize: px(fit), opacity: textA[i],
      });
      y += lh + gap;
    }
  }
}
export default HookCard;
