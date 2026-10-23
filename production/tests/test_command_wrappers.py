"""
The resolve_workflow commands that wrap an rpresolve module with its own CLI
(interchange, shots, wordsnap, beats, roomtone, dxmetrics, duckplan, captionlint,
pacing, transcribe, besttake): each
shows that module's flags under --help and hands the parsed arguments to its
run(). The modules' own tests cover the work.

Run: python3 production/tests/test_command_wrappers.py
"""

import importlib
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PRODUCTION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PRODUCTION))

from rpresolve import cli

# command: (an argv it parses, a flag its --help shows, an attribute the parsed args carry)
WRAPPERS = {
    "interchange": (["m.json", "--format", "edl", "--out", "o.edl"], "--media-root",
                    ("format", "edl")),
    "shots": (["src.mov", "--out", "o.csv"], "--no-dissolves", ("source", "src.mov")),
    "wordsnap": (["w.json", "src.wav", "--out", "o.json"], "--margin-db", ("source", "src.wav")),
    "beats": (["m.wav", "--out", "b.json"], "--prior-bpm", ("music", "m.wav")),
    "roomtone": (["find", "a.wav"], "{find,bed}", ("action", "find")),
    "dxmetrics": (["lag", "a.wav", "b.wav"], "{measure,compare,lag}", ("action", "lag")),
    "duckplan": (["plan", "d.wav", "--out", "dir"], "{plan,render}", ("action", "plan")),
    "captionlint": (["c.srt"], "--size", ("captions", "c.srt")),
    "pacing": (["--words", "w.json"], "--captions", ("words", "w.json")),
    "transcribe": (["m.mov", "--backend", "fake"], "--backend", ("backend", "fake")),
    "besttake": (["folder"], "--no-faces", ("source", "folder")),
}


def run_main(*argv):
    """cli.main with this argv, in process; (exit status, stdout)."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        try:
            status = cli.main(list(argv))
        except SystemExit as e:  # --help
            status = e.code
    return status, out.getvalue()


def module(name, test):
    """rpresolve.<name>, or skip the test where numpy is missing."""
    try:
        return importlib.import_module("rpresolve." + name)
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        test.skipTest(f"{name} needs numpy")


class TestCommandWrappers(unittest.TestCase):
    def test_each_wrapper_is_a_command(self):
        self.assertLessEqual(set(WRAPPERS), {m.NAME for m in cli.commands()})

    def test_help_shows_the_module_flags(self):
        for name, (_, flag, _) in WRAPPERS.items():
            with self.subTest(command=name):
                module(name, self)
                status, out = run_main(name, "--help")
                self.assertEqual(status, 0)
                self.assertIn(f"resolve_workflow {name}", out)
                self.assertIn(flag, out)

    def test_run_hands_the_parsed_args_to_the_module(self):
        for name, (argv, _, (attr, value)) in WRAPPERS.items():
            with self.subTest(command=name):
                mod = module(name, self)
                with mock.patch.object(mod, "run", return_value=7) as run:
                    self.assertEqual(run_main(name, *argv)[0], 7)
                self.assertEqual(getattr(run.call_args[0][0], attr), value)

    def test_without_numpy_the_parser_still_builds_and_says_so(self):
        # The stdlib-only Python builds the whole parser; numpy is blocked in a child process.
        code = ("import sys; sys.modules['numpy'] = None\n"
                "from rpresolve import cli\n"
                "cli.main(['beats', '--help'])\n")
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(PRODUCTION),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertIn("needs numpy", proc.stdout.decode())


if __name__ == "__main__":
    unittest.main()
