// Tests for the Say Why lower third template: lint, motion spec, a ProRes 4444 render per
// sample data set and size, then checks on alpha, length, easing, cut points, safe areas and
// contrast, read from the decoded master and from boxes.json. Neutral sample names only.
// Run: node --test tools/ograf-render/test-say-why-lower-third.mjs
// Skips with a message where Chrome or ffmpeg is missing; the motionlint case needs python3.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIR = join(HERE, "templates", "say-why-lower-third");
const MANIFEST = join(DIR, "say-why-lower-third.ograf.json");
const SCRIPT = join(HERE, "ograf-render.mjs");
const PRODUCTION = join(HERE, "..", "..", "production");

const have = (cmd, args) => spawnSync(cmd, args, { stdio: "ignore" }).status === 0;
const chrome = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
  .concat(process.env.CHROME || []).some((c) => (c.includes("/") ? existsSync(c) : have("which", [c])));
const skip = !chrome ? "Chrome not found" : !have("ffmpeg", ["-version"]) ? "ffmpeg not found" : false;

const spec = JSON.parse(readFileSync(join(DIR, "motion.json"), "utf8"));
const FRAME_MS = 1001 / 24;
const INK = [0x1a, 0x14, 0x12];

const DATA = {
  nameRole: { name: "Alex Example", role: "Guest" },
  withTitle: { name: "Sam Sample", role: "Host", title: "Episode title" },
  long: { name: "Alexandria Exampleton-Samplewood the Third of Nowhere", role: "Director of Everything That Needs a Very Long Role Line", title: "A deliberately overlong title line that cannot fit on one line" },
};
const RUNS = [
  ["nameRole", 480, 270], ["withTitle", 480, 270], ["long", 480, 270], ["withTitle", 270, 480], ["long", 270, 480],
];

// CSS cubic-bezier, solved by bisection (the same maths as the template, written separately).
function bezier([x1, y1, x2, y2], x) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const at = (a, b, s) => 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3;
  let lo = 0, hi = 1;
  for (let i = 0; i < 60; i++) { const m = (lo + hi) / 2; if (at(x1, x2, m) < x) lo = m; else hi = m; }
  return at(y1, y2, (lo + hi) / 2);
}
const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
  .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

