import json
import os
import os.path
import tempfile
import unittest

from pyperformance import _localdeps


def make_checkout(root, name="thing", config="pyproject.toml"):
    path = os.path.join(root, name)
    os.makedirs(os.path.join(path, name), exist_ok=True)
    with open(os.path.join(path, config), "w") as fd:
        fd.write("[project]\nname = %r\n" % name)
    with open(os.path.join(path, name, "__init__.py"), "w") as fd:
        fd.write("VERSION = 1\n")
    return path


class NormalizeNameTests(unittest.TestCase):
    def test_already_normal(self):
        self.assertEqual(_localdeps.normalize_name("pyperf"), "pyperf")

    def test_case_and_separators(self):
        for raw in ("Ruamel.Yaml", "ruamel_yaml", "ruamel-yaml", "ruamel__yaml"):
            with self.subTest(raw=raw):
                self.assertEqual(_localdeps.normalize_name(raw), "ruamel-yaml")

    def test_surrounding_whitespace(self):
        self.assertEqual(_localdeps.normalize_name("  pyperf \n"), "pyperf")


class ParseTests(unittest.TestCase):
    def test_plain(self):
        dep = _localdeps.LocalDep.parse("pyperf=/tmp/pyperf")
        self.assertEqual(dep.name, "pyperf")
        self.assertEqual(dep.path, "/tmp/pyperf")
        self.assertFalse(dep.editable)

    def test_editable_suffix(self):
        dep = _localdeps.LocalDep.parse("pyperf=/tmp/pyperf:editable")
        self.assertEqual(dep.path, "/tmp/pyperf")
        self.assertTrue(dep.editable)

    def test_copy_suffix(self):
        dep = _localdeps.LocalDep.parse("pyperf=/tmp/pyperf:copy")
        self.assertEqual(dep.path, "/tmp/pyperf")
        self.assertFalse(dep.editable)

    def test_path_is_made_absolute_and_expanded(self):
        dep = _localdeps.LocalDep.parse("pyperf=~/pyperf")
        self.assertEqual(dep.path, os.path.join(os.path.expanduser("~"), "pyperf"))
        self.assertTrue(os.path.isabs(dep.path))

    def test_colon_in_path_survives(self):
        # A Windows drive letter, or just an oddly named directory: the suffix
        # is matched off the end, not split on.
        dep = _localdeps.LocalDep.parse(r"pyperf=C:\src\pyperf")
        self.assertTrue(dep.path.endswith(r"C:\src\pyperf"))
        self.assertFalse(dep.editable)

    def test_name_is_normalized_for_comparison(self):
        dep = _localdeps.LocalDep.parse("Ruamel.Yaml=/tmp/x")
        self.assertEqual(dep.name, "Ruamel.Yaml")
        self.assertEqual(dep.normalized_name, "ruamel-yaml")

    def test_malformed(self):
        for raw in ("", "pyperf", "=/tmp/pyperf", "pyperf=", "pyperf=:editable"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    _localdeps.LocalDep.parse(raw)


class ValidateTests(unittest.TestCase):
    def test_accepts_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            _localdeps.LocalDep("thing", path).validate()

    def test_accepts_setup_py_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp, config="setup.py")
            _localdeps.LocalDep("thing", path).validate()

    def test_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            dep = _localdeps.LocalDep("thing", os.path.join(tmp, "nope"))
            with self.assertRaises(ValueError):
                dep.validate()

    def test_rejects_directory_with_no_build_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "bare"))
            with self.assertRaises(ValueError):
                _localdeps.LocalDep("bare", os.path.join(tmp, "bare")).validate()


