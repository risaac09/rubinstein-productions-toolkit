"""overlay_fresh: the optional stale-overlay refusal that queue_render runs first."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rpresolve import overlay_fresh
from rpresolve.workflows import Refused


def check(cmd):
    return overlay_fresh.refuse_if_stale({overlay_fresh.ENV: cmd})


class TestOverlayFresh(unittest.TestCase):
    def test_unset_says_nothing(self):
        check("")
        overlay_fresh.refuse_if_stale({})

    def test_fresh_passes(self):
        check("true")

    def test_stale_refuses_with_its_message(self):
        with self.assertRaisesRegex(Refused, "is stale.*nothing is queued.*old build"):
            check(f"{sys.executable} -c \"import sys; sys.stderr.write('old build'); sys.exit(1)\"")

    def test_other_exit_is_unverified(self):
        with self.assertRaisesRegex(Refused, "could not be verified"):
            check("no-such-program-xyz")
        with self.assertRaisesRegex(Refused, "could not be verified \\(exit 2\\)"):
            check(f"{sys.executable} -c \"import sys; sys.exit(2)\"")


if __name__ == "__main__":
    unittest.main()
