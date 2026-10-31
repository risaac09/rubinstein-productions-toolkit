#!/usr/bin/env node
// ograf-render: render an OGraf template offline to ProRes 4444 with alpha.
// Node built-ins only. Drives one headless Chrome over the DevTools protocol the way
// Resolve's non-realtime path does: load, updateAction, setActionsSchedule, then
// goToTime per frame. See README.md. Exit: 0 ok, 1 a check failed, 2 refused or bad
// input, 3 Chrome or ffmpeg missing.

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { basename, extname, join, resolve, sep } from "node:path";

const LINTER = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OGraf HTML Templates/Scripts/lint-ograf.py";
const CHROME_CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
];
const MIME = { ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
  ".css": "text/css", ".html": "text/html", ".woff2": "font/woff2", ".woff": "font/woff",
  ".ttf": "font/ttf", ".otf": "font/otf", ".png": "image/png", ".svg": "image/svg+xml" };
const DEFAULT_FPS = "24000/1001";
const GENERIC_FAMILIES = new Set(["serif", "sans-serif", "monospace", "cursive", "fantasy",
  "system-ui", "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded", "math", "emoji", "inherit", "initial"]);

class Fail extends Error {
  constructor(msg, code = 2) { super(msg); this.code = code; }
}

const USAGE = `usage: ograf-render.mjs TEMPLATE.ograf.json --out DIR [options]

  --out DIR            output folder (created); holds NAME.mov, boxes.json, report.json
  --fps N|A/B          frame rate (default ${DEFAULT_FPS})
  --size WxH           page size (default the manifest's renderRequirements ideal, else 1920x1080)
  --duration SECONDS   clip length (default: the larger of v_bmd.duration and play + stop, else 10)
  --frames A-B         render only frames A to B inclusive (default all)
  --order sequential|shuffled   frame visiting order (default sequential); the output is the same
  --data JSON|@FILE    property values merged over the schema defaults
  --keep-frames        keep the PNGs in OUT/frames (not if it exists)
  --no-video           stop after the PNGs, boxes and report
  --strict-fonts       a missing font fails the run (default: a warning in report.json)
  --font-wait-ms N     how long a frame waits for fonts and paint before it is captured anyway (default 2000)
  --chrome PATH        Chrome binary (default: CHROME env, then the usual names)
  --lint               run Resolve's own linter when installed (OGRAF_LINT overrides its path)
`;

function parseArgs(argv) {
  const o = { order: "sequential", fps: DEFAULT_FPS };
  const need = (i) => { if (i + 1 >= argv.length) throw new Fail(`${argv[i]} needs a value\n${USAGE}`); return argv[i + 1]; };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-h" || a === "--help") { process.stdout.write(USAGE); process.exit(0); }
    else if (a === "--keep-frames") o.keepFrames = true;
    else if (a === "--no-video") o.noVideo = true;
    else if (a === "--strict-fonts") o.strictFonts = true;
    else if (a === "--lint") o.lint = true;
    else if (["--out", "--fps", "--size", "--duration", "--frames", "--order", "--data", "--chrome", "--font-wait-ms"].includes(a)) {
      o[a.slice(2)] = need(i); i++;
    } else if (a.startsWith("-")) throw new Fail(`unknown option ${a}\n${USAGE}`);
    else if (o.template) throw new Fail(`one template at a time\n${USAGE}`);
    else o.template = a;
  }
  if (!o.template || !o.out) throw new Fail(USAGE);
  if (!["sequential", "shuffled"].includes(o.order)) throw new Fail("--order is sequential or shuffled");
  return o;
}

function parseFps(s) {
  const m = /^(\d+(?:\.\d+)?)(?:\/(\d+(?:\.\d+)?))?$/.exec(s);
  if (!m) throw new Fail(`bad --fps ${s}`);
  const num = Number(m[1]), den = Number(m[2] ?? 1);
  if (!(num > 0 && den > 0)) throw new Fail(`bad --fps ${s}`);
  return { num, den, text: m[2] ? `${m[1]}/${m[2]}` : m[1], value: num / den };
}

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

function loadManifest(path) {
  let m;
  try { m = JSON.parse(readFileSync(path, "utf8")); } catch (e) { throw new Fail(`cannot read ${path}: ${e.message}`); }
  if (m.supportsNonRealTime !== true) {
    throw new Fail(`refused: ${basename(path)} does not set supportsNonRealTime to true, so it has no offline path`);
  }
  if (typeof m.main !== "string" || !m.main) throw new Fail("refused: the manifest has no main");
  return m;
}

