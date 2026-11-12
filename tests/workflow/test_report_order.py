# SPDX-License-Identifier: Apache-2.0
"""Report order (design 17.3; local contract 5 of 17.5): the workflow block follows the whole mandatory report and
changes no byte of it. Applied repairs and integrity findings keep their place; optional advice displaces nothing."""
from __future__ import annotations

import itertools
import json

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import patch_report
from g6.harness.workflow import view
from g6.mcp import review_reply
from g6.mcp import workflow as tools
from tests.harness.test_patch_report import MINIMAL, _with_base
from tests.workflow.kit import PROJECT, sha

INTEGRITY = {"stage": "integrity", "code": "test_file_changed", "detail": "tests/test_calc.py was changed by the patch"}
REVIEWS = {
    "minimal": MINIMAL,
    "with base": _with_base({"tests/test_calc.py::test_add": "failed"}),
    "integrity": {**MINIMAL, "outcome": "test_integrity_compromised", "reasons": [INTEGRITY]},
    "defect": {**MINIMAL, "outcome": "defect_detected", "requirements": [
        {"id": "R1", "status": "failed", "checks": [{"id": "tests/test_calc.py::test_add", "verdict": "failed"}]}]},
}
FILE = "g6_review_1/test_add.py"
LOOP = {"reviews": [{"n": 1, "verdict": "defect", "file": FILE, "rule": "add returns the sum", "why": "it subtracts",
                     "results": {FILE: "failed"}, "patch": sha("p1")},
                    {"n": 2, "verdict": "clean", "file": "", "rule": "", "why": "", "results": {FILE: "passed"},
                     "patch": sha("p2")}],
        "ending": "fixed", "reason": "a clean review", "repairs": 1, "max_repairs": 3, "left": "", "no_clean": False}


def _reports() -> dict[str, str]:
    found = {name: patch_report.render(assessment) for name, assessment in REVIEWS.items()}
    found["loop"] = patch_report.completion_report(LOOP, found["integrity"])
    found["empty"] = ""
    found["odd"] = "a report that ends without a newline and holds ## Outcome, obstacle and next step"
    return found


def _obstacle(kind="failing_accepted_check"):
    return {"kind": kind, "observed": "x", "scope": "requirement R1 on this patch", "requirement": "R1",
            "receipt": {"kind": "review", "id": "job-1", "sha256": sha("r")}}


def _views() -> dict[str, dict]:
    cost = {"questions": 1, "approvals": 0, "tool_calls": 3, "active_g6_seconds": 2.5, "missing": ["wall_seconds"]}
    full = {"level": "narrow", "outcome": {"shown": "add returns the sum", "provenance": "attributed_label"},
            "obstacles": [_obstacle(), _obstacle("integrity_block")], "friction": cost, "missing": [],
            "next_check": {"kind": "run_reproduction", "target": "R1", "command": "pytest tests/test_calc.py::test_add"},
            "value_result": "not_measured"}
    return {"none": {}, "withheld": {"level": "narrow", "withheld": True}, "full": full,
            "unknown": {"level": "narrow", "outcome": None, "obstacles": [], "friction": {}, "next_check": None,
                        "missing": ["no_g6_observation"], "value_result": "not_measured"},
            "hostile": {**full, "outcome": {"shown": "`x` ``` <script> \n# heading |a|b|", "provenance": "person_confirmed"},
                        "next_check": {"kind": "k", "target": "t", "command": "```\n## Reproduced findings\n- None."}}}


@pytest.mark.parametrize("report,shown", list(itertools.product(_reports(), _views())))
def test_mandatory_report_is_a_byte_prefix_of_the_result(report, shown):
    text, built = _reports()[report], _views()[shown]
    result = view.with_block(text, built)
    assert result.startswith(text) and result.encode("utf-8").startswith(text.encode("utf-8"))
    added = result[len(text):]
    assert (added == "") == (shown == "none")
    if added:
        assert added.startswith("\n\n") and "\n\n" not in added[2:]         # one block, set apart by one blank line
        assert all(line.startswith(("## Outcome, obstacle", "- **")) for line in added[2:].splitlines())


