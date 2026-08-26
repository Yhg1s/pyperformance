"""Where the per-Python benchmark venvs are placed.

The default nests them inside ./venv, which is fine on its own but not when the
caller keeps its own virtual environment there: rebuilding that one takes the
benchmark venvs with it. --venvs-dir is how a caller moves them out of the way.
"""

import contextlib
import os
import os.path
import tempfile
import types
import unittest
from unittest import mock

from pyperformance import _venv, run as mrun


@contextlib.contextmanager
def chdir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


NAME = "cpython3.14-0123456789ab-compat-ba9876543210"


class GetVenvRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A temporary directory can be a symlink (/tmp on macOS), and
        # get_venv_root returns an abspath, which does not resolve one.
        self.root = os.path.abspath(self.tmp.name)

    def relative(self, *args, **kwargs):
        with chdir(self.root):
            return os.path.relpath(_venv.get_venv_root(*args, **kwargs), self.root)

    def test_default_is_unchanged(self):
        self.assertEqual(
            self.relative(NAME), os.path.join(_venv.DEFAULT_VENVS_DIR, NAME)
        )

    def test_none_means_the_default(self):
        # _setup_venvs passes options.venvs_dir straight through, and that is
        # None whenever --venvs-dir was not given.
        self.assertEqual(self.relative(NAME, None), self.relative(NAME))

    def test_explicit_directory(self):
        self.assertEqual(
            self.relative(NAME, "benchmark-venvs"),
            os.path.join("benchmark-venvs", NAME),
        )

    def test_empty_string_means_the_working_directory(self):
        self.assertEqual(self.relative(NAME, ""), NAME)

    def test_absolute_directory_is_used_as_given(self):
        target = os.path.join(self.root, "elsewhere")
        with chdir(self.root):
            self.assertEqual(
                _venv.get_venv_root(NAME, target), os.path.join(target, NAME)
            )

    def test_result_is_absolute(self):
        with chdir(self.root):
            self.assertTrue(os.path.isabs(_venv.get_venv_root(NAME, "somewhere")))

    def test_moving_out_escapes_the_outer_venv(self):
        # The actual point: with --venvs-dir the benchmark venvs are no longer
        # under ./venv, so wiping ./venv does not take them along.
        with chdir(self.root):
            outer = os.path.abspath(_venv.DEFAULT_VENVS_DIR)
            default = _venv.get_venv_root(NAME)
            moved = _venv.get_venv_root(NAME, "benchmark-venvs")
        self.assertEqual(os.path.commonpath([default, outer]), outer)
        self.assertNotEqual(os.path.commonpath([moved, outer]), outer)


class FakeVenv:
    python = "/nonexistent/python"

    def ensure_reqs(self, requirements=None):
        return None


class SetupVenvsTests(unittest.TestCase):
    """_setup_venvs() forwards --venvs-dir to every venv root it computes."""

    def setUp(self):
        self.roots = []

        def record(name=None, venvsdir=None, *, python=None):
            self.roots.append((name, venvsdir))
            return os.path.join("/fake", venvsdir or "venv", name)

        patcher = mock.patch.object(mrun._venv, "get_venv_root", record)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch.object(
            mrun, "VenvForBenchmarks", mock.Mock(ensure=lambda *a, **k: FakeVenv())
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch.object(mrun._pythoninfo, "get_info", lambda python: python)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch.object(
            mrun,
            "get_run_id",
            lambda python, bench=None: mrun.RunID("cpython3.14-aaa", "bbb", None, 0),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def options(self, **kwargs):
        base = dict(
            inherit_environ=None,
            local_deps=(),
            venvs_dir=None,
            trust_venv=False,
        )
        base.update(kwargs)
        return types.SimpleNamespace(**base)

    def benchmark(self, name):
        bench = mock.Mock()
        bench.name = name
        bench.__str__ = lambda self: name
        bench.__lt__ = lambda self, other: False
        return bench

    def test_default_leaves_venvsdir_unset(self):
        mrun._setup_venvs([self.benchmark("a")], "/fake/python", self.options())
        self.assertTrue(self.roots)
        self.assertEqual({venvsdir for _, venvsdir in self.roots}, {None})

    def test_option_reaches_every_root(self):
        mrun._setup_venvs(
            [self.benchmark("a"), self.benchmark("b")],
            "/fake/python",
            self.options(venvs_dir="benchmark-venvs"),
        )
        # Both the shared venv and the per-benchmark fallbacks.
        self.assertGreaterEqual(len(self.roots), 2)
        self.assertEqual({venvsdir for _, venvsdir in self.roots}, {"benchmark-venvs"})

    def test_missing_attribute_is_tolerated(self):
        # calibrate_benchmarks() and run_benchmarks() share _setup_venvs, and
        # other callers build their own options objects.
        options = self.options()
        del options.venvs_dir
        mrun._setup_venvs([self.benchmark("a")], "/fake/python", options)
        self.assertEqual({venvsdir for _, venvsdir in self.roots}, {None})


if __name__ == "__main__":
    unittest.main()