class ParseArgsTests(unittest.TestCase):
    def test_several(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = make_checkout(tmp, "aaa")
            b = make_checkout(tmp, "bbb")
            deps = _localdeps.parse_args([f"aaa={a}:editable", f"bbb={b}"])
            self.assertEqual([d.name for d in deps], ["aaa", "bbb"])
            self.assertEqual([d.editable for d in deps], [True, False])

    def test_empty(self):
        self.assertEqual(_localdeps.parse_args(None), ())
        self.assertEqual(_localdeps.parse_args([]), ())

    def test_duplicate_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = make_checkout(tmp, "aaa")
            b = make_checkout(tmp, "bbb")
            with self.assertRaises(ValueError):
                # Same distribution, spelled two ways.
                _localdeps.parse_args([f"a_a-a={a}", f"A.A.A={b}"])


class FingerprintTests(unittest.TestCase):
    def test_stable_when_nothing_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            dep = _localdeps.LocalDep("thing", path)
            self.assertEqual(dep.fingerprint(), dep.fingerprint())

    def test_copy_install_notices_a_source_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            dep = _localdeps.LocalDep("thing", path, editable=False)
            before = dep.fingerprint()
            src = os.path.join(path, "thing", "__init__.py")
            os.utime(src, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
            self.assertNotEqual(dep.fingerprint(), before)

    def test_editable_install_ignores_a_source_edit(self):
        # The .pth makes the edit live already, so reinstalling would be pure
        # cost. This is the behaviour that keeps an edit-run loop cheap.
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            dep = _localdeps.LocalDep("thing", path, editable=True)
            before = dep.fingerprint()
            src = os.path.join(path, "thing", "__init__.py")
            os.utime(src, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
            self.assertEqual(dep.fingerprint(), before)

    def test_editable_install_notices_a_build_config_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            dep = _localdeps.LocalDep("thing", path, editable=True)
            before = dep.fingerprint()
            cfg = os.path.join(path, "pyproject.toml")
            os.utime(cfg, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
            self.assertNotEqual(dep.fingerprint(), before)

    def test_editable_and_copy_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            self.assertNotEqual(
                _localdeps.LocalDep("thing", path, editable=True).fingerprint(),
                _localdeps.LocalDep("thing", path, editable=False).fingerprint(),
            )

    def test_path_change_shows_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = make_checkout(tmp, "aaa")
            b = make_checkout(tmp, "bbb")
            self.assertNotEqual(
                _localdeps.LocalDep("thing", a, editable=True).fingerprint(),
                _localdeps.LocalDep("thing", b, editable=True).fingerprint(),
            )

    def test_ignored_directories_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            dep = _localdeps.LocalDep("thing", path, editable=False)
            before = dep.fingerprint()
            git = os.path.join(path, ".git")
            os.makedirs(git)
            with open(os.path.join(git, "HEAD"), "w") as fd:
                fd.write("ref: refs/heads/main\n")
            self.assertEqual(dep.fingerprint(), before)


class MarkerTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_checkout(tmp)
            dep = _localdeps.LocalDep("Thing", path, editable=True)
            self.assertEqual(_localdeps.read_marker(tmp), {})
            _localdeps.update_marker(tmp, [dep])
            self.assertEqual(_localdeps.read_marker(tmp), {"thing": dep.fingerprint()})

    def test_update_keeps_other_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = _localdeps.LocalDep("aaa", make_checkout(tmp, "aaa"))
            b = _localdeps.LocalDep("bbb", make_checkout(tmp, "bbb"))
            _localdeps.update_marker(tmp, [a])
            _localdeps.update_marker(tmp, [b])
            self.assertEqual(
                _localdeps.read_marker(tmp),
                {"aaa": a.fingerprint(), "bbb": b.fingerprint()},
            )

    def test_marker_is_valid_json_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            dep = _localdeps.LocalDep("thing", make_checkout(tmp))
            _localdeps.update_marker(tmp, [dep])
            filename = os.path.join(tmp, _localdeps.MARKER_NAME)
            with open(filename) as fd:
                data = json.load(fd)
            self.assertEqual(data["thing"]["path"], dep.path)
            self.assertIs(data["thing"]["editable"], False)

    def test_corrupt_marker_reads_as_empty(self):
        # Costs a reinstall, which is the safe direction to fail in.
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, _localdeps.MARKER_NAME), "w") as fd:
                fd.write("{not json")
            self.assertEqual(_localdeps.read_marker(tmp), {})

    def test_missing_marker_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_localdeps.read_marker(tmp), {})


if __name__ == "__main__":
    unittest.main()
