"""How VenvForBenchmarks treats a dependency taken from a local checkout.

These exercise the decision-making -- what gets installed, how often, and what
gets dropped -- with pip stubbed out, so they run without a network or a real
virtual environment.
"""

import os
import os.path
import tempfile
import unittest
from unittest import mock

from pyperformance import _localdeps, venv as mvenv
from pyperformance.tests.test_localdeps import make_checkout


class FakeVenv(mvenv.VenvForBenchmarks):
    """A VenvForBenchmarks that records pip calls instead of making them."""

    def __init__(self, root, local_deps=None):
        # Deliberately not calling super().__init__: it asserts that a real
        # interpreter exists under `root`.
        self.root = root
        self.inherit_environ = None
        self.local_deps = local_deps
        self.installed = []
        self.editable_installs = []

    @property
    def python(self):
        return "/nonexistent/python"

    @property
    def info(self):
        return "/nonexistent/python"

    @property
    def _env(self):
        return {}


class LocalDepVenvTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "venv")
        os.makedirs(self.root)

        def fake_install_requirements(*reqs, **kwargs):
            self.calls.append(("install", tuple(reqs)))
            return (0, "", "")

        def fake_install_editable(path, **kwargs):
            self.calls.append(("editable", path))
            return (0, "", "")

        def fake_run_pip(cmd, *args, **kwargs):
            return (0, "", "")

        self.calls = []
        for name, impl in (
            ("install_requirements", fake_install_requirements),
            ("install_editable", fake_install_editable),
            ("run_pip", fake_run_pip),
        ):
            patcher = mock.patch.object(mvenv._pip, name, impl)
            patcher.start()
            self.addCleanup(patcher.stop)

    def checkout(self, name="pyperf"):
        return make_checkout(self.tmp.name, name)


class SyncLocalDepsTests(LocalDepVenvTestCase):
    def test_no_local_deps_does_nothing(self):
        venv = FakeVenv(self.root)
        self.assertEqual(venv.sync_local_deps(), ())
        self.assertEqual(self.calls, [])

    def test_installs_once_and_records_a_marker(self):
        path = self.checkout()
        dep = _localdeps.LocalDep("pyperf", path, editable=True)
        venv = FakeVenv(self.root, [dep])

        self.assertEqual(len(venv.sync_local_deps()), 1)
        self.assertEqual(self.calls, [("editable", path)])
        self.assertEqual(
            _localdeps.read_marker(self.root), {"pyperf": dep.fingerprint()}
        )

    def test_second_sync_is_a_no_op(self):
        # This is the property the whole feature turns on: ensure_reqs() runs
        # once per benchmark, and pip reinstalls a local path every time it is
        # asked, so a hundred benchmarks must still mean one install.
        path = self.checkout()
        venv = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", path, True)])
        venv.sync_local_deps()
        self.calls.clear()

        for _ in range(100):
            self.assertEqual(venv.sync_local_deps(), ())
        self.assertEqual(self.calls, [])

    def test_a_fresh_venv_object_still_sees_the_marker(self):
        # Each pyperformance invocation builds new venv objects; the marker is
        # what carries "already installed" across them.
        path = self.checkout()
        FakeVenv(
            self.root, [_localdeps.LocalDep("pyperf", path, True)]
        ).sync_local_deps()
        self.calls.clear()

        again = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", path, True)])
        self.assertEqual(again.sync_local_deps(), ())
        self.assertEqual(self.calls, [])

    def test_reinstalls_when_a_copy_install_changed(self):
        path = self.checkout()
        venv = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", path, False)])
        venv.sync_local_deps()
        self.calls.clear()

        src = os.path.join(path, "pyperf", "__init__.py")
        os.utime(src, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
        self.assertEqual(len(venv.sync_local_deps()), 1)
        self.assertEqual(self.calls, [("install", (path,))])

    def test_reinstalls_when_the_path_changed(self):
        first = self.checkout("aaa")
        second = self.checkout("bbb")
        FakeVenv(
            self.root, [_localdeps.LocalDep("pyperf", first, True)]
        ).sync_local_deps()
        self.calls.clear()

        venv = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", second, True)])
        self.assertEqual(len(venv.sync_local_deps()), 1)
        self.assertEqual(self.calls, [("editable", second)])

    def test_only_the_stale_dep_is_reinstalled(self):
        a = self.checkout("aaa")
        b = self.checkout("bbb")
        deps = [
            _localdeps.LocalDep("aaa", a, editable=True),
            _localdeps.LocalDep("bbb", b, editable=False),
        ]
        FakeVenv(self.root, deps).sync_local_deps()
        self.calls.clear()

        os.utime(
            os.path.join(b, "bbb", "__init__.py"),
            ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000),
        )
        venv = FakeVenv(self.root, deps)
        self.assertEqual([d.name for d in venv.sync_local_deps()], ["bbb"])
        self.assertEqual(self.calls, [("install", (b,))])

    def test_install_failure_raises_and_is_not_recorded(self):
        path = self.checkout()
        with mock.patch.object(
            mvenv._pip, "install_editable", lambda *a, **k: (1, "", "boom")
        ):
            venv = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", path, True)])
            with self.assertRaises(mvenv._venv.RequirementsInstallationFailedError):
                venv.sync_local_deps()
        self.assertEqual(_localdeps.read_marker(self.root), {})