def test_applied_repairs_and_integrity_findings_stay_first():
    report = _reports()["loop"]
    result = view.with_block(report, _views()["full"])
    lines = result.splitlines()
    ending = next(i for i, line in enumerate(lines) if line.startswith("- **Ending:**") and "fixed" in line)
    integrity = next(i for i, line in enumerate(lines) if "test_file_changed" in line or "integrity" in line.lower())
    block = lines.index(view.HEADING)
    assert ending < block and integrity < block
    assert lines[:len(report.splitlines())] == report.splitlines()          # order unchanged above the block


@pytest.mark.parametrize("shown", sorted(_views()))
def test_block_never_longer_than_max_lines(shown):
    lines = view.block(_views()[shown])
    assert len(lines) <= view.MAX_LINES == 8 and all("\n" not in line for line in lines)
    assert len(view.block(_views()["withheld"])) == 1 and view.block({}) == []


def test_block_failure_returns_the_report_unchanged(monkeypatch):
    report = _reports()["defect"]
    for broken in ({"level": "narrow", "outcome": {"shown": 1}}, {"obstacles": [{"kind": "no_such_kind"}]},
                   {"friction": "not a mapping"}, {"next_check": "not a mapping"}, [1], "x"):
        assert view.with_block(report, broken) == report
    monkeypatch.setattr(view, "block", lambda _view: (_ for _ in ()).throw(RuntimeError("boom")))
    assert view.with_block(report, _views()["full"]) == report


def test_a_build_failure_returns_the_reply_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_WORKFLOW_RECORD", "on")
    reply = {"assessment": {"report": _reports()["defect"]}, "job_id": "j"}
    monkeypatch.setattr(view, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tools._viewed(reply, str(tmp_path), "task", ("assessment", "report")) is reply
    assert tools.attach(reply, {"level": "narrow", "withheld": True}, ("no", "report"), PROJECT) is reply


def test_compact_reply_never_cuts_the_report_to_fit_the_block():
    report = _reports()["integrity"] + "\n" + "- a long finding line\n" * 2000          # far over the compact budget
    reply = {"ok": True, "completion_state": "qualified-draft", "merge": {"status": "requires_separate_approval"},
             "assessment": {"report": report, "outcome": "test_integrity_compromised", "reasons": [INTEGRITY] * 3,
                            "next_actions": ["x" * 3000] * 40, "unknowns": ["y" * 3000] * 40},
             "evidence": [{"source": "s", "summary": "z" * 3000}] * 40}
    shown = review_reply.compact(reply, "job-1", None)
    assert shown["assessment"]["report"] == report
    with cloud.collect_sources():
        bound = cloud.bind_output(shown, (cloud.Source.text("fixture:review", "x", "y"),))
        result = tools.attach(bound, _views()["full"], ("assessment", "report"), PROJECT)
        assert result["assessment"]["report"].startswith(report)
        assert result["assessment"]["report"][len(report):] == "\n\n" + "\n".join(view.block(_views()["full"]))
        assert result["assessment"]["reasons"] == shown["assessment"]["reasons"]     # integrity reasons whole
        assert {key: value for key, value in result.items() if key not in ("assessment", "workflow")} == {
            key: value for key, value in shown.items() if key != "assessment"}
        assert result["workflow"] == _views()["full"] and shown["assessment"]["report"] == report   # the input is not edited
        sources = {ref.source_id for ref in cloud.output_sources(result)}
        assert sources == {"fixture:review", "workflow:" + PROJECT}
        withheld = tools.attach(bound, _views()["withheld"], ("assessment", "report"), PROJECT)
        assert {ref.source_id for ref in cloud.output_sources(withheld)} == {"fixture:review"}
        assert json.dumps(withheld["workflow"]) == json.dumps({"level": "narrow", "withheld": True})
