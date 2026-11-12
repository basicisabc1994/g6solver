"""Whole-report decisions and output independence, including neighbouring forms."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop, patch_review as review, verification_search
from g6.workforce import repo_tests as rt
from tests.harness.efu2_cases import FAILED, NODE, PASSED, report


@pytest.mark.parametrize("phases,summary", [
    (PASSED, "failed"), (FAILED, "passed"),
    (PASSED, "no_tests_collected"), (FAILED, "no_tests_collected"),
    (PASSED, "could_not_start"), (FAILED, "pytest_not_found"),
    (FAILED, "run_inconsistent"),
    (PASSED, "collection_error"), (FAILED, "collection_error"),
])
def test_contradictory_summary_is_unknown_through_every_phase_consumer(phases, summary):
    raw = report(phases, verdict=summary)
    decided = rt.read_report(raw)
    assert not decided.valid
    assert not rt.read_report(decided.wire()).valid
    assert review._observed([NODE], raw)[NODE] == review.ERROR
    assert review.observation(review._verdicts([NODE], raw)[NODE]) == review.ERROR
    assert verification_search.reading(raw, NODE)["result"] == "error"
    got = completion_loop._read(SimpleNamespace(sources=()), [raw])
    assert got.exit is None


@pytest.mark.parametrize("fields", [
    {"loaded_complete": False}, {"loaded_complete": True},
    {"loaded_outside": {}}, {"loaded_outside": {"calc": "elsewhere/calc.py"}},
    {"loaded_complete": False, "loaded_outside": {}},
    {"loaded_complete": "yes", "loaded_outside": {}},
    {"loaded_complete": True, "loaded_outside": []},
])
def test_missing_wrong_typed_or_incomplete_loading_never_reads_as_clean(fields):
    raw = report(PASSED, **fields)
    assert rt.shadowed(["calc.py"], raw) is None


def test_recorded_loading_and_complete_loading_keep_their_meaning():
    assert rt.shadowed(["calc.py"], report(PASSED)) == {}
    assert rt.shadowed(["calc.py"], report(PASSED, loaded_complete=True, loaded_outside={})) == {}
    raw = report(PASSED, loaded_complete=True, loaded_outside={"calc": "elsewhere/calc.py"})
    assert rt.shadowed(["calc.py"], raw) == {"calc.py": {"module": "calc", "file": "elsewhere/calc.py"}}


@pytest.mark.parametrize("output", [
    "ERROR collecting test_calc.py", "ERROR collecting test_other.py",
    f"not found: {NODE}", "Fatal Python error: Segmentation fault",
    "ModuleNotFoundError: No module named 'invented'", "1 passed", "1 failed",
])
@pytest.mark.parametrize("phases", [PASSED, FAILED])
def test_output_changes_no_decided_check_or_base_collection(phases, output):
    raw = report(phases)
    before = review._verdicts([NODE, "test_other.py"], raw)
    planted = {**raw, "output": output}
    after = review._verdicts([NODE, "test_other.py"], planted)
    assert after == before
    base = deepcopy(after)
    assert review._not_loaded(base, planted) == []
    assert base == after


def test_collection_annotations_are_used_instead_of_printed_collection_errors():
    raw = report({}, verdict="collection_error", test_outcomes={}, collected_ids=[],
                 failure_messages={"test_calc.py": "import failed"})
    assert rt.read_report(raw).valid
    found = review._verdicts([NODE], raw)
    assert review._not_loaded(found, raw) == ["test_calc.py"]
    assert found[NODE]["verdict"] == "not_run"


def test_decided_loading_does_not_share_the_callers_mutable_listing():
    raw = report(PASSED, loaded_complete=True, loaded_outside={"calc": "elsewhere/calc.py"})
    decided = rt.read_report(raw)
    raw["loaded_outside"].clear()
    assert rt.shadowed(["calc.py"], decided)
