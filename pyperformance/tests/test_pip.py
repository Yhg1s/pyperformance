import unittest

from pyperformance._pip import get_pkg_name


class GetPkgNameTests(unittest.TestCase):
    def test_bare_name(self):
        self.assertEqual(get_pkg_name("pyperf"), "pyperf")

    def test_pinned_version(self):
        self.assertEqual(get_pkg_name("pyperf==2.10.0"), "pyperf")

    def test_minimum_version(self):
        self.assertEqual(get_pkg_name("packaging>=24.1"), "packaging")

    def test_env_marker(self):
        self.assertEqual(get_pkg_name("tomli; python_version<'3.11'"), "tomli")

    def test_direct_reference_git(self):
        # The URL ends in a git ref introduced by a second "@", so only the
        # first one may be honoured.
        self.assertEqual(
            get_pkg_name("pyperf@git+https://github.com/psf/pyperf@some-branch"),
            "pyperf",
        )

    def test_direct_reference_git_egg_fragment(self):
        self.assertEqual(
            get_pkg_name(
                "pyperf@git+https://github.com/psf/pyperf@abc123#egg=pyperf"
            ),
            "pyperf",
        )

    def test_direct_reference_local_path(self):
        self.assertEqual(
            get_pkg_name("pyperf @ file:///home/someone/python/pyperf"),
            "pyperf",
        )

    def test_direct_reference_with_env_marker(self):
        self.assertEqual(
            get_pkg_name("pyperf@file:///tmp/pyperf; python_version>='3.10'"),
            "pyperf",
        )


if __name__ == "__main__":
    unittest.main()
