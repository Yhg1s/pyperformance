"""Dependencies taken from a local checkout instead of an index.

A benchmark venv normally resolves everything from a requirements file, which
means testing a change to pyperf costs a release, or at least a push to
somewhere the venv can fetch from. A local dependency short-circuits that: the
venv installs the working tree directly.

The reason this needs a module of its own rather than another line in a
requirements file is that pip rebuilds and reinstalls a local path requirement
on every invocation, even when nothing changed. Benchmark requirements are
installed once per benchmark, so a local pyperf listed that way would be rebuilt
a hundred times in one run. Instead each local dependency is installed once per
venv and recorded in a marker file, and later invocations compare against the
marker rather than asking pip.
"""

import json
import os
import os.path


MARKER_NAME = ".pyperformance-local-deps.json"

# Nothing under these contributes to what gets installed, and .git in
# particular changes on every command that touches the repository.
_IGNORED_DIRS = frozenset(
    [
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "venv",
        ".venv",
    ]
)

# What an editable install bakes in, as opposed to what it merely points at.
_BUILD_CONFIG_FILES = ("pyproject.toml", "setup.py", "setup.cfg")


def normalize_name(name):
    """Return `name` in the form PEP 503 compares distribution names in."""
    out = []
    prev_sep = False
    for ch in name.strip().lower():
        if ch in "-_.":
            prev_sep = True
            continue
        if prev_sep and out:
            out.append("-")
        prev_sep = False
        out.append(ch)
    return "".join(out)


class LocalDep:
    """A distribution to install from `path` rather than from an index."""

    def __init__(self, name, path, editable=False):
        self.name = name
        self.normalized_name = normalize_name(name)
        self.path = os.path.abspath(os.path.expanduser(path))
        self.editable = bool(editable)

    def __repr__(self):
        return "%s(name=%r, path=%r, editable=%r)" % (
            type(self).__name__,
            self.name,
            self.path,
            self.editable,
        )

    def __eq__(self, other):
        if not isinstance(other, LocalDep):
            return NotImplemented
        return (
            self.normalized_name == other.normalized_name
            and self.path == other.path
            and self.editable == other.editable
        )

    def __hash__(self):
        return hash((self.normalized_name, self.path, self.editable))

    @classmethod
    def parse(cls, text):
        """Parse one NAME=PATH[:editable] argument.

        The suffix is opt-in, so a bare NAME=PATH is a plain (non-editable)
        install. It is matched off the end rather than by splitting on ":", so
        that a Windows drive letter in the path survives.
        """
        raw = text.strip()
        name, sep, rest = raw.partition("=")
        name = name.strip()
        rest = rest.strip()
        if not sep or not name or not rest:
            raise ValueError(f"expected NAME=PATH[:editable], got {text!r}")

        editable = False
        for suffix, value in ((":editable", True), (":copy", False)):
            if rest.endswith(suffix):
                editable = value
                rest = rest[: -len(suffix)].strip()
                break
        if not rest:
            raise ValueError(f"no path in {text!r}")

        return cls(name, rest, editable)

    def validate(self):
        """Raise ValueError unless the path looks like something installable."""
        if not os.path.isdir(self.path):
            raise ValueError(
                f"local dependency {self.name!r}: {self.path} is not a directory"
            )
        if not any(
            os.path.exists(os.path.join(self.path, f)) for f in _BUILD_CONFIG_FILES
        ):
            raise ValueError(
                f"local dependency {self.name!r}: {self.path} has no "
                f"{' or '.join(_BUILD_CONFIG_FILES)}, so pip has nothing to build"
            )
        return self

    def fingerprint(self):
        """A value that changes when the venv needs this dep reinstalled.

        An editable install is a pointer, so edits to the source are already
        live and only the build configuration can go stale -- fingerprinting the
        sources there would reinstall on every edit for no benefit, which is the
        cost this whole module exists to avoid. A plain install is a copy, so
        every source file counts.
        """
        parts = ["editable" if self.editable else "copy", self.path]
        if self.editable:
            mtime = _config_mtime(self.path)
        else:
            mtime = _tree_mtime(self.path)
        parts.append(str(mtime))
        return "|".join(parts)


def _config_mtime(root):
    latest = 0
    for name in _BUILD_CONFIG_FILES:
        try:
            latest = max(latest, os.stat(os.path.join(root, name)).st_mtime_ns)
        except OSError:
            pass
    return latest


def _tree_mtime(root):
    latest = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _IGNORED_DIRS and not d.endswith(".egg-info")
        ]
        for filename in filenames:
            try:
                st = os.stat(os.path.join(dirpath, filename))
            except OSError:
                continue
            if st.st_mtime_ns > latest:
                latest = st.st_mtime_ns
    return latest


def parse_args(values):
    """Parse a list of NAME=PATH[:editable] arguments into LocalDeps.

    Raises ValueError on a malformed argument, a path that is not installable,
    or the same distribution named twice.
    """
    deps = {}
    for value in values or ():
        dep = LocalDep.parse(value).validate()
        if dep.normalized_name in deps:
            raise ValueError(
                f"local dependency {dep.name!r} given more than once "
                f"({deps[dep.normalized_name].path} and {dep.path})"
            )
        deps[dep.normalized_name] = dep
    return tuple(deps.values())


def read_marker(venv_root):
    """The recorded fingerprints for `venv_root`, {normalized name: str}.

    An unreadable or malformed marker reads as empty, which costs a reinstall
    rather than skipping one.
    """
    filename = os.path.join(venv_root, MARKER_NAME)
    try:
        with open(filename, encoding="utf-8") as fd:
            data = json.load(fd)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(name): str(entry.get("fingerprint"))
        for name, entry in data.items()
        if isinstance(entry, dict)
    }


def update_marker(venv_root, deps):
    """Record `deps` as installed in `venv_root`, keeping other entries."""
    filename = os.path.join(venv_root, MARKER_NAME)
    try:
        with open(filename, encoding="utf-8") as fd:
            data = json.load(fd)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}

    for dep in deps:
        data[dep.normalized_name] = {
            "name": dep.name,
            "path": dep.path,
            "editable": dep.editable,
            "fingerprint": dep.fingerprint(),
        }

    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fd:
        json.dump(data, fd, indent=2, sort_keys=True)
        fd.write("\n")
    os.replace(tmp, filename)
