import os
import os.path
import sys

import pyperformance

from . import _localdeps, _pip, _utils, _venv

REQUIREMENTS_FILE = os.path.join(
    os.path.dirname(__file__), "requirements", "requirements.txt"
)
PYPERF_OPTIONAL = ["psutil"]


class Requirements(object):
    @classmethod
    def from_file(cls, filename):
        self = cls()
        self._add_from_file(filename)
        return self

    @classmethod
    def from_benchmarks(cls, benchmarks):
        self = cls()
        for bench in benchmarks or ():
            filename = bench.requirements_lockfile
            self._add_from_file(filename)
        return self

    def __init__(self):
        # if pip or setuptools is updated:
        # .github/workflows/main.yml should be updated as well

        # requirements
        self.specs = []

    def __len__(self):
        return len(self.specs)

    def __iter__(self):
        for spec in self.specs:
            yield spec

    def _add_from_file(self, filename):
        if not os.path.exists(filename):
            return
        for line in _utils.iter_clean_lines(filename):
            fullpath = os.path.join(os.path.dirname(filename), line.strip())
            if os.path.isfile(fullpath):
                self._add(fullpath)
            else:
                self._add(line)

    def _add(self, line):
        self.specs.append(line)

    def get(self, name):
        for req in self.specs:
            if _pip.get_pkg_name(req) == name:
                return req
        return None


# This is used by the hg_startup benchmark.
def get_venv_program(program):
    bin_path = os.path.dirname(sys.executable)
    bin_path = os.path.realpath(bin_path)

    if not os.path.isabs(bin_path):
        print("ERROR: Python executable path is not absolute: %s" % sys.executable)
        sys.exit(1)

    if not os.path.exists(os.path.join(bin_path, "activate")):
        print(
            "ERROR: Unable to get the virtual environment of "
            "the Python executable %s" % sys.executable
        )
        sys.exit(1)

    if os.name == "nt":
        path = os.path.join(bin_path, program)
    else:
        path = os.path.join(bin_path, program)

    if not os.path.exists(path):
        print(
            "ERROR: Unable to get the program %r "
            "from the virtual environment %r" % (program, bin_path)
        )
        sys.exit(1)

    return path


NECESSARY_ENV_VARS = {
    "nt": [
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMPUTERNAME",
        "ComSpec",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        "CommonProgramW6432",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "Path",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "SystemDrive",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERDNSDOMAIN",
        "USERDOMAIN",
        "USERDOMAIN_ROAMINGPROFILE",
        "USERNAME",
        "USERPROFILE",
        "windir",
    ],
}
NECESSARY_ENV_VARS_DEFAULT = [
    "HOME",
    "PATH",
]


def _get_envvars(inherit=None, osname=None):
    # Restrict the env we use.
    try:
        necessary = NECESSARY_ENV_VARS[osname or os.name]
    except KeyError:
        necessary = NECESSARY_ENV_VARS_DEFAULT
    copy_env = list(necessary)
    if inherit:
        copy_env.extend(inherit)

    env = {}
    for name in copy_env:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


