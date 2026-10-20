// Hermetic tests for ograf-render: a fixture template written here, neutral test colours,
// nothing from Blackmagic. Run: node --test tools/ograf-render/test.mjs
// Skips with a message where Chrome or ffmpeg is missing. The overlay-check case also
// needs numpy and a resolve_workflow.py that has the overlay-check command
// (RESOLVE_WORKFLOW overrides the path).

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(HERE, "ograf-render.mjs");
const WORKFLOW = process.env.RESOLVE_WORKFLOW || join(HERE, "..", "..", "production", "resolve_workflow.py");

const have = (cmd, args) => spawnSync(cmd, args, { stdio: "ignore" }).status === 0;
const chrome = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
  .concat(process.env.CHROME || []).some((c) => (c.includes("/") ? existsSync(c) : have("which", [c])));
const skip = !chrome ? "Chrome not found" : !have("ffmpeg", ["-version"]) ? "ffmpeg not found" : false;

// A bar that slides and fades in over 400 ms of play, holds, then fades out from the
// scheduled stopAction. Every value is a pure function of the timestamp.
const graphic = (body) => `
class Fixture extends HTMLElement {
  constructor() {
    super();
    this._schedule = [];
    this._data = {};
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = '<style>:host{position:absolute;inset:0;display:block}' +
      '.bar{position:absolute;left:40px;top:60px;width:200px;height:50px;border-radius:12px;background:#2a8f5c}' +
      '.t{position:absolute;left:60px;top:70px;color:#f2f2f2;font:bold 24px Helvetica,Arial,sans-serif}</style>' +
      '<div class="bar" data-layer="bar"></div><div class="t" data-layer="title"></div>';
    this._bar = root.querySelector(".bar");
    this._t = root.querySelector(".t");
  }
  async load(p) { this._data = { ...p.data }; this._t.textContent = this._data.title ?? ""; return { statusCode: 200 }; }
  async dispose() { return { statusCode: 200 }; }
  async playAction() { return { statusCode: 200 }; }
  async stopAction() { return { statusCode: 200 }; }
  async customAction() { return { statusCode: 200 }; }
  async updateAction(p) { this._data = { ...this._data, ...p.data }; this._t.textContent = this._data.title ?? ""; return { statusCode: 200 }; }
  async setActionsSchedule(p) { this._schedule = p.schedule; return { statusCode: 200 }; }
  async goToTime(p) {
    const ms = p.timestamp;
    const stop = this._schedule.find((e) => e.action.type === "stopAction")?.timestamp ?? Infinity;
    let a = Math.min(ms / 400, 1);
    if (ms >= stop) a = 1 - Math.min((ms - stop) / 400, 1);
    ${body}
    for (const el of [this._bar, this._t]) { el.style.opacity = String(a); el.style.transform = "translateX(" + (1 - a) * -30 + "px)"; }
    return { statusCode: 200 };
  }
}
export default Fixture;
`;

const manifest = (extra = {}) => ({
  $schema: "https://ograf.ebu.io/v1/specification/json-schemas/graphics/schema.json",
  id: "fixture", name: "Fixture", main: "fixture.js", supportsRealTime: false, supportsNonRealTime: true,
  actionDurations: [{ type: "playAction", duration: 400 }, { type: "stopAction", duration: 400 }],
  schema: { type: "object", properties: { title: { type: "string", default: "TEST" } } },
  v_bmd: { duration: 1.5 }, ...extra,
});

