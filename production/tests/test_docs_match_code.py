"""
Doc claims that the code can contradict:
- requirements.txt says its table names every variable the tools read;
- media-lane.md names the commands that write without the git-tree check;
- resolve-template-spec.md says how apply_grade puts the look on node 8.

Stdlib only. Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROD = HERE.parent
ROOT = PROD.parent
sys.path.insert(0, str(PROD))

from rpresolve import grade  # noqa: E402

ENV_READS = [re.compile(r"environ(?:\.get)?\s*[(\[]\s*[\"']([A-Z][A-Z0-9_]+)"),
             re.compile(r"getenv\(\s*[\"']([A-Z][A-Z0-9_]+)"),
             re.compile(r"process\.env\.([A-Z][A-Z0-9_]+)"),
             re.compile(r"\$\{([A-Z][A-Z0-9_]+):?[-=]"),
             # a name kept in a constant, read later as os.environ.get(RNNOISE_ENV)
             re.compile(r"^\s*[A-Z_]*ENV[A-Z_]*\s*=\s*[\"']([A-Z][A-Z0-9_]+)[\"']", re.M)]
# Read but not the toolkit's to document: the shell's own, Hugging Face's cache
# variables, offload.sh's re-exec marker and vertcut's own temp folder.
NOT_OURS = {"HOME", "PATH", "TMPDIR", "USER", "HF_HOME", "HF_HUB_CACHE",
            "OFFLOAD_CAFFEINATED", "VERTCUT_TMP"}


def env_reads():
    found = {}
    for top in ("production", "tools", "scripts"):
        for d, dirs, names in os.walk(ROOT / top):
            dirs[:] = [x for x in dirs if x not in ("tests", "node_modules", "__pycache__")]
            for n in names:
                p = Path(d) / n
                if p.suffix not in (".py", ".sh", ".mjs", ".js", ".cpp", ""):
                    continue
                try:
                    text = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for pat in ENV_READS:
                    for v in pat.findall(text):
                        found.setdefault(v, str(p.relative_to(ROOT)))
    return found


class DocsMatchCode(unittest.TestCase):
    def test_requirements_names_every_env_var(self):
        # only the Variable column of the table counts, not prose that happens to match
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        table = {v.strip() for line in lines if line.startswith("#   ")
                 for v in line[4:].split("  ")[0].split(",")}
        missing = {v: f for v, f in env_reads().items() if v not in NOT_OURS and v not in table}
        self.assertEqual(missing, {})

    def test_media_lane_unchecked_list_is_current(self):
        text = " ".join((PROD / "media-lane.md").read_text(encoding="utf-8").split())
        m = re.search(r"These refuse to write inside a git tree:(.*?)without checking", text)
        self.assertTrue(m, "media-lane.md lost its git-tree sentence")
        guarded, unchecked = m.group(1).rsplit(". ", 1)
        # rpstills/__main__.py guards these with out_problem before writing.
        for cmd in ("index", "run", "cluster", "cull", "review", "crops"):
            self.assertIn(f"`{cmd}`", guarded)
            self.assertNotIn(f"`{cmd}`", unchecked)

    def test_template_spec_says_overwrite_for_the_look(self):
        labels = ["NR", "EXP", "WB", "CON", "SAT", "SKIN", "WIN", "LOOK", "TEX", "CST OUT", "LUT"]
        self.assertFalse(grade.is_default_graph([(lb, "", None) for lb in labels]))
        text = " ".join((PROD / "resolve-template-spec.md").read_text(encoding="utf-8").split())
        # every place that tells you to pass node 8 also says overwrite, close after it
        starts = [m.start() for m in re.finditer(r"`lut\.node` 8", text)]
        self.assertTrue(starts)
        for i in starts:
            self.assertIn("`overwrite` true", text[i:i + 400])
        self.assertNotIn("The only node a script writes", text)


if __name__ == "__main__":
    unittest.main()
