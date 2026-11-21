// Chrome binary lookup for ograf-render. A headless-only build (chrome-headless-shell) is
// preferred: it has no app bundle, so launching it never activates a GUI app on macOS.
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { basename, delimiter, join } from "node:path";

const HEADLESS_SHELL = "chrome-headless-shell";
const CHROME_CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
];

export const isHeadlessShell = (bin) => basename(bin) === HEADLESS_SHELL;

const found = (c, env) => (c.includes("/") ? existsSync(c) : (env.PATH || "").split(delimiter).some((d) => d && existsSync(join(d, c))));

// Puppeteer's cache layout: <cache>/chrome-headless-shell/<platform>-<version>/<dir>/chrome-headless-shell
// shortcut: version order is numeric by dotted parts, not full semver; fine for Chrome's four-part versions.
function cachedShells(env) {
  const root = join(env.PUPPETEER_CACHE_DIR || join(homedir(), ".cache", "puppeteer"), HEADLESS_SHELL);
  const num = (d) => d.replace(/^[^-]*-/, "").split(".").map(Number);
  const cmp = (a, b) => { const x = num(a), y = num(b); for (let i = 0; i < 4; i++) if ((y[i] || 0) !== (x[i] || 0)) return (y[i] || 0) - (x[i] || 0); return 0; };
  try {
    return readdirSync(root).sort(cmp).flatMap((v) => {
      try { return readdirSync(join(root, v)).map((d) => join(root, v, d, HEADLESS_SHELL)); } catch { return []; }
    });
  } catch { return []; }
}

// Order: explicit arg, CHROME env, headless-shell (PATH, then puppeteer cache), the usual Chrome names.
export function findChrome(arg, env = process.env) {
  const tries = [arg, env.CHROME, HEADLESS_SHELL, ...cachedShells(env), ...CHROME_CANDIDATES].filter(Boolean);
  const hit = tries.find((c) => found(c, env));
  return hit ?? null;
}