describe("ograf-render", { skip }, () => {
  let work;
  const dirs = {};
  before(() => {
    work = mkdtempSync(join(tmpdir(), "ograf-test-"));
    const make = (name, js, man) => {
      const d = join(work, name);
      mkdirSync(d);
      writeFileSync(join(d, "fixture.js"), js);
      writeFileSync(join(d, "fixture.ograf.json"), JSON.stringify(man));
      dirs[name] = join(d, "fixture.ograf.json");
    };
    make("good", graphic(""), manifest());
    make("random", graphic("a = Math.min(1, Math.max(0, a * (0.9 + Math.random() * 0.1)));"), manifest());
    make("nonrt", graphic(""), manifest({ supportsNonRealTime: false }));
  });
  after(() => rmSync(work, { recursive: true, force: true }));

  const render = (name, out, ...extra) => spawnSync("node", [SCRIPT, dirs[name], "--out", join(work, out), "--size", "320x180", "--keep-frames", ...extra], { encoding: "utf8" });
  const report = (out) => JSON.parse(readFileSync(join(work, out, "report.json"), "utf8"));

  it("renders a ProRes 4444 master that overlay-check passes", (t) => {
    const r = render("good", "a");
    assert.equal(r.status, 0, r.stdout + r.stderr);
    const mov = join(work, "a", "fixture.mov");
    const probe = spawnSync("ffprobe", ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,profile,pix_fmt,color_range,color_space", "-of", "json", mov], { encoding: "utf8" });
    const s = JSON.parse(probe.stdout).streams[0];
    assert.equal(s.codec_name, "prores");
    assert.equal(s.profile, "4444");
    assert.match(s.pix_fmt, /^yuva444p1[02]le$/); // the decoder reports ProRes 4444 as 12-bit
    assert.equal(s.color_range, "tv");
    assert.equal(s.color_space, "bt709");
    const help = spawnSync("python3", [WORKFLOW, "overlay-check", "--help"], { encoding: "utf8" });
    if (help.status !== 0 || !have("python3", ["-c", "import numpy"])) return t.skip("overlay-check or numpy not available");
    const o = spawnSync("python3", [WORKFLOW, "overlay-check", mov, "--fps", "24000/1001"], { encoding: "utf8" });
    assert.equal(o.status, 0, o.stdout + o.stderr);
  });

  it("writes boxes for data-layer elements inside the shadow root", () => {
    const b = JSON.parse(readFileSync(join(work, "a", "boxes.json"), "utf8"));
    const layers = b.frames["20"].map((x) => x.layer).sort();
    assert.deepEqual(layers, ["bar", "title"]);
    const bar = b.frames["20"].find((x) => x.layer === "bar");
    assert.equal(Math.round(bar.w), 200);
  });

  it("gives identical per-frame hashes on two runs", () => {
    const r = render("good", "b");
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.deepEqual(report("b").hashes, report("a").hashes);
  });

  it("gives shuffled-order frames equal to sequential ones", () => {
    const r = render("good", "c", "--order", "shuffled", "--no-video");
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.deepEqual(report("c").hashes, report("a").hashes);
  });

  it("fails determinism for a template that calls Math.random() in goToTime", () => {
    const r = render("random", "d", "--no-video");
    assert.equal(r.status, 1, r.stdout + r.stderr);
    assert.equal(report("d").checks.determinism.pass, false);
  });

  it("refuses a template without supportsNonRealTime", () => {
    const r = render("nonrt", "e");
    assert.equal(r.status, 2);
    assert.match(r.stderr, /supportsNonRealTime/);
    assert.equal(existsSync(join(work, "e", "fixture.mov")), false);
  });

  it("flags a font that is not installed", () => {
    assert.ok(report("a").fonts.some((f) => f.family === "Helvetica"), "the fixture names Helvetica");
    const d = join(work, "font");
    mkdirSync(d);
    writeFileSync(join(d, "fixture.js"), graphic("").replace("Helvetica,Arial,sans-serif", "ZzNoSuchFace9,sans-serif"));
    writeFileSync(join(d, "fixture.ograf.json"), JSON.stringify(manifest()));
    dirs.font = join(d, "fixture.ograf.json");
    const r = render("font", "f", "--no-video");
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.deepEqual(report("f").checks.fonts.missing, ["ZzNoSuchFace9"]);
    assert.equal(render("font", "g", "--no-video", "--strict-fonts").status, 1);
  });
});
