// Unit test for the Chrome binary lookup order. No Chrome is launched.
// Run: node --test tools/ograf-render/test-find-chrome.mjs
import assert from "node:assert/strict";
import { chmodSync, mkdirSync, readdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, describe, it } from "node:test";
import { findChrome, isHeadlessShell, platformDir } from "./find-chrome.mjs";
import { spawnSync } from "node:child_process";
import { symlinkSync } from "node:fs";

const root = mkdtempSync(join(tmpdir(), "find-chrome-"));
after(() => rmSync(root, { recursive: true, force: true }));
const fake = (...parts) => { // an executable file at root/parts
  const p = resolve(root, ...parts);
  mkdirSync(join(p, ".."), { recursive: true });
  writeFileSync(p, "#!/bin/sh\n"); chmodSync(p, 0o755);
  return p;
};
const shell = (cache, v) => fake(cache, "chrome-headless-shell", `${platformDir()}-${v}`, "chrome-headless-shell-x", "chrome-headless-shell");
const emptyPath = join(root, "nopath");
mkdirSync(emptyPath);

describe("findChrome", () => {
  const cache = join(root, "cache");
  const v9 = shell(cache, "99.0.1.1");
  const v155 = shell(cache, "155.0.8059.39");
  const v1000 = shell(cache, "1000.0.0.1");
  const env = { PUPPETEER_CACHE_DIR: cache, PATH: emptyPath };

  it("an explicit arg wins over CHROME and the cache", () => {
    const arg = fake("arg", "chrome");
    assert.equal(findChrome(arg, { ...env, CHROME: v9 }), arg);
  });
  it("CHROME wins over the cache", () => {
    const c = fake("env", "chrome");
    assert.equal(findChrome(undefined, { ...env, CHROME: c }), c);
  });
  it("the cache is searched newest version first (numeric, not text)", () => {
    assert.equal(findChrome(undefined, env), v1000);
    assert.ok(![v9, v155].includes(v1000));
  });
  it("a headless-shell on PATH beats the cache", () => {
    const onPath = fake("bin", "chrome-headless-shell");
    assert.equal(findChrome(undefined, { ...env, PATH: join(root, "bin") }), onPath);
    assert.ok(onPath);
  });
  it("an empty or missing cache falls through without throwing", () => {
    const r = findChrome(undefined, { PUPPETEER_CACHE_DIR: join(root, "nothing"), PATH: emptyPath });
    assert.ok(r === null || !isHeadlessShell(r)); // the usual Chrome names, or null on a bare machine
  });
  it("only a binary named chrome-headless-shell is treated as headless-only", () => {
    assert.ok(isHeadlessShell("/x/chrome-headless-shell"));
    assert.ok(!isHeadlessShell("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"));
  });
});

describe("explicit browser is never replaced (finding 4)", () => {
  const cache = join(root, "cache2");
  shell(cache, "1.0.0.1");
  const env = { PUPPETEER_CACHE_DIR: cache, PATH: emptyPath };
  it("a missing --chrome throws with code 3 and names the path", () => {
    assert.throws(() => findChrome("/missing/chrome", env), (e) => e.code === 3 && /\/missing\/chrome/.test(e.message));
  });
  it("a missing CHROME throws too", () => {
    assert.throws(() => findChrome(undefined, { ...env, CHROME: "/missing/chrome" }), (e) => e.code === 3);
  });
});

describe("explicit path must be an executable file (finding 5)", () => {
  const env = { PUPPETEER_CACHE_DIR: join(root, "none"), PATH: emptyPath };
  it("a directory is refused", () => {
    const d = join(root, "adir"); mkdirSync(d);
    assert.throws(() => findChrome(d, env), (e) => e.code === 3);
  });
  it("a non-executable file is refused", () => {
    const f = fake("noexec", "chrome"); chmodSync(f, 0o644);
    assert.throws(() => findChrome(f, env), (e) => e.code === 3);
  });
  it("a directory named chrome-headless-shell on PATH is skipped", () => {
    mkdirSync(join(root, "dirpath", "chrome-headless-shell"), { recursive: true });
    const r = findChrome(undefined, { ...env, PATH: join(root, "dirpath") });
    assert.ok(r === null || !r.startsWith(join(root, "dirpath")));
  });
});

describe("headless decision follows the real file (finding 6)", () => {
  it("a link named chrome-headless-shell to another binary is not headless-only", () => {
    const real = fake("full", "Google Chrome");
    const link = join(root, "linkdir", "chrome-headless-shell");
    mkdirSync(join(root, "linkdir")); symlinkSync(real, link);
    assert.ok(!isHeadlessShell(link));
  });
  it("a link to a real chrome-headless-shell is headless-only", () => {
    const real = fake("hs", "chrome-headless-shell");
    const link = join(root, "linkdir", "alias"); symlinkSync(real, link);
    assert.ok(isHeadlessShell(link));
  });
});

describe("cache entries are filtered by platform (finding 7)", () => {
  it("a build for another platform is never picked", () => {
    const cache = join(root, "cache3");
    const other = fake(cache, "chrome-headless-shell", "linux-999.0.0.1", "d", "chrome-headless-shell");
    const mine = fake(cache, "chrome-headless-shell", `${platformDir()}-1.0.0.1`, "d", "chrome-headless-shell");
    const env = { PUPPETEER_CACHE_DIR: cache, PATH: emptyPath };
    assert.equal(findChrome(undefined, env, process.platform, process.arch), mine);
    assert.notEqual(findChrome(undefined, env, "darwin", "arm64"), other);
    assert.equal(findChrome(undefined, env, "linux", "x64"), other);
  });
  it("maps the platform names puppeteer uses", () => {
    assert.equal(platformDir("darwin", "arm64"), "mac_arm");
    assert.equal(platformDir("darwin", "x64"), "mac");
    assert.equal(platformDir("linux", "x64"), "linux");
    assert.equal(platformDir("win32", "x64"), "win64");
  });
});

describe("ograf-render CLI with a bad Chrome (findings 4, 5)", () => {
  const cli = join(import.meta.dirname, "ograf-render.mjs");
  const tpl = join(import.meta.dirname, "templates", "hook-card", "hook-card.ograf.json");
  const run = (chrome) => {
    const tmp = join(root, "tmp-" + Math.random().toString(36).slice(2)); mkdirSync(tmp);
    const r = spawnSync(process.execPath, [cli, tpl, "--out", join(tmp, "out"), "--chrome", chrome, "--no-video", "--frames", "0-0"],
      { encoding: "utf8", env: { PATH: emptyPath, HOME: root, TMPDIR: tmp, PUPPETEER_CACHE_DIR: join(root, "none") } });
    return { ...r, tmp };
  };
  const leftovers = (tmp) => readdirSync(tmp).filter((n) => n.startsWith("ograf-chrome-"));
  it("a missing browser exits 3, no temp folder", () => {
    const r = run("/missing/chrome");
    assert.equal(r.status, 3, r.stderr); assert.match(r.stderr, /missing\/chrome/); assert.deepEqual(leftovers(r.tmp), []);
  });
  it("a directory exits 3, no temp folder", () => {
    const r = run(root);
    assert.equal(r.status, 3, r.stderr); assert.deepEqual(leftovers(r.tmp), []);
  });
  it("a spawn failure after the check (exec format) exits 3, no temp folder", () => {
    const f = join(root, "badexec"); writeFileSync(f, "not a program"); chmodSync(f, 0o755);
    const r = run(f);
    assert.equal(r.status, 3, r.stderr); assert.deepEqual(leftovers(r.tmp), []);
  });
});