function schemaDefaults(schema) {
  const out = {};
  for (const [k, v] of Object.entries(schema?.properties ?? {})) if (v && "default" in v) out[k] = v.default;
  return out;
}

// Resolve's default schedule: play at 0, an automatic stop placed so the exit ends on the
// last frame (rounded down to a whole frame; a duration of -1 puts it on the last frame).
function defaultSchedule(manifest, data, fps, durationMs, frameCount) {
  const frameMs = 1000 / fps.value;
  const sched = [{ timestamp: 0, action: { type: "playAction", params: {} } }];
  const stop = (manifest.actionDurations ?? []).find((d) => d.type === "stopAction");
  if (stop) {
    const at = stop.duration === -1 ? (frameCount - 1) * frameMs : durationMs - stop.duration;
    sched.push({ timestamp: Math.max(0, Math.floor(at / frameMs + 1e-9) * frameMs), action: { type: "stopAction", params: {} } });
  }
  sched.push({ timestamp: 0, action: { type: "updateAction", params: { data } } });
  return sched;
}

function clipMs(manifest, durationArg) {
  if (durationArg !== undefined) {
    const s = Number(durationArg);
    if (!(s > 0)) throw new Fail("bad --duration");
    return s * 1000;
  }
  const ad = (t) => (manifest.actionDurations ?? []).find((d) => d.type === t)?.duration ?? 0;
  const declared = Math.max(manifest.v_bmd?.duration ? manifest.v_bmd.duration * 1000 : 0,
    Math.max(0, ad("playAction")) + Math.max(0, ad("stopAction")));
  return declared || 10000;
}

// ---------------------------------------------------------------------------
// Static server for the template folder plus a host page
// ---------------------------------------------------------------------------

function hostPage(main) {
  return `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent;overflow:hidden}</style>
<body><script type="module">
import G from ${JSON.stringify("/" + main.replace(/^\.?\//, ""))};
customElements.define("og-graphic", G);
const el = document.createElement("og-graphic");
document.body.append(el);
window.__g = el;
window.__ready = true;
</script>`;
}

function serve(root, main) {
  const base = resolve(root);
  const server = createServer((req, res) => {
    let url;
    try { url = decodeURIComponent(new URL(req.url, "http://x").pathname); } catch { res.statusCode = 400; return res.end(); }
    if (url === "/__host.html") { res.setHeader("content-type", "text/html"); return res.end(hostPage(main)); }
    const p = resolve(base, "." + url);
    if ((p !== base && !p.startsWith(base + sep)) || !existsSync(p) || !statSync(p).isFile()) { res.statusCode = 404; return res.end(); }
    res.setHeader("content-type", MIME[extname(p)] ?? "application/octet-stream");
    res.end(readFileSync(p));
  });
  return new Promise((ok) => server.listen(0, "127.0.0.1", () => ok({ server, port: server.address().port })));
}

// ---------------------------------------------------------------------------
// Chrome over the DevTools protocol
// ---------------------------------------------------------------------------

function findChrome(arg) {
  const tries = [arg, process.env.CHROME, ...CHROME_CANDIDATES].filter(Boolean);
  for (const c of tries) {
    if (c.includes("/") ? existsSync(c) : spawnSync("which", [c]).status === 0) return c;
  }
  throw new Fail("Chrome not found; pass --chrome PATH or set CHROME", 3);
}