class VenvForBenchmarks(_venv.VirtualEnvironment):
    @classmethod
    def create(
        cls,
        root=None,
        python=None,
        *,
        inherit_environ=None,
        upgrade=False,
        local_deps=None,
    ):
        env = _get_envvars(inherit_environ)
        self = super().create(root, python, env=env, withpip=False)
        self.inherit_environ = inherit_environ
        self.local_deps = local_deps

        try:
            self.ensure_pip(upgrade=upgrade)
            self.sync_local_deps()
        except BaseException:
            _utils.safe_rmtree(self.root)
            raise

        # Display the pip version
        _pip.run_pip("--version", python=self.python, env=self._env)

        return self

    @classmethod
    def ensure(
        cls,
        root,
        python=None,
        *,
        inherit_environ=None,
        upgrade=False,
        skip_pip=False,
        local_deps=None,
        **kwargs,
    ):
        exists = _venv.venv_exists(root)
        if upgrade == "oncreate":
            upgrade = not exists
        elif upgrade == "onexists":
            upgrade = exists
        elif isinstance(upgrade, str):
            raise NotImplementedError(upgrade)

        if exists:
            self = super().ensure(root)
            self.inherit_environ = inherit_environ
            self.local_deps = local_deps
            if skip_pip:
                # Trust that pip is already installed correctly
                pass
            elif upgrade:
                self.upgrade_pip()
            else:
                self.ensure_pip(upgrade=False)
            # Not gated on skip_pip: --trust-venv is about not reinstalling what
            # an index would give back unchanged, and a local checkout is the
            # one thing that can have changed since the venv was built. The
            # marker keeps this free when it has not.
            self.sync_local_deps()
            return self
        else:
            return cls.create(
                root,
                python,
                inherit_environ=inherit_environ,
                upgrade=upgrade,
                local_deps=local_deps,
                **kwargs,
            )

    def __init__(self, root, *, base=None, inherit_environ=None, local_deps=None):
        super().__init__(root, base=base)
        self.inherit_environ = inherit_environ or None
        self.local_deps = local_deps

    @property
    def local_deps(self):
        return self._local_deps

    @local_deps.setter
    def local_deps(self, value):
        self._local_deps = tuple(value or ())
        self.local_dep_names = frozenset(
            dep.normalized_name for dep in self._local_deps
        )

    def sync_local_deps(self):
        """Install any local dependency this venv does not already have.

        Installing is skipped when the venv's marker file already records the
        dependency at the same fingerprint. That is the whole point: pip
        reinstalls a local path requirement unconditionally, and ensure_reqs()
        runs once per benchmark, so without the marker a local pyperf would be
        rebuilt once for every benchmark in the suite.
        """
        if not self.local_deps:
            return ()

        installed = _localdeps.read_marker(self.root)
        stale = [
            dep
            for dep in self.local_deps
            if installed.get(dep.normalized_name) != dep.fingerprint()
        ]
        if not stale:
            print(
                "local dependencies already installed in %s: %s"
                % (self.root, ", ".join(dep.name for dep in self.local_deps))
            )
            return ()

        for dep in stale:
            kind = "editable" if dep.editable else "copy"
            print(
                "installing local dependency %s (%s) from %s into %s"
                % (dep.name, kind, dep.path, self.root)
            )
            if dep.editable:
                ec, _, _ = _pip.install_editable(
                    dep.path,
                    python=self.info,
                    env=self._env,
                )
            else:
                ec, _, _ = _pip.install_requirements(
                    dep.path,
                    python=self.python,
                    env=self._env,
                    upgrade=False,
                )
            if ec != 0:
                raise _venv.RequirementsInstallationFailedError(dep.path)

        _localdeps.update_marker(self.root, stale)
        return tuple(stale)

    def _drop_local_deps(self, requirements):
        """Remove requirements that a local checkout already provides.

        A benchmark's lockfile naming pyperf would otherwise have pip replace
        the local checkout with a released version part-way through a run.
        """
        if not self.local_dep_names:
            return requirements

        def is_local(spec):
            name = _localdeps.normalize_name(_pip.get_pkg_name(spec))
            return name in self.local_dep_names

        dropped = [spec for spec in requirements if is_local(spec)]
        if not dropped:
            return requirements
        print(
            "ignoring requirement(s) provided by a local checkout: %s"
            % ", ".join(dropped)
        )
        kept = [spec for spec in requirements if not is_local(spec)]
        if isinstance(requirements, Requirements):
            requirements.specs = kept
            return requirements
        return kept

    @property
    def _env(self):
        # Restrict the env we use.
        return _get_envvars(self.inherit_environ)

    def install_pyperformance(self):
        print("installing pyperformance in the venv at %s" % self.root)
        # Install pyperformance inside the virtual environment.
        if 1:
            self.ensure_reqs(["pyperformance@git+https://github.com/Yhg1s/pyperformance@local-changes#egg=pyperformance"])
            self._install_pyperf_optional_dependencies()
        elif pyperformance.is_dev():
            basereqs = Requirements.from_file(REQUIREMENTS_FILE)
            has_pyperf = bool(basereqs.get("pyperf"))
            # ensure_reqs() drops a locally-provided pyperf from basereqs, so
            # ask before rather than after: pyperf still wants its optional
            # dependencies whichever copy of it is installed.
            self.ensure_reqs(basereqs)
            if has_pyperf or "pyperf" in self.local_dep_names:
                self._install_pyperf_optional_dependencies()

            root_dir = os.path.dirname(pyperformance.PKG_ROOT)
            ec, _, _ = _pip.install_editable(
                root_dir,
                python=self.info,
                env=self._env,
            )
            if ec != 0:
                raise _venv.RequirementsInstallationFailedError(root_dir)
        else:
            version = pyperformance.__version__
            self.ensure_reqs([f"pyperformance=={version}"])
            self._install_pyperf_optional_dependencies()

    def _install_pyperf_optional_dependencies(self):
        for req in PYPERF_OPTIONAL:
            try:
                self.ensure_reqs([req])
            except _venv.RequirementsInstallationFailedError:
                print("WARNING: failed to install %s" % req)
                pass

    def ensure_reqs(self, requirements=None):
        # parse requirements
        bench = None
        if requirements is None:
            requirements = Requirements()
        elif hasattr(requirements, "requirements_lockfile"):
            bench = requirements
            requirements = Requirements.from_benchmarks([bench])

        # A local checkout of a requirement wins over whatever would otherwise
        # be installed for it, including the injection just below.
        requirements = self._drop_local_deps(requirements)

        # Every benchmark must depend on pyperf.
        if (
            bench is not None
            and "pyperf" not in self.local_dep_names
            and not requirements.get("pyperf")
        ):
            basereqs = Requirements.from_file(REQUIREMENTS_FILE)
            pyperf_req = basereqs.get("pyperf")
            if not pyperf_req:
                raise NotImplementedError
            requirements.specs.append(pyperf_req)
            # XXX what about psutil?

        if not requirements:
            print("(nothing to install)")
        else:
            # install requirements
            super().ensure_reqs(
                *requirements,
                upgrade=False,
            )

            if bench is not None:
                self._install_pyperf_optional_dependencies()

        # Dump the package list and their versions: pip freeze
        _pip.run_pip("freeze", python=self.python, env=self._env)

        return requirements
