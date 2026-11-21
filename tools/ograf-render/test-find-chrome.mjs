// Unit test for the Chrome binary lookup order. No Chrome is launched.
// Run: node --test tools/ograf-render/test-find-chrome.mjs
import assert from "node:assert/strict";
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, describe, it } from "node:test";
import { findChrome, isHeadlessShell } from "./find-chrome.mjs";

const root = mkdtempSync(join(tmpdir(), "find-chrome-"));
after(() => rmSync(root, { recursive: true, force: true }));
const fake = (...parts) => { // an executable file at root/parts
  const p = resolve(root, ...parts);
  mkdirSync(join(p, ".."), { recursive: true });
  writeFileSync(p, "#!/bin/sh\n"); chmodSync(p, 0o755);
  return p;
};
const shell = (cache, v) => fake(cache, "chrome-headless-shell", `mac_arm-${v}`, "chrome-headless-shell-mac-arm64", "chrome-headless-shell");
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
    assert.equal(findChrome(undefined, { ...env, PATH: join(root, "bin") }), "chrome-headless-shell");
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
