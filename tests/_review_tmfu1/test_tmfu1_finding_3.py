"""Review 1 of claude/template-methods-fu (9203d9c6), finding 3.

Class: one fact decided by two rules (the parent's review 4 I1, at the line the repair draws).

``own_import_absent`` takes a missing module for the tree's own when the tree holds anything under its top-level
name: a file, a package, or a folder with one Python file. A namespace package is shared: the tree holds one portion
and installed distributions hold the others (``pkgutil.extend_path`` in the portion's ``__init__.py``, or PEP 420
folders). G6's ``-I -S`` runner has no installed distribution, so importing another portion raises
ModuleNotFoundError there and nowhere else, and G6 reads that as the code's failed call: a counterexample on the
patch, and at the base commit a confirmed check's control.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu1_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

RUNS = support.RUNS
SUBJECT = "import nsco.installed_part\n" + RUNS
BASE_PASSED = {"result": "passed", "detail": "G6 observed the calls in its isolated runner",
               "observations": [{"input": {"args": [1], "kwargs": {}}, "output": {"returned": 1}}]}
#: The tree's portion of the namespace package ``nsco``; another distribution installs ``nsco.installed_part``.
PORTIONS = {
    "pkgutil-style": {"nsco/__init__.py": "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n",
                      "nsco/mine.py": "VALUE = 1\n"},
    "pep-420": {"nsco/mine.py": "VALUE = 1\n"},
}


def _installed(disk):
    """A folder standing for the person's site-packages: the portion another distribution installs."""
    site = disk / "site"
    (site / "nsco").mkdir(parents=True)
    (site / "nsco" / "installed_part.py").write_text("VALUE = 2\n", encoding="utf-8")
    return site


def _person_runs(tree, site):
    """What the person's interpreter gives for ``subject.run(1)`` with the tree and their installed packages."""
    ran = subprocess.run([sys.executable, "-B", "-c", "import subject; print(subject.run(1))"], cwd=tree,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join([str(tree), str(site)])},
                         capture_output=True, timeout=60)
    return ran.returncode, ran.stdout.decode().strip(), ran.stderr.decode("utf-8", "replace")[-300:]


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("portion", sorted(PORTIONS))
def test_another_portion_of_a_namespace_package_is_not_the_trees_own_missing_module(request, template, portion):
    disk = support.disk_for(request.node.nodeid)
    site = _installed(disk)
    on_patch, found, ran = support.runner_reading(disk / "tree", template, RUNS, SUBJECT, PORTIONS[portion])
    assert _person_runs(disk / "tree", site)[:2] == (0, "1")   # the code imports and runs where it is installed
    verdict = vs.classify("preserve", on_patch, BASE_PASSED)
    killed = vs._to_a_result(on_patch) and on_patch["result"] == "failed"   # the same run, at the base commit
    assert on_patch["result"] == "error" and verdict["result"] != vs.COUNTEREXAMPLE and not killed, (
        "the tree holds one portion of the namespace package nsco and imports a portion an installed distribution "
        "holds; G6's own interpreter (-I -S, no installed distribution) cannot import it, which is no observation of "
        f"the function's behaviour. G6 reports: {verdict} from {found} (stderr {ran.stderr[-200:]!r})")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_a_missing_module_of_the_trees_own_regular_package_is_a_failed_call(request):
    disk = support.disk_for(request.node.nodeid)
    read, found, ran = support.runner_reading(disk / "tree", "example.v1", RUNS, "import held.missing\n" + RUNS,
                                              {"held/__init__.py": ""})
    assert read["result"] == "failed" and read["detail"] == "call failed: builtins.ModuleNotFoundError", (
        found, ran.stderr)


def test_control_a_portion_of_folders_only_is_unavailable(request):
    disk = support.disk_for(request.node.nodeid)
    read, found, ran = support.runner_reading(disk / "tree", "example.v1", RUNS, SUBJECT,
                                              {"nsco/deeper/mine.py": "VALUE = 1\n"})
    assert read["result"] == "error" and read["detail"].startswith("G6_E_OBSERVATION_UNAVAILABLE:"), (
        found, ran.stderr)


def test_control_the_fixture_without_the_installed_portion_fails_for_the_person_too(request):
    disk = support.disk_for(request.node.nodeid)
    support.runner_reading(disk / "tree", "example.v1", RUNS, SUBJECT, PORTIONS["pkgutil-style"])
    empty = disk / "empty"
    empty.mkdir()
    code, out, err = _person_runs(disk / "tree", empty)
    assert code != 0 and "ModuleNotFoundError" in err, (code, out, err)
