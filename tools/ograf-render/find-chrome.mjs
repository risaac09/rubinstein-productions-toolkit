// Chrome binary lookup for ograf-render. A headless-only build (chrome-headless-shell) is
// preferred: it has no app bundle, so launching it never activates a GUI app on macOS.
import { accessSync, constants, readdirSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, delimiter, join } from "node:path";

const HEADLESS_SHELL = "chrome-headless-shell";
const CHROME_CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
];

// Decided on the real file, so a link named chrome-headless-shell that points at full Chrome
// still gets --headless=new (a missing flag would open a window).
export const isHeadlessShell = (bin) => {
  try { return basename(realpathSync(bin)) === HEADLESS_SHELL; } catch { return basename(bin) === HEADLESS_SHELL; }
};

// Puppeteer's platform directory prefix for this machine.
export const platformDir = (platform = process.platform, arch = process.arch) =>
  platform === "darwin" ? (arch === "arm64" ? "mac_arm" : "mac") : platform === "win32" ? (arch === "ia32" ? "win32" : "win64") : platform;

// The full path of an executable file, or null; a bare name is looked up on env.PATH.
const exe = (c, env) => {
  const ok = (p) => { try { if (!statSync(p).isFile()) return false; accessSync(p, constants.X_OK); return true; } catch { return false; } };
  if (c.includes("/")) return ok(c) ? c : null;
  const d = (env.PATH || "").split(delimiter).find((d) => d && ok(join(d, c)));
  return d ? join(d, c) : null;
};

// Puppeteer's cache layout: <cache>/chrome-headless-shell/<platform>-<version>/<dir>/chrome-headless-shell
// shortcut: version order is numeric by dotted parts, not full semver; fine for Chrome's four-part versions.
function cachedShells(env, platform, arch) {
  const root = join(env.PUPPETEER_CACHE_DIR || join(homedir(), ".cache", "puppeteer"), HEADLESS_SHELL);
  const num = (d) => d.replace(/^[^-]*-/, "").split(".").map(Number);
  const cmp = (a, b) => { const x = num(a), y = num(b); for (let i = 0; i < 4; i++) if ((y[i] || 0) !== (x[i] || 0)) return (y[i] || 0) - (x[i] || 0); return 0; };
  try {
    return readdirSync(root).filter((v) => v.startsWith(platformDir(platform, arch) + "-")).sort(cmp).flatMap((v) => {
      try { return readdirSync(join(root, v)).map((d) => join(root, v, d, HEADLESS_SHELL)); } catch { return []; }
    });
  } catch { return []; }
}

// Order: explicit arg, CHROME env, headless-shell (PATH, then puppeteer cache), the usual Chrome names.
// A browser the user named that is not an executable file is an error (code 3), never a fallback.
export function findChrome(arg, env = process.env, platform = process.platform, arch = process.arch) {
  const named = arg || env.CHROME;
  if (named) {
    const hit = exe(named, env);
    if (!hit) throw Object.assign(new Error(`${named} is not an executable file`), { code: 3 });
    return hit;
  }
  for (const c of [HEADLESS_SHELL, ...cachedShells(env, platform, arch), ...CHROME_CANDIDATES]) {
    const hit = exe(c, env);
    if (hit) return hit;
  }
  return null;
}