class DropLocalDepsTests(LocalDepVenvTestCase):
    def test_pyperf_is_not_injected_when_local(self):
        path = self.checkout()
        venv = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", path, True)])
        bench = mock.Mock()
        bench.requirements_lockfile = os.path.join(self.tmp.name, "missing.txt")

        venv.ensure_reqs(bench)

        installed = [c for c in self.calls if c[0] == "install"]
        for _, reqs in installed:
            for req in reqs:
                self.assertNotIn("pyperf", str(req))

    def test_pyperf_is_still_injected_when_not_local(self):
        venv = FakeVenv(self.root)
        bench = mock.Mock()
        bench.requirements_lockfile = os.path.join(self.tmp.name, "missing.txt")

        venv.ensure_reqs(bench)

        flat = [str(r) for _, reqs in self.calls for r in reqs]
        self.assertTrue(
            any("pyperf" in r for r in flat),
            f"expected an injected pyperf requirement, got {flat}",
        )

    def test_a_benchmark_pinning_pyperf_cannot_clobber_the_local_one(self):
        path = self.checkout()
        lockfile = os.path.join(self.tmp.name, "requirements.txt")
        with open(lockfile, "w") as fd:
            fd.write("pyperf==2.9.0\nsix==1.16.0\n")

        venv = FakeVenv(self.root, [_localdeps.LocalDep("pyperf", path, True)])
        bench = mock.Mock()
        bench.requirements_lockfile = lockfile

        venv.ensure_reqs(bench)

        flat = [str(r) for _, reqs in self.calls for r in reqs]
        self.assertNotIn("pyperf==2.9.0", flat)
        self.assertIn("six==1.16.0", flat)

    def test_dropping_is_name_normalized(self):
        path = make_checkout(self.tmp.name, "ruamel_yaml")
        lockfile = os.path.join(self.tmp.name, "requirements.txt")
        with open(lockfile, "w") as fd:
            fd.write("Ruamel.Yaml==0.18.14\nsix==1.16.0\n")

        venv = FakeVenv(self.root, [_localdeps.LocalDep("ruamel-yaml", path, True)])
        bench = mock.Mock()
        bench.requirements_lockfile = lockfile

        venv.ensure_reqs(bench)

        flat = [str(r) for _, reqs in self.calls for r in reqs]
        self.assertNotIn("Ruamel.Yaml==0.18.14", flat)
        self.assertIn("six==1.16.0", flat)

    def test_plain_requirement_lists_are_filtered_too(self):
        path = self.checkout("psutil")
        venv = FakeVenv(self.root, [_localdeps.LocalDep("psutil", path, True)])
        venv.ensure_reqs(["psutil"])
        flat = [str(r) for _, reqs in self.calls for r in reqs]
        self.assertEqual(flat, [])


if __name__ == "__main__":
    unittest.main()
