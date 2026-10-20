"""
Offline tests for rpresolve.motionlint. Pure Python, no media, no Resolve.

Run: /usr/bin/python3 -m unittest production.tests.test_motionlint -v
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from rpresolve import motionlint  # noqa: E402

GOOD = json.loads((Path(__file__).parent / "fixtures" / "motion" / "condense.json").read_text())


def codes(spec):
    return {c for c, _, _ in motionlint.lint(spec)}


def mutated(fn):
    s = copy.deepcopy(GOOD)
    fn(s)
    return s


class MotionLint(unittest.TestCase):
    def test_condense_passes(self):
        self.assertEqual(motionlint.lint(GOOD), [])

    def test_bans(self):
        # decision 24: each ban has a fixture
        self.assertIn("ban-name", codes(mutated(lambda s: s["curves"].update(elastic=[0.3, 0, 0.7, 1]))))
        self.assertIn("ban-name", codes(mutated(lambda s: s["curves"].update(bounceOut=[0.3, 0, 0.7, 1]))))
        self.assertIn("overshoot", codes(mutated(lambda s: s["curves"].update(backOut=[0.34, 1.56, 0.64, 1]))))
        self.assertIn("overshoot", codes(mutated(lambda s: s["curves"].update(dip=[0.3, -0.2, 0.7, 1]))))
        self.assertIn("ban-name", codes(mutated(lambda s: s["segments"][1]["tweens"][0].update(ease="easeOutBack"))))

    def test_curve_edges_inside_are_fine(self):
        self.assertEqual(codes(mutated(lambda s: s["curves"].update(flat=[0, 0, 1, 1.0]))), set())

    def test_bad_curves(self):
        self.assertIn("bad-curve", codes(mutated(lambda s: s["curves"].update(x=[1.2, 0, 0.5, 1]))))
        self.assertIn("bad-curve", codes(mutated(lambda s: s["curves"].update(x=[0, 0, 1]))))
        self.assertIn("bad-curve", codes(mutated(lambda s: s["curves"].update(x=[0, 0, 1, float("nan")]))))

    def test_unknown_ease(self):
        self.assertIn("unknown-ease", codes(mutated(lambda s: s["segments"][1]["tweens"][0].update(ease="nope"))))

    def test_uneased_transform(self):
        self.assertIn("uneased", codes(mutated(lambda s: s["segments"][1]["tweens"][0].update(ease="linear"))))
        self.assertIn("uneased", codes(mutated(lambda s: s["segments"][1]["tweens"][0].pop("ease"))))
        # opacity may stay linear
        self.assertEqual(codes(mutated(lambda s: s["segments"][2]["tweens"][1].update(ease="linear"))), set())

    def test_sum_and_hold(self):
        self.assertIn("sum", codes(mutated(lambda s: s.update(length=121))))
        self.assertIn("min-hold", codes(mutated(lambda s: s["segments"][0].update(frames=6))))

    def test_tween_fit_counts_stagger(self):
        # 20 frames + 6 stagger overruns a 24-frame segment
        self.assertIn("tween-fit", codes(mutated(lambda s: s["segments"][2].update(frames=24))))

    def test_cues(self):
        self.assertIn("cue", codes(mutated(lambda s: s["cues"].update(lock=120))))
        self.assertIn("cue", codes(mutated(lambda s: s["cues"].update(lock=78.5))))

    def test_schema(self):
        self.assertIn("schema", codes([]))
        self.assertIn("schema", codes(mutated(lambda s: s.pop("fps"))))
        self.assertIn("schema", codes(mutated(lambda s: s["segments"][0].update(kind="pause"))))
        self.assertIn("schema", codes(mutated(lambda s: s["segments"][0].update(tweens=[{}]))))
        # hostile shapes report, they do not raise
        self.assertIn("schema", codes(mutated(lambda s: s["segments"][1]["tweens"][0].update(ease=["out"]))))
        self.assertIn("schema", codes(mutated(lambda s: s.update(cues=[78]))))
        self.assertIn("schema", codes(mutated(lambda s: s.update(fps=True))))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text(json.dumps(mutated(lambda s: s["curves"].update(elastic=[0, 0, 1, 1]))))
            run = lambda p: subprocess.run([sys.executable, "-m", "rpresolve.motionlint", str(p)],
                                           cwd=PROD, capture_output=True, text=True)
            self.assertEqual(run(Path(__file__).parent / "fixtures" / "motion" / "condense.json").returncode, 0)
            r = run(bad)
            self.assertEqual(r.returncode, 1)
            self.assertIn("ban-name", r.stdout)
            self.assertEqual(run(Path(d) / "missing.json").returncode, 2)


if __name__ == "__main__":
    unittest.main()
