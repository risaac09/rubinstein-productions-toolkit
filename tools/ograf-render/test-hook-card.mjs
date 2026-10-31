// Tests for the hook card template: lint, motion spec, a ProRes 4444 render per data set and
// shape (9:16 and 1:1), then checks on alpha, length, easing, cut points, safe-zone margins
// and contrast, read from the decoded master and from boxes.json. Placeholder lines only.
// Run: node --test tools/ograf-render/test-hook-card.mjs
// Skips with a message where Chrome or ffmpeg is missing; the python cases need python3.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIR = join(HERE, "templates", "hook-card");
const MANIFEST = join(DIR, "hook-card.ograf.json");
const SCRIPT = join(HERE, "ograf-render.mjs");
const PRODUCTION = join(HERE, "..", "..", "production");

const have = (cmd, args) => spawnSync(cmd, args, { stdio: "ignore" }).status === 0;
const chrome = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
  .concat(process.env.CHROME || []).some((c) => (c.includes("/") ? existsSync(c) : have("which", [c])));
const skip = !chrome ? "Chrome not found" : !have("ffmpeg", ["-version"]) ? "ffmpeg not found" : false;
const py = (code) => spawnSync("python3", ["-c", code], { cwd: PRODUCTION, encoding: "utf8", env: { ...process.env, PYTHONPATH: PRODUCTION } });

const spec = JSON.parse(readFileSync(join(DIR, "motion.json"), "utf8"));
const FRAME_MS = 1001 / 24;
const INK = [0x1a, 0x14, 0x12];

// Widest legal lines: 16 characters on 9:16, 24 on 1:1 (rpresolve.hooklint). "W" is the widest capital.
const DATA = {
  placeholder: {},
  oneLine: { line1: "HOOK LINE ONE", line2: "" },
  widePortrait: { line1: "W".repeat(16), line2: "W".repeat(16) },
  wideSquare: { line1: "W".repeat(24), line2: "W".repeat(24) },
};
// [data set, width, height]. 360x640 is 9:16, 540x540 is 1:1.
const RUNS = [["placeholder", 360, 640], ["oneLine", 360, 640], ["widePortrait", 360, 640], ["placeholder", 540, 540], ["wideSquare", 540, 540]];

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

