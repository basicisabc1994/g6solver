"""Review 2 of claude/template-methods-fu (970da2d3), finding 1.

Class: one fact decided by two rules (review 1's findings 2 to 4, for the moment the repair does not reach).

Repair 1 decides whether a missing import is the tree's own from its name, its path and the tree's regular packages
(``own_import_absent``), and gives an import G6's isolated interpreter cannot satisfy
``G6_E_OBSERVATION_UNAVAILABLE`` "across all phases". The phases are the preparation phases only. The same import
written inside the function (the ordinary way to import lazily) runs during the call, where the bootstrap takes
whatever is raised as the code's own outcome: G6's interpreter is started with ``-I -S`` and never has the
repository's installed packages (``behaviour_runner.run``: "never a repository interpreter or repository site
packages"), so every such call "raises ModuleNotFoundError" there. A confirmed example then fails, a patch that
moves an import into its function is a counterexample, and a differential probe reports a changed behaviour, for
code that returns the same value in the person's interpreter. The same statement at the top of the module is
``G6_E_OBSERVATION_UNAVAILABLE`` (control).

No confined run is needed: G6's fixed bootstrap is started as ``behaviour_runner.run`` starts it (``-I -S -B``).
``pydantic`` stands for an installed package: this interpreter has it, G6's isolated child does not.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu2_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

RUNS = support.RUNS
AT_CALL = "def run(value):\n    import pydantic\n    return value\n"
AT_CALL_FROM = "def run(value):\n    from pydantic import BaseModel\n    return value\n"
AT_TOP = "import pydantic\n" + RUNS
UNAVAILABLE = "G6_E_OBSERVATION_UNAVAILABLE"


def _base_and_patch(request, template, patch_source, extra=None):
    disk = support.disk_for(request.node.nodeid)
    at_base, _, _ = support.runner_reading(disk / "base", template, RUNS, RUNS)
    on_patch, found, ran = support.runner_reading(disk / "patch", template, RUNS, patch_source, extra)
    return at_base, on_patch, found, ran


@pytest.mark.parametrize("source", [AT_CALL, AT_CALL_FROM], ids=["import", "from-import"])
def test_an_import_the_runner_cannot_satisfy_inside_the_function_is_not_a_counterexample(request, source):
    import pydantic  # noqa: F401  the person's interpreter has the package: the patched function returns its value
    at_base, on_patch, found, ran = _base_and_patch(request, "example.v1", source)
    assert at_base["result"] == "passed", at_base
    verdict = vs.classify("preserve", on_patch, at_base)
    assert on_patch["result"] == "error" and UNAVAILABLE in on_patch["detail"], (
        "the patch imports an installed package inside the function; G6's isolated interpreter has no installed "
        "package, so the call raised ModuleNotFoundError there and nowhere else. G6 reads that as the code's own "
        f"outcome: the reading is {on_patch['result']!r} ({on_patch['detail']!r}) and the finding is "
        f"{verdict['result']!r}: {verdict['detail']!r}. The same import at the top of the module is {UNAVAILABLE}.")


def test_an_import_the_runner_cannot_satisfy_inside_the_function_is_not_a_changed_behaviour(request):
    at_base, on_patch, found, ran = _base_and_patch(request, "differential.v1", AT_CALL)
    assert at_base["result"] == "passed", at_base
    verdict = vs.classify("preserve", on_patch, at_base)
    assert verdict["result"] not in (vs.BEHAVIOUR_CHANGED, vs.COUNTEREXAMPLE), (
        "the probe observed, in G6's isolated interpreter, a ModuleNotFoundError for an installed package the "
        f"function imports when called, and reports it as the patch's behaviour: {verdict}")


# --- controls ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
def test_control_the_same_import_at_the_top_of_the_module_is_unavailable(request, template):
    at_base, on_patch, found, ran = _base_and_patch(request, template, AT_TOP)
    assert on_patch["result"] == "error" and on_patch["detail"] == (
        UNAVAILABLE + ": import or lazy lookup did not complete: builtins.ModuleNotFoundError"), (found, ran.stderr)
    assert vs.classify("preserve", on_patch, at_base)["result"] == vs.CHECK_ERROR


def test_control_a_call_that_raises_its_own_error_is_a_counterexample(request):
    at_base, on_patch, found, ran = _base_and_patch(
        request, "example.v1", "def run(value):\n    raise ValueError('no')\n")
    assert on_patch["result"] == "failed", (found, ran.stderr)
    assert vs.classify("preserve", on_patch, at_base)["result"] == vs.COUNTEREXAMPLE


def test_control_the_trees_own_missing_module_inside_the_function_is_a_counterexample(request):
    at_base, on_patch, found, ran = _base_and_patch(
        request, "example.v1", "def run(value):\n    import held.missing\n    return value\n",
        {"held/__init__.py": ""})
    assert on_patch["result"] == "failed", (found, ran.stderr)
    assert vs.classify("preserve", on_patch, at_base)["result"] == vs.COUNTEREXAMPLE


def test_control_the_unchanged_function_passes(request):
    at_base, on_patch, found, ran = _base_and_patch(request, "example.v1", RUNS)
    assert on_patch["result"] == "passed" and vs.classify("preserve", on_patch, at_base)["result"] == vs.NOT_REFUTED
