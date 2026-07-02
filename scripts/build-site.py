#!/usr/bin/env python3
"""build-site.py — assemble the GitHub Pages site into _site/.

The field guide is the site's front door; the agentic system map keeps its
page at /map/. Markdown stays the single source: pages are rendered at build
time, links between guide pages become .html links, and links into the rest
of the repository become GitHub blob URLs.

Requires the `markdown` package (pip install markdown). Run from anywhere:
    python3 scripts/build-site.py
Output: _site/ at the repository root (gitignored).
"""

import re
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"
REPO_URL = "https://github.com/risaac09/rubinstein-productions-toolkit"

# source markdown -> (output name, page title)
PAGES = {
    "docs/field-guide/index.md": ("index.html", "Field Guide"),
    "docs/field-guide/writers.md": ("writers.html", "For Writers"),
    "docs/field-guide/producers.md": ("producers.html", "For Producers"),
    "docs/field-guide/evaluators.md": ("evaluators.html", "For Evaluators"),
    "docs/ROADMAP.md": ("roadmap.html", "Roadmap"),
}

NAV = [
    ("index.html", "Start Here"),
    ("writers.html", "Writers"),
    ("producers.html", "Producers"),
    ("evaluators.html", "Evaluators"),
    ("roadmap.html", "Roadmap"),
    ("map/", "System Map"),
    (REPO_URL, "GitHub"),
]

CSS = """\
:root {
  --bg: #faf9f5; --ink: #1f1e1a; --muted: #5c5a52;
  --accent: #8a5c00; --line: #e0ddd2; --panel: #f2f0e8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --ink: #e8e6d9; --muted: #9aa3ad;
    --accent: #e8a949; --line: #2a313b; --panel: #171b21;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.skip-link {
  position: absolute; left: -9999px; top: 0; background: var(--accent);
  color: var(--bg); padding: 8px 16px; z-index: 10;
}
.skip-link:focus { left: 0; }
nav.site {
  display: flex; gap: 8px; flex-wrap: wrap; padding: 14px 16px;
  border-bottom: 1px solid var(--line); max-width: 760px; margin: 0 auto;
}
nav.site a {
  font-size: 13px; color: var(--muted); text-decoration: none;
  border: 1px solid var(--line); border-radius: 999px; padding: 5px 12px;
}
nav.site a[aria-current="page"] { color: var(--ink); border-color: var(--accent); }
nav.site a:hover, nav.site a:focus { color: var(--ink); }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
h1, h2, h3 { line-height: 1.25; letter-spacing: -0.01em; }
h1 { font-size: 30px; margin: 20px 0 10px; }
h2 { font-size: 22px; margin: 32px 0 8px; }
a { color: var(--accent); }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
code {
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  font-size: 0.9em; background: var(--panel); border-radius: 4px;
  padding: 1px 5px;
}
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 15px; }
th, td { border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }
th { background: var(--panel); }
footer {
  max-width: 760px; margin: 0 auto; padding: 16px; color: var(--muted);
  font-size: 13px; border-top: 1px solid var(--line);
}
"""

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Rubinstein Productions Toolkit</title>
<link rel="stylesheet" href="site.css">
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<nav class="site" aria-label="Site">
{nav}
</nav>
<main id="main">
{body}
</main>
<footer>
Rendered from <a href="{source_url}">{source_path}</a> ·
MIT licensed · <a href="{repo}">Repository</a>
</footer>
</body>
</html>
"""


def rewrite_links(text: str) -> str:
    # Repo-relative links (../../path from docs/field-guide/) -> GitHub URLs;
    # directories (trailing slash) need /tree/, files /blob/.
    text = re.sub(r"\]\(\.\./\.\./([^)]+/)\)", rf"]({REPO_URL}/tree/main/\1)", text)
    text = re.sub(r"\]\(\.\./\.\./([^)]+)\)", rf"]({REPO_URL}/blob/main/\1)", text)
    # Sibling guide pages -> rendered pages.
    return re.sub(r"\]\(([A-Za-z-]+)\.md\)", r"](\1.html)", text)


def nav_html(current: str) -> str:
    items = []
    for href, label in NAV:
        cur = ' aria-current="page"' if href == current else ""
        items.append(f'<a href="{href}"{cur}>{label}</a>')
    return "\n".join(items)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "map").mkdir(parents=True)

    (OUT / "site.css").write_text(CSS)
    shutil.copy(ROOT / "architecture" / "index.html", OUT / "map" / "index.html")

    md = markdown.Markdown(extensions=["tables", "fenced_code"])
    for src, (name, title) in PAGES.items():
        body = md.reset().convert(rewrite_links((ROOT / src).read_text()))
        page = TEMPLATE.format(
            title=title,
            nav=nav_html(name),
            body=body,
            source_path=src,
            source_url=f"{REPO_URL}/blob/main/{src}",
            repo=REPO_URL,
        )
        (OUT / name).write_text(page)
        print(f"  {src} -> _site/{name}")
    print(f"build-site: {len(PAGES)} pages + map -> {OUT}")


if __name__ == "__main__":
    main()
