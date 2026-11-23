# SPDX-License-Identifier: Apache-2.0
"""Every finding has a runnable case command, and skipped activities keep their reasons."""
from g6.harness import patch_report as report


def test_open_and_fixed_findings_show_rule_path_and_case_command():
    first = {"n": 1, "file": "test_critic.py", "rule": "keep the stated value", "why": "value differs",
             "test_path": "review-state/sets/critic/test_critic.py",
             "verdict": "defect", "results": {"test_critic.py": "failed"},
             "failed": {"test_critic.py": ["test_critic.py::test_value"]},
             "observations": {"test_critic.py": {
                 "parent": {"exit": 1, "results": {"test_critic.py::test_value": "failed"}, "texts": {}},
                 "patch": {"exit": 1, "results": {"test_critic.py::test_value": "failed"}, "texts": {}}}}}
    opened = report.completion_report({"reviews": [first], "ending": "open"}, "local review")
    assert "Reproduce:" in opened and "python -m pytest test_critic.py::test_value" in opened
    assert first["test_path"] in opened
    second = {"n": 2, "file": "test_critic_2.py", "rule": "none", "why": "", "verdict": "none",
              "results": {"test_critic.py": "passed"}, "failed": {}}
    fixed = report.completion_report({"reviews": [first, second], "ending": "fixed"}, "local review")
    assert "**Fixed:**" in fixed and "keep the stated value" in fixed
    assert "python -m pytest test_critic.py::test_value" in fixed and "passes now" in fixed


def test_every_skip_and_full_reason_is_shown_within_line_cap():
    skipped = [{"tier": 3, "check": f"activity-{n}", "reason": f"not run: reason-{n}-" + "x" * 90}
               for n in range(30)]
    assessment = {"outcome": "insufficient_evidence", "scope": {"checks_skipped": skipped}}
    rendered = report.render(assessment)
    for item in skipped:
        assert item["check"] in rendered and item["reason"] in rendered
    assert len(rendered.splitlines()) <= report.MAX_LINES
    assert "more not run" not in rendered


def test_skipped_search_activity_and_base_run_are_named_too():
    assessment = {"scope": {}, "completion": {"activities": [
        {"activity": "proposed check", "check": "new-case", "result": "skipped", "reason": "budget exhausted"},
        {"activity": "proposed check", "check": "other-case", "result": "check_error", "runs": [
            {"on": "base commit", "result": "skipped", "reason": "no time for parent"}]}]}}
    rendered = report.render(assessment)
    assert "new-case" in rendered and "budget exhausted" in rendered
    assert "other-case" in rendered and "no time for parent" in rendered
