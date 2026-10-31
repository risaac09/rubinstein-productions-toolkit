"""
The offline reel lane in media-lane-quickstart.md (section 6) cannot drift from the code:
every resolve_workflow.py subcommand it runs is registered, and every long flag it uses
appears in that command's --help. vertcut flags are checked when vertcut runs here (it
needs its own venv; skipped where it does not). Stdlib only.
Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

PROD = Path(__file__).resolve().parent.parent
DOC = PROD / "media-lane-quickstart.md"
VERTCUT = PROD.parent / "tools" / "vertcut" / "vertcut"


def section6_commands():
    text = DOC.read_text()
    body = re.split(r"^## 6\. ", text, flags=re.M)[1].split("\n## ")[0]
    cmds = []
    for block in re.findall(r"```bash\n(.*?)```", body, flags=re.S):
        block = re.sub(r"<<'EOF'\n.*?\nEOF\n", "\n", block, flags=re.S)  # heredoc bodies are data
        for line in block.replace("\\\n", " ").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                cmds.append(shlex.split(line))
    return cmds


def has_flag(help_out, flag):
    """The whole flag, not a prefix of a longer one (--review is not --review-log)."""
    return re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", help_out) is not None


def help_text(argv):
    r = subprocess.run(argv + ["--help"], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class QuickstartReels(unittest.TestCase):
    def test_every_command_and_flag_exists(self):
        skipped = False
        seen = {"resolve": set(), "vertcut": set(), "offload": set()}
        for argv in section6_commands():
            flags = [t.split("=")[0] for t in argv if re.match(r"--[a-z][a-z0-9-]*", t)]
            for i, tok in enumerate(argv):
                if tok == "resolve_workflow.py":
                    sub = argv[i + 1]
                    code, out = help_text([sys.executable, str(PROD / "resolve_workflow.py"), sub])
                    self.assertEqual(code, 0, f"{sub} is not a registered subcommand")
                    for f in flags:
                        self.assertTrue(has_flag(out, f), f"{sub} has no {f}")
                    seen["resolve"].add(sub)
                elif tok.endswith("vertcut/vertcut"):
                    sub = argv[i + 1]
                    seen["vertcut"].add(sub)
                    code, out = help_text([str(VERTCUT), sub])
                    if code != 0:
                        skipped = True  # vertcut's own dependencies are absent here
                        continue
                    for f in flags:
                        self.assertTrue(has_flag(out, f), f"vertcut {sub} has no {f}")
                elif tok == "./offload.sh":
                    seen["offload"].add(argv[i + 1])
                    script = (PROD / "offload.sh").read_text()
                    self.assertIn(f"offload.sh {argv[i + 1]}", script)
                    for f in flags:
                        self.assertIn(f, script, f"offload.sh has no {f}")
        self.assertEqual(seen["resolve"], {"transcribe", "reel-spans", "gate-packet", "review-log", "reel-ab",
                                           "deliver-check", "qc", "release-check"})
        self.assertEqual(seen["vertcut"], {"manifest", "cut"})
        self.assertEqual(seen["offload"], {"id", "copy"})
        if skipped:
            self.skipTest("vertcut --help did not run here; its flags were not checked")


if __name__ == "__main__":
    unittest.main()
