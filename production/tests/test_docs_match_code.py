"""
Doc claims that the code can contradict:
- requirements.txt says its table names every variable the tools read;
- media-lane.md names the commands that write without the git-tree check;
- resolve-template-spec.md says how apply_grade puts the look on node 8;
- resolve-traps.md T6 and T17, and fusion-macros.md's dip row, say what the code and config hold;
- no shipped method asks one item for two comps; production/ docs and code name no author.

Stdlib only. Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import re
import sys
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROD = HERE.parent
ROOT = PROD.parent
sys.path.insert(0, str(PROD))

from rpresolve import cmd_effect, drafts, grade, methods, reframe  # noqa: E402

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


def skipped_dir(name):
    """Not the toolkit's own code: tests, dependencies, caches, and any virtualenv
    (hidden, like .venv, or named venv or site-packages), which an untracked local
    checkout can carry under a tool's folder."""
    return (name in ("tests", "node_modules", "__pycache__", "venv", "site-packages")
            or name.startswith("."))


def env_reads(root=ROOT):
    found = {}
    for top in ("production", "tools", "scripts"):
        for d, dirs, names in os.walk(root / top):
            dirs[:] = [x for x in dirs if not skipped_dir(x)]
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
                        found.setdefault(v, str(p.relative_to(root)))
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

    def test_the_magic_mask_recipe_is_there_and_says_what_ran(self):
        text = " ".join((PROD / "fusion-macros.md").read_text(encoding="utf-8").split())
        self.assertIn("## Person matte: the Magic Mask recipe", text)  # the anchor two docs link to
        self.assertIn("run live 2026-11-11", text)
        self.assertIn("sandbox project", text)
        self.assertIn("](#person-matte-the-magic-mask-recipe)", text)  # from "Needs a person's eye"
        self.assertIn("](fusion-macros.md#person-matte-the-magic-mask-recipe)",
                      (PROD / "edit-methods.md").read_text(encoding="utf-8"))

    def test_edit_methods_not_drawn_list_is_the_macros_the_draft_cannot_draw(self):
        text = " ".join((PROD / "edit-methods.md").read_text(encoding="utf-8").split())
        m = re.search(r"Not drawn: (.*?)(?:, which have no stand-in| and the UI steps)", text)
        self.assertTrue(m, "edit-methods.md lost its Not drawn sentence")
        named = set(re.findall(r"`([a-z-]+)`", m.group(1)))
        macros = {p.stem for p in (PROD / "fusion-macros").glob("*.setting")}
        self.assertEqual(named, macros - drafts.DRAWN)


    def test_env_scan_ignores_a_local_virtualenv(self):
        # A retired tool's folder can survive on a machine as an untracked .venv; its
        # activate script reads VIRTUAL_ENV and the scan reported it as the toolkit's.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in (
                    ("tools/a/run.sh", 'echo "${REAL_VAR:-x}"\n'),
                    ("tools/a/.venv/bin/activate", 'VIRTUAL_ENV="${VIRTUAL_ENV:-}"\n'),
                    ("tools/a/venv/bin/activate", 'VIRTUAL_ENV="${VIRTUAL_ENV:-}"\n'),
                    ("tools/a/lib/site-packages/m.py", 'import os\nos.environ.get("PKG_VAR")\n'),
                    ("production/.keep/x.py", 'import os\nos.environ.get("HIDDEN_VAR")\n'),
                    ("scripts/s.sh", "")):
                f = root / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
            self.assertEqual(sorted(env_reads(root)), ["REAL_VAR"])

    def test_t17_fps_floor_is_the_configs(self):
        cfg = json.loads((PROD / "resolve-config.json").read_text(encoding="utf-8"))
        floors = {k: d["min_fps"] for k, d in cfg["destinations"].items() if d.get("min_fps")}
        self.assertEqual(floors, {"social_9x16": "30000/1001"})
        self.assertNotIn("min_fps", cfg["destinations"]["social_9x16_music"])
        t17 = next(x for x in (PROD / "resolve-traps.md").read_text(encoding="utf-8").splitlines()
                   if x.startswith("- **T17."))
        self.assertIn("`min_fps` 30000/1001", t17)
        self.assertIn(f"`social_9x16` alone carries a {float(Fraction(floors['social_9x16'])):.2f} fps floor", t17)
        self.assertNotIn("prefer 30 fps", t17)
        self.assertEqual(cfg["destinations"]["social_9x16_music"]["captions"], "none")
        self.assertIn("`social_9x16_music` has no captions and no floor", t17)

    def test_t6_pan_has_no_separate_zoom_factor(self):
        # T6: Pan = shift x timeline width / fitted picture width, the shift already holding
        # the zoom. For a 16:9 source filling a 9:16 timeline that is T15's formula.
        src, out = (3840, 2160), (1080, 1920)
        fit = reframe.fit_factor(src, out, reframe.SCALE_TO_CROP)
        for zoom in (1.0, 1.5):
            for cx in (0.3, 0.5, 0.62):
                scale = zoom * fit
                p = reframe.resolve_props(src, out, scale, (cx * src[0], src[1] / 2.0),
                                          reframe.SCALE_TO_CROP)
                shift = (src[0] / 2.0 - cx * src[0]) * scale
                self.assertAlmostEqual(p["Pan"], shift * out[0] / (src[0] * fit), places=6)
                self.assertAlmostEqual(p["Pan"], (0.5 - cx) * 1080 * zoom, places=6)
        # at 16:9 into 16:9 the fitted picture spans the timeline: Pan is output pixels
        p = reframe.resolve_props((3840, 2160), (1920, 1080), 0.75, (1800.0, 1080.0))
        self.assertAlmostEqual(p["Pan"], (3840 / 2.0 - 1800.0) * 0.75, places=6)
        traps = (PROD / "resolve-traps.md").read_text(encoding="utf-8")
        t6 = next(x for x in traps.splitlines() if x.startswith("- **T6."))
        # the doc's formula is the one above, with no zoom factor of its own
        self.assertEqual(re.search(r"Pan = ([^,.]*)", t6).group(1),
                         "shift x timeline width / fitted picture width")
        self.assertNotIn("scaled image width x item zoom", t6)
        # Tilt carries the opposite sign: the shift is y down, Resolve's Tilt is y up
        up = reframe.resolve_props(src, out, fit, (src[0] / 2.0, src[1] * 0.3), reframe.SCALE_TO_CROP)
        self.assertLess(up["Tilt"], 0)
        self.assertIn("Tilt is the same on the height with the opposite sign", t6)
        self.assertIn("y down", t6)

    def test_dip_row_names_the_keys_the_setting_holds(self):
        text = (PROD / "fusion-macros" / "dip-to-black.setting").read_text(encoding="utf-8")
        gain = text.split("BrightnessContrast1Gain = BezierSpline", 1)[1]
        keys = {int(f): float(v) for f, v in re.findall(r"\[(\d+)\] = \{ ([\d.]+)", gain)}
        fall = sorted(f for f in keys if f)
        self.assertEqual((keys[0], keys[fall[0]], keys[fall[-1]]), (1.0, 1.0, 0.0))
        self.assertEqual(fall[-1] - fall[0], 18)
        doc = (PROD / "fusion-macros.md").read_text(encoding="utf-8")
        row = next(x for x in doc.splitlines() if x.startswith("| `dip-to-black`"))
        self.assertIn(f"frames {fall[0]} to {fall[-1]}", row)
        self.assertIn("hold at 1.0 on frame 0", row)
        # the row's list of values and its spacing are the .setting's
        listed = re.search(r"Gain` 1\.0, then ([\d., ]+) every (\d+) frames", row)
        self.assertEqual([float(v) for v in listed.group(1).split(",")], [keys[f] for f in fall[1:]])
        self.assertEqual({b - a for a, b in zip(fall, fall[1:])}, {int(listed.group(2))})
        # apply_effect moves every key together so the last lands on the item's last frame,
        # which puts the hold before frame 0 on an item shorter than the macro (T25)
        _, parsed = cmd_effect.load_macro("dip-to-black")

        def moved(frames, source_frames):
            k, off = cmd_effect.comp_timing(parsed, "dip-to-black", frames, source_frames)
            spline = cmd_effect.shifted(cmd_effect.scaled(parsed, k), off)["Tools"][
                "BrightnessContrast1Gain"]["KeyFrames"]
            return sorted(f for f in spline if isinstance(f, int))
        self.assertEqual(moved(112, 112)[-1], 111)
        self.assertLess(moved(112, 112)[0], 0)
        self.assertEqual(moved(168, 168)[0], 0)
        self.assertGreater(moved(200, 200)[0], 0)
        self.assertIn("shorter than the macro (T25)", row)
        traps = (PROD / "resolve-traps.md").read_text(encoding="utf-8")
        t25 = next(x for x in traps.splitlines() if x.startswith("- **T25."))
        self.assertIn("item shorter than the macro", t25)
        self.assertNotIn("longer than the macro", t25)
        # T25's measured item: 90 frames of 112 source frames
        keys25 = moved(90, 112)
        self.assertIn(f"[{keys25[0]}, 0, {keys25[1]}, \u2026, {keys25[-1]}]", t25)

    def test_a_shipped_slot_holds_one_comp(self):
        # apply_effect refuses an item that already has a comp, so a slot's word needs a
        # text macro: with exposure-flash it made to_manifest emit a text-word step for a
        # comp that cannot exist on that item.
        for m in methods.list_methods():
            for i, s in enumerate(m["slots"], 1):
                if s["text"] is not None:
                    self.assertIn(s["effect"], methods.TEXT_MACROS, f"{m['id']} slot {i}")

    def test_production_names_no_author(self):
        # The repo is public: a procedure's actor is the operator (CLAUDE.md). Any text file
        # under production/ (tests aside) may name the repo's own URL and nothing else of him.
        name = re.compile(r"isaac|risaac09", re.I)
        own_repo = "github.com/risaac09/rubinstein-productions-toolkit"
        hits = []
        for d, dirs, names in os.walk(PROD):
            dirs[:] = [x for x in dirs if not skipped_dir(x)]
            for n in names:
                p = Path(d) / n
                try:
                    lines = p.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue  # a binary file
                for k, line in enumerate(lines, 1):
                    if name.search(line.replace(own_repo, "")):
                        hits.append(f"{p.relative_to(ROOT)}:{k}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