async function launchChrome(bin) {
  const dir = mkdtempSync(join(tmpdir(), "ograf-chrome-"));
  const proc = spawn(bin, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${dir}`,
    "--no-first-run", "--disable-dev-shm-usage", "--no-default-browser-check", "--hide-scrollbars", "--disable-gpu",
    "--force-color-profile=srgb", ...(process.getuid?.() === 0 || process.env.CI ? ["--no-sandbox"] : []), "about:blank"], { stdio: "ignore" });
  // Chrome may still be writing to its profile as it dies, so retry; a leftover temp dir must not fail a render.
  const cleanup = () => {
    try { proc.kill("SIGKILL"); } catch {}
    try { rmSync(dir, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 }); } catch {}
  };
  try {
    const portFile = join(dir, "DevToolsActivePort");
    for (let i = 0; i < 300 && !existsSync(portFile); i++) await new Promise((r) => setTimeout(r, 100));
    if (!existsSync(portFile)) throw new Fail("Chrome did not open a DevTools port", 3);
    const port = readFileSync(portFile, "utf8").split("\n")[0];
    let page;
    for (let i = 0; i < 50 && !page; i++) {
      page = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page");
      if (!page) await new Promise((r) => setTimeout(r, 100));
    }
    if (!page) throw new Fail("Chrome opened no page", 3);
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = () => bad(new Fail("DevTools socket failed", 3)); });
    let id = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      const p = m.id && pending.get(m.id);
      if (p) { pending.delete(m.id); m.error ? p.bad(new Error(`${p.method}: ${m.error.message}`)) : p.ok(m.result); }
    };
    const send = (method, params = {}) => new Promise((ok, bad) => {
      const timer = setTimeout(() => bad(new Error(`${method}: no answer from Chrome in 60 s (a template stuck in a loop?)`)), 60000);
      pending.set(++id, { ok: (v) => { clearTimeout(timer); ok(v); }, bad: (e) => { clearTimeout(timer); bad(e); }, method });
      ws.send(JSON.stringify({ id, method, params }));
    });
    return { send, close: () => { try { ws.close(); } catch {} cleanup(); } };
  } catch (e) { cleanup(); throw e; }
}

// Runs an expression in the page; throws with the page's own message on an exception.
async function evaluate(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) {
    throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  }
  return r.result.value;
}

// Collects every [data-layer] element, looking inside open shadow roots.
const BOXES_JS = `(() => {
  const out = [];
  const visit = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.hasAttribute("data-layer")) {
        const r = el.getBoundingClientRect();
        out.push({ layer: el.getAttribute("data-layer"), x: r.x, y: r.y, w: r.width, h: r.height,
          opacity: Number(getComputedStyle(el).opacity) });
      }
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(document);
  return out;
})()`;

// Font families named by any text-bearing element, with whether each one is present.
// document.fonts.check() is true for any system family, so measure against two fallbacks.
const FONTS_JS = `(async () => {
  const families = new Map();
  const visit = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) visit(el.shadowRoot);
      const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (!hasText) continue;
      const first = getComputedStyle(el).fontFamily.split(",")[0].trim().replace(/^["']|["']$/g, "");
      families.set(first, el.textContent.trim().slice(0, 40));
    }
  };
  visit(document);
  await document.fonts.ready;
  const loaded = new Set([...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family.replace(/^["']|["']$/g, "")));
  const c = document.createElement("canvas").getContext("2d");
  const sample = "Hamburgefonstiv 0123456789 mmmmmmmmmmlli";
  const width = (font) => { c.font = "72px " + font; return c.measureText(sample).width; };
  const out = [];
  for (const family of families.keys()) {
    const generic = ${JSON.stringify([...GENERIC_FAMILIES])}.includes(family.toLowerCase());
    const present = generic || loaded.has(family) ||
      width('"' + family + '", monospace') !== width("monospace") ||
      width('"' + family + '", serif') !== width("serif");
    out.push({ family, present });
  }
  return out;
})()`;

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

function shuffled(n) {
  const a = Array.from({ length: n }, (_, i) => i);
  let s = 0x9e3779b9; // fixed seed: the order is repeatable
  const rnd = () => { s = (s + 0x6d2b79f5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  for (let i = n - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}

function runLinter(manifestPath) {
  const lint = process.env.OGRAF_LINT || LINTER;
  if (!existsSync(lint)) return { ran: false, note: "Resolve's linter is not installed" };
  const r = spawnSync("python3", [lint, manifestPath], { encoding: "utf8" });
  return { ran: true, exit: r.status, output: (r.stdout + r.stderr).trim() };
}

async function main() {
  if (typeof WebSocket === "undefined") throw new Fail(`Node 22 or newer is needed (global WebSocket); this is ${process.version}`, 3);
  const o = parseArgs(process.argv.slice(2));
  const manifestPath = resolve(o.template);
  const manifest = loadManifest(manifestPath);
  const root = resolve(manifestPath, "..");
  if (!existsSync(join(root, manifest.main))) throw new Fail(`refused: main ${manifest.main} is not next to the manifest`);

  const fps = parseFps(o.fps);
  const ideal = manifest.renderRequirements?.[0]?.resolution;
  let [width, height] = [ideal?.width?.ideal ?? 1920, ideal?.height?.ideal ?? 1080];
  if (o.size) {
    const m = /^(\d+)x(\d+)$/.exec(o.size);
    if (!m) throw new Fail("bad --size, use WxH");
    [width, height] = [Number(m[1]), Number(m[2])];
  }
  const durationMs = clipMs(manifest, o.duration);
  const total = Math.max(1, Math.round((durationMs / 1000) * fps.value));
  let [first, last] = [0, total - 1];
  if (o.frames) {
    const m = /^(\d+)-(\d+)$/.exec(o.frames);
    if (!m || Number(m[1]) > Number(m[2]) || Number(m[2]) >= total) throw new Fail(`bad --frames, use A-B within 0-${total - 1}`);
    [first, last] = [Number(m[1]), Number(m[2])];
  }
  let extra = {};
  if (o.data) {
    try { extra = JSON.parse(o.data.startsWith("@") ? readFileSync(o.data.slice(1), "utf8") : o.data); }
    catch (e) { throw new Fail(`bad --data: ${e.message}`); }
  }
  const data = { ...schemaDefaults(manifest.schema), ...extra };
  const name = basename(manifestPath).replace(/\.ograf\.json$|\.json$/, "");
  const outDir = resolve(o.out);
  const mov = join(outDir, `${name}.mov`);
  const tmpMov = join(outDir, `.${name}.tmp.mov`);

  if (!o.noVideo && spawnSync("ffmpeg", ["-version"]).status !== 0) throw new Fail("ffmpeg not found on PATH", 3);
  const chromeBin = findChrome(o.chrome);
  // Nothing already in OUT is touched until the new render has passed its checks: frames go
  // to a temp folder this run made, the movie to a temp name, and both are cleaned up on failure.
  mkdirSync(outDir, { recursive: true });
  const framesDir = mkdtempSync(join(outDir, ".frames-"));
  try {
    return await render();
  } finally {
    rmSync(framesDir, { recursive: true, force: true });
    rmSync(tmpMov, { force: true });
  }

  async function render() {
  const { server, port } = await serve(root, manifest.main);
  const cdp = await launchChrome(chromeBin);
  const report = { template: basename(manifestPath), id: manifest.id, fps: fps.text, width, height,
    duration_ms: durationMs, frames: [first, last], order: o.order, fonts: [], checks: {}, hashes: {} };
  const boxes = {};
  try {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
    await cdp.send("Page.navigate", { url: `http://127.0.0.1:${port}/__host.html` });
    for (let i = 0; ; i++) {
      if (await evaluate(cdp, "window.__ready === true").catch(() => false)) break;
      if (i > 100) throw new Fail("the template's module did not load (check main for an import error)");
      await new Promise((r) => setTimeout(r, 100));
    }
    const call = (method, arg) => evaluate(cdp, `window.__g.${method}(${JSON.stringify(arg)})`);
    const ok = (r, what) => { if (r && r.statusCode >= 400) throw new Fail(`${what} returned ${r.statusCode}${r.message ? `: ${r.message}` : ""}`); };
    ok(await call("load", { data, renderType: "non-realtime", renderCharacteristics: { resolution: { width, height }, frameRate: fps.value } }), "load");
    ok(await call("updateAction", { data }), "updateAction");
    ok(await call("setActionsSchedule", { schedule: defaultSchedule(manifest, data, fps, durationMs, total) }), "setActionsSchedule");

    let painted = false;
    const fontWaitMs = Number(o["font-wait-ms"]) || 2000;
    const fontWaitTimeouts = [];
    let probing = false;  // determinism revisits are not the stored frames
    const visit = async (i) => {
      const timestamp = (i * 1000) / fps.value;
      ok(await call("goToTime", { timestamp }), `goToTime(${timestamp})`);
      // Fonts must settle before a capture, or a frame can be grabbed in a fallback face. The
      // first frame also waits two paints; later ones force a layout (which starts any font load)
      // and wait on the fonts, which costs no frame wait. The timeout keeps a throttled page
      // (rAF paused) from hanging the render.
      const paints = painted ? 0 : 2;
      painted = true;
      const settled = await evaluate(cdp, `Promise.race([new Promise((r) => setTimeout(() => r(false), ${fontWaitMs})), (async () => {
        for (let k = 0; k < ${paints}; k++) await new Promise((r) => requestAnimationFrame(r));
        document.body.offsetHeight;
        await document.fonts.ready;
        return true;
      })()])`);
      if (!settled && !probing) fontWaitTimeouts.push(i);  // captured anyway; the report says so
      const shot = await cdp.send("Page.captureScreenshot", { format: "png", fromSurface: true });
      return { png: Buffer.from(shot.data, "base64"), boxes: await evaluate(cdp, BOXES_JS) };
    };
    const indices = Array.from({ length: last - first + 1 }, (_, i) => first + i);
    const order = o.order === "shuffled" ? shuffled(indices.length).map((k) => indices[k]) : indices;
    for (const i of order) {
      const f = await visit(i);
      writeFileSync(join(framesDir, `${String(i).padStart(6, "0")}.png`), f.png);
      report.hashes[i] = sha256(f.png);
      boxes[i] = f.boxes;
    }

    // Determinism: revisit a spread of frames in reverse, after the whole pass has moved
    // time elsewhere. A random-access seek that depends on history, or any Math.random()
    // in goToTime, gives a different PNG.
    const probe = [...new Set([first, last, ...Array.from({ length: 6 }, (_, k) => first + Math.round(((last - first) * (k + 1)) / 7))])].sort((a, b) => b - a);
    const drift = [];
    probing = true;
    for (const i of probe) {
      if (sha256((await visit(i)).png) !== report.hashes[i]) drift.push(i);
    }
    report.checks.determinism = { pass: drift.length === 0, probed: probe.length, differing_frames: drift.sort((a, b) => a - b) };

    if (fontWaitTimeouts.length) {
      const frames = [...new Set(fontWaitTimeouts)].sort((a, b) => a - b);
      report.font_wait_timeouts = { frames, wait_ms: fontWaitMs, note: "the font/paint wait timed out; these frames were captured without confirmed fonts" };
      process.stderr.write(`ograf-render: font/paint wait timed out after ${fontWaitMs} ms on ${frames.length} frame(s) (${frames.slice(0, 5).join(", ")}${frames.length > 5 ? ", ..." : ""}); captured anyway, see report.json\n`);
    }
    report.fonts = await evaluate(cdp, FONTS_JS);
    ok(await call("dispose", {}).catch(() => null), "dispose");
  } finally {
    cdp.close();
    server.close();
  }

  const missing = report.fonts.filter((f) => !f.present).map((f) => f.family);
  report.checks.fonts = { pass: missing.length === 0, missing };
  if (o.lint) report.checks.lint = runLinter(manifestPath);

  if (!o.noVideo) {
    // PNG is straight-alpha RGBA; scale does the RGB to BT.709 limited-range YUV conversion and
    // setparams then tags the frames, so no output flag decides the colour tags (ffmpeg 6.1 and
    // 8 tag differently when asked on the command line).
    const vf = "scale=out_range=tv:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,format=yuva444p10le," +
      "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709";
    const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-framerate", fps.text, "-start_number", String(first),
      "-i", join(framesDir, "%06d.png"), "-vf", vf, "-c:v", "prores_ks", "-profile:v", "4444",
      "-pix_fmt", "yuva444p10le", "-vendor", "apl0", tmpMov], { encoding: "utf8" });
    if (r.status !== 0) throw new Fail(`ffmpeg failed: ${r.stderr.trim().slice(-400)}`);
  }
  const det = report.checks.determinism;
  const lintBad = report.checks.lint?.ran && report.checks.lint.exit !== 0;
  const passed = det.pass && !(o.strictFonts && missing.length) && !lintBad;
  // Only a render that passed every check replaces the previous movie and frames; a failed one
  // still writes its report and boxes, which describe the failure.
  if (passed && !o.noVideo) { renameSync(tmpMov, mov); report.video = mov; }
  writeFileSync(join(outDir, "boxes.json"), JSON.stringify({ fps: fps.text, width, height, frames: boxes }, null, 1));
  if (o.keepFrames) {
    // An existing OUT/frames is never replaced: it may not be ours.
    const kept = join(outDir, "frames");
    if (!existsSync(kept)) { renameSync(framesDir, kept); chmodSync(kept, 0o755); }
    else process.stderr.write(`ograf-render: ${kept} already exists and was left alone; frames not kept\n`);
  }
  writeFileSync(join(outDir, "report.json"), JSON.stringify(report, null, 1));

  process.stdout.write(`${name}: ${last - first + 1} frames at ${fps.text}, ${width}x${height}${report.video ? ` -> ${mov}` : ""}\n`);
  process.stdout.write(`determinism: ${det.pass ? "PASS" : `FAIL (frames ${det.differing_frames.join(", ")} changed on revisit)`}\n`);
  if (missing.length) process.stdout.write(`fonts: ${o.strictFonts ? "FAIL" : "warn"}, not found: ${missing.join(", ")}\n`);
  if (report.checks.lint) process.stdout.write(`linter: ${report.checks.lint.ran ? `exit ${report.checks.lint.exit}\n${report.checks.lint.output}` : report.checks.lint.note}\n`);
  if (!passed && !o.noVideo) process.stdout.write("a failed render does not replace the previous movie\n");
  return passed ? 0 : 1;
  }
}

main().then((c) => process.exit(c), (e) => {
  process.stderr.write(`ograf-render: ${e.message}\n`);
  process.exit(e instanceof Fail ? e.code : 1);
});
