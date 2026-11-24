"""Review 1 of claude/template-methods-fu (9203d9c6), finding 4.

Class: one fact decided by two rules (the other side of review 4's I1: a real defect read as unavailable).

The tree's own lookup that fails is the code's failed call when the import is spelt ``import held.missing`` or
``from held.missing import name`` (ModuleNotFoundError naming a module under a package the tree holds). The same
missing module of the same held package, spelt ``from held import missing``, and a name the tree's own module no
longer defines (``from helpers import gone``), raise ImportError, which ``setup_call`` reads as
``G6_E_OBSERVATION_UNAVAILABLE`` whatever it names: a patch that deletes a module or a name its own tree still
imports is reported as an observation G6 could not make, where the parent (86204ecf) reported the failed call.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu1_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

RUNS = support.RUNS
BASE_PASSED = {"result": "passed", "detail": "G6 observed the calls in its isolated runner",
               "observations": [{"input": {"args": [1], "kwargs": {}}, "output": {"returned": 1}}]}
#: The patch removed ``held/missing.py`` or ``helpers.gone``; the tree's own module still imports it.
OWN = {
    "from-package-import-module": ("from held import missing\n" + RUNS, {"held/__init__.py": ""}),
    "from-module-import-name": ("from helpers import gone\n" + RUNS, {"helpers.py": "kept = 1\n"}),
}
SAME_FACT = {
    "import-package-module": ("import held.missing\n" + RUNS, {"held/__init__.py": ""}),
    "from-package-module-import-name": ("from held.missing import name\n" + RUNS, {"held/__init__.py": ""}),
}


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("spelling", sorted(OWN))
def test_the_trees_own_missing_import_is_the_codes_failed_call_however_it_is_spelt(request, template, spelling):
    disk = support.disk_for(request.node.nodeid)
    source, extra = OWN[spelling]
    on_patch, found, ran = support.runner_reading(disk / "tree", template, RUNS, source, extra)
    verdict = vs.classify("preserve", on_patch, BASE_PASSED)
    assert on_patch["result"] == "failed" and verdict["result"] == vs.COUNTEREXAMPLE, (
        "the module imports a module or a name of the tree's own that the tree does not hold: the tree's own lookup "
        "failed, in any interpreter (no installed package is involved), and `import held.missing` in its place is "
        f"the code's failed call (the controls below). G6 reports: {verdict} from {found} "
        f"(stderr {ran.stderr[-200:]!r})")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("spelling", sorted(SAME_FACT))
def test_control_the_same_missing_module_spelt_as_a_module_path_is_a_failed_call(request, template, spelling):
    disk = support.disk_for(request.node.nodeid)
    source, extra = SAME_FACT[spelling]
    read, found, ran = support.runner_reading(disk / "tree", template, RUNS, source, extra)
    assert read["result"] == "failed" and read["detail"] == "call failed: builtins.ModuleNotFoundError", (
        found, ran.stderr)
    assert vs.classify("preserve", read, BASE_PASSED)["result"] == vs.COUNTEREXAMPLE


@pytest.mark.parametrize("spelling", sorted(OWN))
def test_control_the_changes_base_commit_read_it_as_the_failed_call(request, spelling):
    """The same tree under the runner of 86204ecf (its ``behaviour_runner``, read with git show): a regression."""
    disk = support.disk_for(request.node.nodeid)
    source, extra = OWN[spelling]
    read, found, ran = support.runner_reading(disk / "tree", "example.v1", RUNS, source, extra,
                                              module=support.parent_runner())
    assert read["result"] == "failed" and read["detail"].startswith("call failed: "), (found, ran.stderr)


def test_control_an_installed_package_the_runner_lacks_is_unavailable(request):
    disk = support.disk_for(request.node.nodeid)
    read, found, ran = support.runner_reading(disk / "tree", "example.v1", RUNS, "from pydantic import BaseModel\n" + RUNS)
    assert read["result"] == "error" and read["detail"].startswith("G6_E_OBSERVATION_UNAVAILABLE:"), (
        found, ran.stderr)


def test_control_the_trees_with_the_module_and_the_name_pass(request):
    disk = support.disk_for(request.node.nodeid)
    for name, (source, extra) in OWN.items():
        whole = {**extra, "held/missing.py": "", "helpers.py": "kept = 1\ngone = 2\n"}
        read, found, ran = support.runner_reading(disk / name, "example.v1", RUNS, source, whole)
        assert read["result"] == "passed", (name, found, ran.stderr)
