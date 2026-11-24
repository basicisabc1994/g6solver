"""Review 2 of claude/template-methods-fu (970da2d3), finding 4.

Class: one fact (is this node G6's generated check?) decided by a name the caller chooses.

Repair 1 gives a generated check's node its own reader: the first message line, admitted only by the compiled
producer grammar, else ``G6_E_CHECK_SETUP: the generated check recorded no compiled failure line``; and G6's pytest
plugin keeps the head, not the tail, of such a node's failure message. Both decide "generated" from the file's name
alone (``verification_search._generated_template``, ``repo_tests._Counted``): any file called ``test_g6_example.py``,
``test_g6_relation.py``, ``test_g6_access_rule.py``, ``test_g6_differential.py`` or ``test_g6_state_invariant.py``.
A caller names the file of a free-form proposed check (``ProposedCheck.file``: any ``test_<name>.py``; nothing
reserves these five), and a repository may hold one. Such a check's honest failure is no longer read as a failure:
the reading is an error that says a generated check recorded no compiled line, so a counterexample the caller's own
check found is reported as a fault in G6's check. At 9203d9c6 the same run reads ``failed`` with the assertion.

The run is a real pytest process with G6's own ``_Counted`` plugin.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmfu2_support as support  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

SUBJECT = {"subject.py": "def run(value):\n    return value + 1\n"}
#: The top of the copy is the working folder (as G6 runs pytest); a check in a folder below it names it itself here.
CHECK = ("import os, sys\nsys.path.insert(0, os.getcwd())\nfrom subject import run\n\n"
         "def test_keeps_its_value():\n    assert run(1) == 1\n")


def _run(request, rel):
    disk = support.disk_for(request.node.nodeid)
    run, record, ran = support.free_form_pytest(disk / "tree", rel, CHECK, SUBJECT)
    return run, record, rel + "::test_keeps_its_value"


def _reader_at(commit):
    """``verification_search`` as ``commit`` holds it, loaded beside the tree's own (read with git show)."""
    root = Path(__file__).resolve().parents[2]
    text = subprocess.run(["git", "-C", str(root), "show", commit + ":src/g6/harness/verification_search.py"],
                          capture_output=True, timeout=60, check=True).stdout
    path = Path(__file__).parent / "disk" / ("verification_search_" + commit + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    spec = importlib.util.spec_from_file_location("tmfu2_verification_search_" + commit, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("rel", ["test_g6_example.py", "tests/test_g6_relation.py"])
def test_a_callers_own_check_is_read_as_a_failure_whatever_its_file_is_called(request, rel):
    run, record, node = _run(request, rel)
    read = vs.reading(run, node)
    verdict = vs.classify("preserve", read, {"result": "passed", "detail": "1 test(s) passed"})
    assert read["result"] == "failed" and "assert" in read["detail"], (
        f"the caller's own check {rel} failed its assertion ({record['messages'][node]!r}); G6 reads its node by "
        f"the generated checks' grammar because of the file's name: {read['result']!r} ({read['detail']!r}), and "
        f"the finding is {verdict['result']!r}: {verdict['detail']!r}")


# --- controls ----------------------------------------------------------------------------------------------------------

def test_control_the_same_check_under_another_name_is_a_failure(request):
    run, record, node = _run(request, "test_g6_proposed_0.py")
    read = vs.reading(run, node)
    assert read["result"] == "failed" and read["detail"] == "assert 2 == 1", read
    assert vs.classify("preserve", read, {"result": "passed", "detail": "x"})["result"] == vs.COUNTEREXAMPLE


def test_control_the_reader_before_the_repair_read_the_same_run_as_a_failure(request):
    run, record, node = _run(request, "test_g6_example.py")
    before = _reader_at("9203d9c6")
    read = before.reading(run, node)
    assert read["result"] == "failed" and read["detail"] == "assert 2 == 1", read