describe("Say Why lower third", () => {
  it("passes motionlint, and the template's curves are the spec's", (t) => {
    const js = readFileSync(join(DIR, "say-why-lower-third.js"), "utf8");
    const curves = JSON.parse(js.match(/const CURVES = (\{.*\});/)[1].replace(/(\w+):/g, '"$1":'));
    assert.deepEqual(curves, spec.curves);
    // The template's millisecond timings must sit within a frame of the spec's frame counts.
    const B = JSON.parse(js.match(/const BUILD = (\{.*\});/)[1].replace(/(\w+):/g, '"$1":'));
    const tw = (i) => spec.segments[0].tweens[i];
    const close = (ms, frames, what) => assert.ok(Math.abs(ms - frames * FRAME_MS) <= FRAME_MS, `${what}: ${ms} ms vs ${frames} frames`);
    close(B.rule, tw(0).frames, "rule"); close(B.plate, tw(1).frames, "plate");
    close(B.text, tw(2).frames, "text"); close(B.textStart, tw(2).start, "text start"); close(B.stagger, tw(2).stagger, "stagger");
    close(Number(js.match(/const STOP = (\d+)/)[1]), spec.segments[2].tweens[0].frames, "stop");
    assert.doesNotMatch(js, /Math\.random|setTimeout|setInterval|requestAnimationFrame|new Date|\.animate\(/);
    const r = spawnSync("python3", ["-m", "rpresolve.motionlint", join(DIR, "motion.json")], { cwd: PRODUCTION, encoding: "utf8" });
    if (r.error) return t.skip("python3 not available");
    assert.equal(r.status, 0, r.stdout + r.stderr);
  });

  it("declares a manifest Resolve accepts", () => {
    const m = JSON.parse(readFileSync(MANIFEST, "utf8"));
    assert.equal(m.supportsNonRealTime, true);
    assert.deepEqual(Object.keys(m.schema.properties), ["name", "role", "title", "accentColor"]);
    for (const p of Object.values(m.schema.properties)) assert.ok(p.title && "default" in p);
    const play = m.actionDurations.find((d) => d.type === "playAction").duration;
    assert.ok(Math.abs(play - spec.segments[0].frames * FRAME_MS) < FRAME_MS, "play length matches the build segment");
    assert.equal(Math.round(m.v_bmd.duration * 24000 / 1001), spec.length);
    const lint = process.env.OGRAF_LINT || "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OGraf HTML Templates/Scripts/lint-ograf.py";
    if (existsSync(lint)) {
      const r = spawnSync("python3", [lint, DIR], { encoding: "utf8" });
      assert.equal(r.status, 0, r.stdout + r.stderr);
      assert.match(r.stdout, /0 error\(s\), 0 warning\(s\)/);
    }
  });

  describe("renders", { skip }, () => {
    let work;
    const out = new Map();
    before(() => {
      work = mkdtempSync(join(tmpdir(), "say-why-lt-"));
      for (const [set, w, h] of RUNS) {
        const dir = join(work, `${set}-${w}x${h}`);
        const r = spawnSync("node", [SCRIPT, MANIFEST, "--out", dir, "--size", `${w}x${h}`, "--data", JSON.stringify(DATA[set])], { encoding: "utf8" });
        assert.equal(r.status, 0, `${set} ${w}x${h}: ${r.stdout}${r.stderr}`);
        const boxes = JSON.parse(readFileSync(join(dir, "boxes.json"), "utf8")).frames;
        const raw = spawnSync("ffmpeg", ["-v", "error", "-i", join(dir, "say-why-lower-third.mov"),
          "-vf", "scale=in_range=tv:in_color_matrix=bt709:out_range=pc,format=rgba", "-f", "rawvideo", "-"], { maxBuffer: 1 << 29 });
        assert.equal(raw.status, 0, String(raw.stderr));
        out.set(`${set}-${w}x${h}`, { set, w, h, dir, boxes, raw: raw.stdout, frames: raw.stdout.length / (w * h * 4) });
      }
    });
    after(() => work && rmSync(work, { recursive: true, force: true }));

    const each = (fn) => () => { for (const run of out.values()) fn(run); };
    const px = (run, f, x, y) => { const o = ((f * run.h + y) * run.w + x) * 4; return [...run.raw.subarray(o, o + 4)]; };
    const layer = (run, f, name) => run.boxes[f].find((l) => l.layer === name);
    const bbox = (run, f) => {
      let x0 = run.w, y0 = run.h, x1 = -1, y1 = -1;
      for (let y = 0; y < run.h; y++) for (let x = 0; x < run.w; x++) {
        if (px(run, f, x, y)[3] > 0) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      }
      return x1 < 0 ? null : { x0, y0, x1, y1 };
    };

    it("is ProRes 4444 with alpha and the spec's length", each((run) => {
      const p = JSON.parse(spawnSync("ffprobe", ["-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=codec_name,profile,pix_fmt,nb_read_frames,width,height", "-of", "json", join(run.dir, "say-why-lower-third.mov")], { encoding: "utf8" }).stdout).streams[0];
      assert.equal(p.codec_name, "prores");
      assert.equal(p.profile, "4444");
      assert.match(p.pix_fmt, /^yuva444p1[02]le$/);
      assert.equal(Number(p.nb_read_frames), spec.length);
      assert.equal(run.frames, spec.length);
      assert.deepEqual([p.width, p.height], [run.w, run.h]);
    }));

    it("is fully clear on the first and last frame, with no partial frame at either cut", each((run) => {
      assert.equal(bbox(run, 0), null, "first frame");
      assert.equal(bbox(run, spec.length - 1), null, "last frame");
      const hold = spec.cues.hold + 1;
      assert.ok(px(run, hold, 0, 0)[3] === 0, "corner stays clear");
      const plate = layer(run, hold, "plate");
      assert.equal(plate.opacity, 1, "the hold is fully opaque");
      assert.ok(px(run, hold, Math.round(plate.x + plate.w - 4), Math.round(plate.y + plate.h / 2))[3] >= 254);
    }));

    it("eases the rule, plate and text on the spec's curves", each((run) => {
      const end = layer(run, spec.cues.hold + 2, "rule").h;
      const t = (f) => f * FRAME_MS;
      const stopAt = Math.floor((5000 - 500) / FRAME_MS + 1e-9) * FRAME_MS;
      const near = (got, want, what, f) => assert.ok(Math.abs(got - want) < 0.02, `${what} frame ${f}: ${got} vs ${want}`);
      for (let f = 0; f < spec.length; f++) {
        const u = t(f) - stopAt;
        const fade = u < 0 ? 1 : 1 - bezier(spec.curves.inOut, u / 500);
        near(layer(run, f, "plate").opacity, Math.min(bezier(spec.curves.inOut, t(f) / 300), fade), "plate", f);
        near(layer(run, f, "rule").h / end, Math.min(bezier(spec.curves.out, t(f) / 480), u < 0 ? 1 : 1 - bezier(spec.curves.exhale, u / 500)), "rule", f);
        for (const [name, i] of [["name", 1], ["role", 2]]) {
          near(layer(run, f, name).opacity, Math.min(bezier(spec.curves.out, (t(f) - 120 - i * 80) / 360), fade), name, f);
        }
      }
      assert.equal(layer(run, spec.cues.stop - 5, "plate").opacity, 1, "the hold is steady until the stop");
      for (let f = 0; f < spec.length; f++) {
        for (const l of run.boxes[f]) assert.ok(l.opacity >= 0 && l.opacity <= 1, "no overshoot");
      }
    }));

    it("keeps every visible pixel inside the safe area", each((run) => {
      const port = run.h > run.w;
      const [mx, bottom] = port ? [0.06, 0.74] : [0.05, 0.95];
      for (let f = 0; f < spec.length; f += 3) {
        const b = bbox(run, f);
        if (!b) continue;
        assert.ok(b.x0 >= run.w * mx - 1 && b.x1 <= run.w * (1 - mx) + 1, `frame ${f} x ${b.x0}-${b.x1}`);
        assert.ok(b.y0 >= run.h * 0.05 - 1 && b.y1 <= run.h * bottom + 1, `frame ${f} y ${b.y0}-${b.y1}`);
      }
    }));

    it("keeps text readable over a light and a dark plate", each((run) => {
      const f = spec.cues.hold + 4;
      const plate = layer(run, f, "plate");
      const x0 = Math.round(plate.x), y0 = Math.round(plate.y), x1 = Math.round(plate.x + plate.w), y1 = Math.round(plate.y + plate.h);
      const base = px(run, f, x1 - 4, Math.round((y0 + y1) / 2));
      INK.forEach((v, i) => assert.ok(Math.abs(base[i] - v) <= 6, `plate colour ${base}`));
      let bright = base;
      for (let y = y0; y < y1; y++) for (let x = x0 + 8; x < x1; x++) { const p = px(run, f, x, y); if (lum(p) > lum(bright)) bright = p; }
      for (const bg of [[242, 242, 242], [16, 16, 16]]) {
        const over = (p) => p.slice(0, 3).map((c, i) => (p[3] * c + (255 - p[3]) * bg[i]) / 255);
        assert.ok(contrast(over(bright), over(base)) >= 7, `text contrast over ${bg[0]}: ${contrast(over(bright), over(base))}`);
        over(base).forEach((c, i) => assert.ok(Math.abs(c - base[i]) <= 2, "the plate hides the plate behind it"));
      }
    }));

    it("renders the same pixels on a second run", () => {
      const run = out.get("withTitle-480x270");
      const r = spawnSync("node", [SCRIPT, MANIFEST, "--out", join(work, "again"), "--size", "480x270", "--data", JSON.stringify(DATA.withTitle), "--no-video"], { encoding: "utf8" });
      assert.equal(r.status, 0, r.stdout + r.stderr);
      assert.deepEqual(JSON.parse(readFileSync(join(work, "again", "report.json"), "utf8")).hashes, JSON.parse(readFileSync(join(run.dir, "report.json"), "utf8")).hashes);
    });
  });
});
