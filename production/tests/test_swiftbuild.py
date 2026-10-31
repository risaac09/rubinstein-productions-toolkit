import os
import stat
import tempfile
import unittest

from rpresolve import swiftbuild


def fake_swiftc(d, ok=True):
    """A stand-in compiler: writes an executable to the -o path, or fails."""
    p = os.path.join(d, "fakec")
    with open(p, "w") as f:
        f.write('#!/bin/sh\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                + ('printf "#!/bin/sh\\n" > "$out"; chmod +x "$out"\n' if ok else 'printf x > "$out"; exit 1\n'))
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return p


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.d)
        self.src = os.path.join(self.d, "h.swift")
        self.cache = os.path.join(self.d, "cache")

    def write(self, text):
        with open(self.src, "w") as f:
            f.write(text)

    def test_failed_build_leaves_nothing(self):
        self.write("a")
        self.assertIsNone(swiftbuild.build(self.src, self.cache, fake_swiftc(self.d, ok=False)))
        self.assertEqual(os.listdir(self.cache), [])

    def test_reuse_and_rekey(self):
        self.write("a")
        c = fake_swiftc(self.d)
        first = swiftbuild.build(self.src, self.cache, c)
        self.assertTrue(os.access(first, os.X_OK))
        os.remove(c)  # a second call must not need the compiler
        self.assertEqual(swiftbuild.build(self.src, self.cache, c), first)
        self.write("b")
        self.assertIsNone(swiftbuild.build(self.src, self.cache, c))
        c = fake_swiftc(self.d)
        second = swiftbuild.build(self.src, self.cache, c)
        self.assertNotEqual(first, second)
        self.assertEqual(sorted(os.listdir(self.cache)), sorted(map(os.path.basename, (first, second))))

    def test_missing_compiler_or_source(self):
        self.write("a")
        self.assertIsNone(swiftbuild.build(self.src, self.cache, os.path.join(self.d, "nope")))
        self.assertIsNone(swiftbuild.build(os.path.join(self.d, "gone.swift"), self.cache))


if __name__ == "__main__":
    unittest.main()
