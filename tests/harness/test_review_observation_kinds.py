# SPDX-License-Identifier: Apache-2.0
"""Report attribution follows typed observations for static and executed checks."""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness.patch_report import render
from g6.harness.patch_review import _record, _reply
from g6.harness.verification_templates import BASE, PATCH, Prepared, TemplateInstance, Tree, structural


def _static():
    files = {"shop/__init__.py": "", "shop/core.py": "import shop.web\n", "shop/web.py": "VALUE = 1\n"}
    texts = {(name, side): "" if name == "shop/core.py" and side == BASE else body
             for name, body in files.items() for side in (BASE, PATCH)}
    change = SimpleNamespace(base_commit="base", files=["shop/core.py"])
    tree = Tree(Path.cwd(), change, frozenset(files), frozenset(files), texts)
    instance = TemplateInstance(
        id="layer", template="forbidden_dependency.v1", requirement_id="R1", intent="preserve",
        bindings={"package": "shop", "contract": {
            "forbidden": [{"source": "shop.core", "forbidden": "shop.web"}]}},
    )
    found = structural(tree, instance)
    assert found["verdict"] == "failed"
    return found


@pytest.mark.parametrize("kind", ["static", "test", "mixed", "empty"])
def test_internal_observations_keep_their_attribution_and_actual_reproduction(kind):
    static = _static() if kind in {"static", "mixed"} else None
    runs = [_record(1, "stated checks and extra tests", ["tests/test_sum.py::test_sum"],
                    {"verdict": "failed", "failed": 1})] if kind in {"test", "mixed"} else []
    defects = ([{"code": "forbidden_dependency_added", "node": static["id"], "detail": static["detail"],
                 "reproduction": static["reproduction"]}] if static else [])
    if runs:
        defects.append({"code": "check_failed", "node": "tests/test_sum.py::test_sum", "detail": "sum failed"})
    prepared = Prepared(structural=[static], activities=[static["activity"]]) if static else None
    reasons = [{"stage": "tier_1", "code": one["code"], "detail": one["detail"]} for one in defects]
    assessment = _reply(
        SimpleNamespace(base_commit="base", files=["shop/core.py"]), [], reasons, [], runs, [], [], {}, {}, [],
        "artifact", "", {}, time.monotonic(), defects=defects, prepared=prepared,
    )["assessment"]
    assert assessment["evidence_status"] == ("asserted" if kind == "empty" else "observed_by_g6")
    found = assessment["completion"]["defects_reproduced"]["stated_and_repository_checks"]
    assert all(one.get("evidence_status") == "observed_by_g6" for one in found)
    report = assessment["report"]
    if static:
        assert found[0]["reproduction"]["imports"] == static["reproduction"]["imports"]
        assert "Read from the files, no code runs:" in report
        assert "shop/core.py" in report and "import shop.web" in report
        assert "python -m pytest layer" not in report
    if runs:
        assert "python -m pytest tests/test_sum.py::test_sum" in report
    if kind != "empty":
        assert "G6 did not run it" not in report


@pytest.mark.parametrize("bucket", ["stated_and_repository_checks", "proposed_checks"])
def test_asserted_finding_prevents_an_overall_observed_claim(bucket):
    assessment = {"outcome": "supported_within_scope", "evidence_status": "observed_by_g6",
                  "scope": {"checks_run": [{"evidence_status": "observed_by_g6"}]},
                  "completion": {"defects_reproduced": {bucket: [{
                      "check": "layer", "code": "forbidden_dependency_added", "evidence_status": "asserted",
                      "reproduction": {"kind": "static", "imports": [{"file": "shop/core.py", "line": 1,
                                                                         "text": "import shop.web"}]},
                  }]}}}
    report = render(assessment)
    assert report.startswith("**Agent-reported support; G6 did not run it**")
    assert "Read from the files, no code runs:" not in report


def test_nested_observation_flag_does_not_attribute_a_static_finding():
    report = render({"outcome": "defect_detected", "completion": {"defects_reproduced": {
        "stated_and_repository_checks": [{"check": "layer", "code": "forbidden_dependency_added",
            "reproduction": {"kind": "static", "evidence_status": "observed_by_g6", "imports": [
                {"file": "shop/core.py", "line": 1, "text": "import shop.web"}]}}]}}})
    assert "G6 did not run it" in report
    assert "Read from the files, no code runs:" not in report
