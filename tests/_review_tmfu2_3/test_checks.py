"""Retained review checks with all fixture writes redirected to this review."""
import importlib
import sys
from types import SimpleNamespace

import pytest

from tests._review_tmfu2_3.test_review import tmp_path, vs

MODULES = [
    "tests.harness.test_template_tmfu4_marks",
    "tests.harness.test_template_tmfu2_amendment1",
    "tests.harness.test_template_tmfu2_r1",
    "tests.harness.test_template_tmfu2_r2",
    "tests.harness.test_template_tmfu2_r2_am3",
    "tests.harness.test_template_tmfu2_r2_am4",
    *[f"tests._review_tmfu1.test_tmfu1_finding_{i}" for i in range(1, 8)],
    *[f"tests._review_tmfu2.test_tmfu2_finding_{i}" for i in (1, 2, 5, 6)],
    *[f"tests._review_tmfu3.test_tmfu3_finding_{i}" for i in (1, 4, 5)],
    *[f"tests._review_tmfu4.test_tmfu4_finding_{i}" for i in (1, 2, 5, 6)],
    "tests._review_tmfu2_1.test_review",
    "tests._review_tmfu2_1.test_checks",
    "tests._review_tmfu2_2.test_review",
    "tests._review_tmfu2_2.test_checks",
]

for module_name in MODULES:
    module = importlib.import_module(module_name)
    for name, function in vars(module).items():
        if name.startswith("test_") and callable(function):
            globals()["test_retained_" + module_name.replace(".", "_") + "__" + name] = function


@pytest.fixture(autouse=True)
def redirect_disks(tmp_path, monkeypatch):
    for name in ("tmfu1_support", "tmfu2_support", "tmfu3_support", "tmfu4_support"):
        if name in sys.modules:
            monkeypatch.setattr(sys.modules[name], "__file__", __file__)


@pytest.mark.parametrize("kind", ["free", "example", "differential", "replay"])
@pytest.mark.parametrize("stage", ["patch", "base", "repeat"])
def test_read_error_remains_local_and_later_check_runs(monkeypatch, kind, stage):
    from tests.harness.test_proposed_checks import result

    checks = [vs.ProposedCheck.model_validate({
        "id": name, "requirement_id": "R1", "intent": "preserve", "file": f"test_{name}.py",
        "source": f"def test_{name}():\n    pass\n"}) for name in ("broken", "later")]
    placed = {one.id: f"g6_check_x/{one.file}" for one in checks}
    origin = {"free": vs.FREE_FORM, "example": vs.Origin(template="example.v1"),
              "differential": vs.Origin(template="differential.v1", refuted_by_difference=True),
              "replay": vs.Origin(keep_observations=True)}[kind]
    calls = []
    original_reading = vs.reading

    def run(change, path, source, node, *, at_base, **kwargs):
        pair = {"input": [1], "output": int(not at_base)}
        return result([node], output="G6-OBSERVATION " + __import__("json").dumps(pair) + "\n")

    def reading(ran, node, **kwargs):
        calls.append(node)
        if node == placed["broken"] and calls.count(node) == {"patch": 1, "base": 2, "repeat": 3}[stage]:
            raise ValueError("cannot read this check")
        return original_reading(ran, node, **kwargs)

    monkeypatch.setattr(vs, "_run_check", run)
    monkeypatch.setattr(vs, "reading", reading)
    done = vs.search(SimpleNamespace(base_commit="a" * 40, sources=()), checks, placed,
                     deadline=vs._clock() + 60, artifact="b" * 64, origins={"broken": origin})
    broken, later = done.activities
    assert broken["result"] == "error", broken
    assert broken["stopped"] == "the check broken"
    assert broken["reason"] == "ValueError: cannot read this check"
    assert later["result"] == vs.BEHAVIOUR_CHANGED, later