describe("Hook card", () => {
  it("passes motionlint, and the template's curves and timings are the spec's", (t) => {
    const js = readFileSync(join(DIR, "hook-card.js"), "utf8");
    const curves = JSON.parse(js.match(/const CURVES = (\{.*\});/)[1].replace(/(\w+):/g, '"$1":'));
    assert.deepEqual(curves, spec.curves);
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

  it("declares a manifest Resolve accepts, with placeholder lines that pass hooklint", (t) => {
    const m = JSON.parse(readFileSync(MANIFEST, "utf8"));
    assert.equal(m.id, "hook-card");
    assert.equal(m.supportsNonRealTime, true);
    assert.deepEqual(Object.keys(m.schema.properties), ["line1", "line2", "accentColor"]);
    for (const p of Object.values(m.schema.properties)) assert.ok(p.title && "default" in p);
    assert.match(m.schema.properties.line1.default, /^HOOK LINE ONE$/);
    assert.match(m.schema.properties.line2.default, /^HOOK LINE TWO$/);
    const play = m.actionDurations.find((d) => d.type === "playAction").duration;
    assert.ok(Math.abs(play - spec.segments[0].frames * FRAME_MS) < FRAME_MS, "play length matches the build segment");
    assert.equal(Math.round(m.v_bmd.duration * 24000 / 1001), spec.length);
    // The default hold is at least the hooklint floor for a nine-word hook.
    assert.ok((spec.segments[1].frames + spec.segments[2].frames - 1) * FRAME_MS / 1000 >= 2.7, "hold covers 0.3 s per word at nine words");
    const lint = process.env.OGRAF_LINT || "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OGraf HTML Templates/Scripts/lint-ograf.py";
    if (existsSync(lint)) {
      const r = spawnSync("python3", [lint, DIR], { encoding: "utf8" });
      assert.equal(r.status, 0, r.stdout + r.stderr);
      assert.match(r.stdout, /0 error\(s\), 0 warning\(s\)/);
    }
    const h = spawnSync("python3", ["-m", "rpresolve.hooklint", "--line", m.schema.properties.line1.default, "--line", m.schema.properties.line2.default,
      "--size", "1080x1920", "--hold", "2.7", "--start", "0"], { cwd: PRODUCTION, encoding: "utf8" });
    if (h.error) return t.skip("python3 not available");
    assert.equal(h.status, 0, h.stdout + h.stderr);
  });

  describe("renders", { skip }, () => {
    let work, margins;
    const out = new Map();
    before(() => {
      // The margins come from rpresolve.safezone, the one place they are written.
      const r = py("import json; from rpresolve import safezone as z; print(json.dumps({'tall': z.margins(1080, 1920), 'square': z.margins(1080, 1080)}))");
      assert.equal(r.status, 0, "rpresolve.safezone must import: " + r.stderr);
      margins = JSON.parse(r.stdout);
      work = mkdtempSync(join(tmpdir(), "hook-card-"));
      for (const [set, w, h] of RUNS) {
        const dir = join(work, `${set}-${w}x${h}`);
        const r = spawnSync("node", [SCRIPT, MANIFEST, "--out", dir, "--size", `${w}x${h}`, "--data", JSON.stringify(DATA[set])], { encoding: "utf8" });
        assert.equal(r.status, 0, `${set} ${w}x${h}: ${r.stdout}${r.stderr}`);
        const boxes = JSON.parse(readFileSync(join(dir, "boxes.json"), "utf8")).frames;
        const raw = spawnSync("ffmpeg", ["-v", "error", "-i", join(dir, "hook-card.mov"),
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
        if (run.raw[((f * run.h + y) * run.w + x) * 4 + 3] > 0) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      }
      return x1 < 0 ? null : { x0, y0, x1, y1 };
    };

    it("is ProRes 4444 with alpha and the spec's length", each((run) => {
      const p = JSON.parse(spawnSync("ffprobe", ["-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=codec_name,profile,pix_fmt,nb_read_frames,width,height", "-of", "json", join(run.dir, "hook-card.mov")], { encoding: "utf8" }).stdout).streams[0];
      assert.equal(p.codec_name, "prores");
      assert.equal(p.profile, "4444");
      assert.match(p.pix_fmt, /^yuva444p1[02]le$/);
      assert.equal(Number(p.nb_read_frames), spec.length);
      assert.equal(run.frames, spec.length);
      assert.deepEqual([p.width, p.height], [run.w, run.h]);
    }));

    it("is fully clear on the first and last frame, and opaque once the build ends", each((run) => {
      assert.equal(bbox(run, 0), null, "first frame");
      assert.equal(bbox(run, spec.length - 1), null, "last frame");
      const hold = spec.cues.hold + 1;
      assert.ok(px(run, hold, 0, 0)[3] === 0, "corner stays clear");
      const plate = layer(run, hold, "plate");
      assert.equal(plate.opacity, 1, "the hold is fully opaque");
      assert.ok(px(run, hold, Math.round(plate.x + plate.w - 4), Math.round(plate.y + plate.h / 2))[3] >= 254);
      // A frame-0 hook: the plate is fully on by the fifth frame (about 0.17 s).
      assert.ok(layer(run, 4, "plate").opacity > 0.99, "the plate is on screen within four frames");
    }));

    it("eases the rule, plate and text on the spec's curves, with no overshoot", each((run) => {
      const end = layer(run, spec.cues.hold + 2, "rule").h;
      const t = (f) => f * FRAME_MS;
      const stopAt = Math.floor((4000 - 500) / FRAME_MS + 1e-9) * FRAME_MS;
      const near = (got, want, what, f) => assert.ok(Math.abs(got - want) < 0.02, `${what} frame ${f}: ${got} vs ${want}`);
      for (let f = 0; f < spec.length; f++) {
        const u = t(f) - stopAt;
        const fade = u < 0 ? 1 : 1 - bezier(spec.curves.inOut, u / 500);
        near(layer(run, f, "plate").opacity, Math.min(bezier(spec.curves.inOut, t(f) / 167), fade), "plate", f);
        near(layer(run, f, "rule").h / end, Math.min(bezier(spec.curves.out, t(f) / 250), u < 0 ? 1 : 1 - bezier(spec.curves.exhale, u / 500)), "rule", f);
        for (const [name, i] of [["line1", 0], ["line2", 1]]) {
          const l = layer(run, f, name);
          if (l && l.w > 0) near(l.opacity, Math.min(bezier(spec.curves.out, (t(f) - 42 - i * 42) / 208), fade), name, f);
        }
      }
      assert.equal(layer(run, spec.cues.stop - 5, "plate").opacity, 1, "the hold is steady until the stop");
      for (let f = 0; f < spec.length; f++) {
        for (const l of run.boxes[f]) assert.ok(l.opacity >= 0 && l.opacity <= 1, "no overshoot");
      }
    }));

    it("keeps every opaque pixel inside the safe-zone margins at both shapes", each((run) => {
      const [top, bottom, side] = run.h >= 1.5 * run.w ? margins.tall : margins.square;
      let seen = 0;
      for (let f = 0; f < spec.length; f += 2) {
        const b = bbox(run, f);
        if (!b) continue;
        seen++;
        assert.ok(b.x0 >= run.w * side - 1 && b.x1 <= run.w * (1 - side) + 1, `${run.set} frame ${f} x ${b.x0}-${b.x1}`);
        assert.ok(b.y0 >= run.h * top - 1 && b.y1 <= run.h * (1 - bottom) + 1, `${run.set} frame ${f} y ${b.y0}-${b.y1}`);
      }
      assert.ok(seen > 30, "the card was on screen for most of the clip");
    }));

    it("keeps text readable at 7:1 over a light and a dark picture", each((run) => {
      const f = spec.cues.hold + 4;
      const plate = layer(run, f, "plate");
      const x0 = Math.round(plate.x), y0 = Math.round(plate.y), x1 = Math.round(plate.x + plate.w), y1 = Math.round(plate.y + plate.h);
      const base = px(run, f, x1 - 4, Math.round((y0 + y1) / 2));
      INK.forEach((v, i) => assert.ok(Math.abs(base[i] - v) <= 6, `plate colour ${base}`));
      let bright = base;
      for (let y = y0; y < y1; y++) for (let x = x0 + 12; x < x1; x++) { const p = px(run, f, x, y); if (lum(p) > lum(bright)) bright = p; }
      for (const bg of [[242, 242, 242], [16, 16, 16]]) {
        const over = (p) => p.slice(0, 3).map((c, i) => (p[3] * c + (255 - p[3]) * bg[i]) / 255);
        assert.ok(contrast(over(bright), over(base)) >= 7, `text contrast over ${bg[0]}: ${contrast(over(bright), over(base))}`);
        over(base).forEach((c, i) => assert.ok(Math.abs(c - base[i]) <= 2, "the plate hides the picture behind it"));
      }
    }));

    it("fits the widest legal lines inside the text area, unclipped", () => {
      for (const key of ["widePortrait-360x640", "wideSquare-540x540"]) {
        const run = out.get(key);
        const f = spec.cues.hold + 4, line = layer(run, f, "line1");
        // The text box clips at its right edge, so text that overflowed would be bright in its last columns.
        const x0 = Math.floor(line.x + line.w * 0.98), x1 = Math.round(line.x + line.w);
        for (let y = Math.round(line.y); y < Math.round(line.y + line.h); y++) {
          for (let x = x0; x < x1; x++) assert.ok(lum(px(run, f, x, y)) < 0.05, `${key} text meets the clip edge at ${x},${y}`);
        }
        // And the widest line really is wide: it reaches into the last tenth of the box.
        let reach = 0;
        for (let y = Math.round(line.y); y < Math.round(line.y + line.h); y++) {
          for (let x = Math.round(line.x); x < x1; x++) if (lum(px(run, f, x, y)) > 0.5) reach = Math.max(reach, x - line.x);
        }
        assert.ok(reach > line.w * 0.85, `${key} the widest line fills the box (${reach} of ${line.w})`);
      }
    });

    it("renders the same pixels on a second run", () => {
      const run = out.get("placeholder-360x640");
      const r = spawnSync("node", [SCRIPT, MANIFEST, "--out", join(work, "again"), "--size", "360x640", "--data", JSON.stringify(DATA.placeholder), "--no-video"], { encoding: "utf8" });
      assert.equal(r.status, 0, r.stdout + r.stderr);
      assert.deepEqual(JSON.parse(readFileSync(join(work, "again", "report.json"), "utf8")).hashes, JSON.parse(readFileSync(join(run.dir, "report.json"), "utf8")).hashes);
    });
  });
});
