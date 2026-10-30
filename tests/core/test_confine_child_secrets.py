# SPDX-License-Identifier: Apache-2.0
"""The final spawn boundary must sanitize even a caller's unfiltered environment."""
import json
import os
import subprocess
import sys

import pytest

from g6.core import confine


class LocalProcess:
    """Exercise spawn policy with a real child, without claiming OS confinement."""
    name = "test-policy-only"

    def start(self, argv, identity, *, network, **options):
        return subprocess.Popen(argv, **options)


@pytest.mark.parametrize("explicit", [False, True])
def test_confined_spawn_does_not_inherit_credentials(monkeypatch, explicit):
    monkeypatch.setenv("G6_TEST_SECRET", "planted-not-a-real-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "planted-not-a-real-key")
    monkeypatch.setenv("PYTHONPATH", "untrusted-import-path")
    monkeypatch.setattr(confine, "_select", lambda: (LocalProcess(), ""))
    plan = confine.Plan("test-policy-only", "", confine.Confinement(()), object())
    options = {"env": dict(os.environ)} if explicit else {}
    child = confine.popen(plan, [sys.executable, "-I", "-c",
                                "import os,json; print(json.dumps({k: k in os.environ for k in "
                                "['G6_TEST_SECRET','OPENAI_API_KEY','PYTHONPATH']}))"],
                         stdout=subprocess.PIPE, **options)
    values = json.loads(child.communicate(timeout=10)[0])
    assert child.returncode == 0
    assert values == {"G6_TEST_SECRET": False, "OPENAI_API_KEY": False, "PYTHONPATH": False}


@pytest.mark.parametrize("options", [{"pass_fds": (99,)}, {"close_fds": False}])
def test_confined_spawn_refuses_extra_inherited_descriptors(monkeypatch, options):
    monkeypatch.setattr(confine, "_select", lambda: (LocalProcess(), ""))
    plan = confine.Plan("test-policy-only", "", confine.Confinement(()), object())
    with pytest.raises(confine.NotStarted, match="descriptor"):
        confine.popen(plan, [sys.executable, "-c", "pass"], **options)


def test_explicit_runtime_paths_are_kept_only_inside_declared_mounts(tmp_path):
    work, dependency, outside = tmp_path / "run", tmp_path / "deps", tmp_path / "host"
    for folder in (work, dependency, outside):
        folder.mkdir()
    boundary = confine.Confinement(read_write=(str(work),), read_only=(str(dependency),))
    got = confine.child_environment({"HOME": str(work), "USERPROFILE": str(work), "ELAN_HOME": str(dependency),
                                    "DOT_SAGE": str(work / "sage"), "SAGE_ROOT": str(dependency),
                                    "IPYTHONDIR": str(outside), "OPENAI_API_KEY": "synthetic", "PYTHONPATH": str(dependency)},
                                   confinement=boundary)
    assert got == {"HOME": str(work), "USERPROFILE": str(work), "ELAN_HOME": str(dependency),
                   "DOT_SAGE": str(work / "sage"), "SAGE_ROOT": str(dependency)}
