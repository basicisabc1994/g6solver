# SPDX-License-Identifier: Apache-2.0
"""The narrow view (design 17.3 and 17.4 step 2; AT-001 in part, AT-004, AT-008): observation, limits and the offered
check on separate labelled lines, no definite constraint wording, no value claim, a level nothing can raise past the
admitted one, and only a notice when the workflow source is not shared."""
from __future__ import annotations

import json

import pytest

from g6.harness.workflow import adapters, policy, view
from g6.harness.workflow import record as rec
from tests.workflow.kit import Step, at, loop_state, make_repo

TASK_TEXT = "fix this bug"
PROVIDER = "openai:codex"
BANNED = ("the constraint is", "the bottleneck is", "binding constraint", "root cause", "verified", "saves",
          "minutes")


@pytest.fixture
def repo(tmp_path, home):
    return make_repo(tmp_path / "repo")


@pytest.fixture
def ids(repo):
    return adapters.identities(repo, TASK_TEXT)


@pytest.fixture
def granted(ids, accepted_cloud_dialog):
    accepted_cloud_dialog(rec.workflow_source(ids[0]), PROVIDER, text="workflow record")
    return ids


def _failed(ident="R1"):
    return {"report": "REPORT", "outcome": "defect_detected", "reasons": [], "requirements": [
        {"id": ident, "status": "failed", "provenance": "stated",
         "checks": [{"id": "tests/test_calc.py::test_add", "verdict": "failed", "provenance": "stated"}]}]}


def _loop_run(repo, tmp_path, name="s1"):
    (tmp_path / name).mkdir(exist_ok=True)
    done = loop_state(1, done=True, ending="clean")
    assert adapters.observe_loop(repo, TASK_TEXT, tmp_path / name, done, Step("stop", "clean"), at=at(0)) == ""


def _fixtures(granted, repo, tmp_path) -> dict[str, dict]:
    project, task = granted
    _loop_run(repo, tmp_path)
    common = {"provider": PROVIDER, "request": TASK_TEXT}
    return {
        "obstacle": view.build(project, task, assessment=_failed(), intent={"goal": "add returns the sum"},
                               job_id="job-1", **common),
        "no outcome": view.build(project, task, assessment=_failed(), **common),
        "nothing found": view.build(project, task, assessment={"report": "R", "requirements": []}, **common),
        "unsent": view.build(project, task, loop={"ending": "untested", "run_id": "f" * 32, "report": "x",
                                                  "error": "[G6_E_CLOUD_CONSENT_REQUIRED] w"}, **common),
        "unobserved": view.build(project, adapters.identities(repo, "a task G6 never saw")[1], **common),
        "withheld": view.build(project, task, assessment=_failed(), provider="anthropic:claude_code", **{
            "request": TASK_TEXT}),
    }


def test_observation_inference_and_recommendation_are_on_separate_labelled_lines(granted, repo, tmp_path):
    lines = view.block(_fixtures(granted, repo, tmp_path)["obstacle"])
    labels = [line.split("**")[1] for line in lines[1:]]
    assert lines[0] == view.HEADING and "optional" in lines[0] and "decides nothing" in lines[0]
    assert labels == ["Stated outcome (as the request stated it):", "Observed, with a receipt:", "Not shown by this:",
                      "A next check you may run:", "G6's own cost on this task:"]
    observed, limits, offered = lines[2], lines[3], lines[4]
    assert "reproduced by G6" in observed and "receipt `review:job-1`" in observed and "pytest" not in observed
    assert "only obstacle" in limits and "wider workflow" in limits
    assert "`pytest tests/test_calc.py::test_add`" in offered and "you may run" in offered
    assert not any("hypothesis" in line.lower() for line in lines)        # the narrow level infers nothing


def test_no_definite_constraint_wording(granted, repo, tmp_path):
    for name, built in _fixtures(granted, repo, tmp_path).items():
        said = "\n".join(view.block(built)).lower()
        assert said and not [word for word in BANNED if word in said], name
        fields = json.dumps(built).lower()
        assert not [word for word in BANNED if word in fields], name
        assert "probability" not in fields and "confidence" not in fields and "score" not in fields


def test_missing_events_and_no_obstacle_say_observation_is_insufficient(granted, repo, tmp_path):
    found = _fixtures(granted, repo, tmp_path)
    unobserved = view.block(found["unobserved"])
    assert found["unobserved"]["obstacles"] == [] and adapters.NO_OBSERVATION in found["unobserved"]["missing"]
    assert any(line.startswith("- **Observation is insufficient:** G6 recorded nothing for this task") and
               "No obstacle is named." in line for line in unobserved)
    assert not any("Observed, with a receipt" in line for line in unobserved)
    nothing = view.block(found["nothing found"])
    assert any("Observation is insufficient" in line and "nothing G6 can show a receipt for" in line for line in nothing)
    assert any(line == "- **A next check you may run:** none" for line in nothing)


def test_text_from_the_repository_is_code_fenced(granted, repo, tmp_path):
    project, task = granted
    hostile = "`x` ``` <script>alert(1)</script> [link](https://example.invalid) \n# heading |a|b|"
    built = view.build(project, task, assessment=_failed(ident=hostile), intent={"goal": hostile}, provider=PROVIDER,
                       request=hostile, job_id="job-1")
    lines = view.block(built)
    assert all("\n" not in line for line in lines) and not any(line.startswith("# heading") for line in lines)
    from g6.harness import patch_report

    assert patch_report.code(built["outcome"]["shown"]) in lines[1]
    from tests.harness.test_patch_report import _outside_code

    for line in lines[1:3]:
        outside = _outside_code(line)
        assert "<script>" not in outside and "[link]" not in outside


def test_level_cannot_exceed_the_admitted_level(home, monkeypatch):
    assert view.ADMITTED_LEVEL == "narrow" and view.LEVELS == ("off", "narrow", "diagnosis", "suggestions")
    for asked, got in (("suggestions", "narrow"), ("diagnosis", "narrow"), ("narrow", "narrow"), ("off", "off"),
                       ("", "narrow"), ("everything", "narrow"), ("NARROW", "narrow")):
        monkeypatch.setenv(view.VIEW_ENV, asked)
        assert view.level() == got, asked
    monkeypatch.delenv(view.VIEW_ENV)
    assert view.level() == "narrow"
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "diagnosis")              # only a recorded gate result does this
    monkeypatch.setenv(view.VIEW_ENV, "suggestions")
    assert view.level() == "diagnosis"
    import inspect

    assert "level" not in inspect.signature(view.build).parameters       # no argument raises it


def test_level_off_gives_the_existing_reply_exactly(granted, repo, tmp_path, monkeypatch):
    from g6.mcp import workflow as tools

    project, task = granted
    reply = {"assessment": {"report": "REPORT", "requirements": []}, "job_id": "j"}
    for name, value in ((view.VIEW_ENV, "off"), (policy.RECORD_ENV, "off")):
        monkeypatch.setenv(name, value)
        assert view.level() == "off" and view.build(project, task, assessment=_failed(), provider=PROVIDER) == {}
        assert view.with_block("REPORT", view.build(project, task, provider=PROVIDER)) == "REPORT"
        assert tools._viewed(reply, str(repo), TASK_TEXT, ("assessment", "report")) is reply
        assert view.appended(str(repo), TASK_TEXT, "REPORT", provider=PROVIDER) == "REPORT"
        monkeypatch.delenv(name)
    monkeypatch.delenv(policy.RECORD_ENV, raising=False)                  # the record is off unless turned on
    assert view.level() == "off"


def test_withheld_view_holds_only_the_notice(granted, repo, tmp_path):
    built = _fixtures(granted, repo, tmp_path)["withheld"]
    assert built == {"level": "narrow", "withheld": True, "policy_version": policy.POLICY_VERSION}
    assert view.block(built) == [view.WITHHELD] and "g6_cloud_grant" in view.WITHHELD
    project, task = granted
    for provider in (None, "", "cloud:unknown", "anthropic:api"):
        assert view.build(project, task, assessment=_failed(), provider=provider)["withheld"] is True
    assert "REPORT\n\n" + view.WITHHELD == view.appended(str(repo), TASK_TEXT, "REPORT", provider="anthropic:claude_code")


def test_literal_request_and_stated_outcome_are_shown_apart(granted, repo, tmp_path):
    # AT-001, in part: the request and the outcome are separate fields, each with where it came from
    found = _fixtures(granted, repo, tmp_path)
    with_goal, without = found["obstacle"], found["no outcome"]
    assert with_goal["request"]["shown"] == TASK_TEXT and with_goal["request"]["said"] == "the request, as given"
    assert with_goal["outcome"]["shown"] == "add returns the sum"
    assert with_goal["outcome"]["provenance"] == "attributed_label" and TASK_TEXT not in with_goal["outcome"]["shown"]
    assert without["outcome"] is None and without["request"]["shown"] == TASK_TEXT
    assert "- **Stated outcome:** not stated. G6 asks nothing more about it here." in view.block(without)
    from g6.harness import question_store as qs

    assert not qs.TaskQuestions(granted[1]).exists()                      # no question was reserved to find it out


def test_checked_patch_with_no_outcome_measurement_says_value_not_measured(granted, repo, tmp_path):
    # AT-008: a review in state verified with no outcome measurement claims no value
    project, task = granted
    _loop_run(repo, tmp_path)
    assessment = {"report": "R", "outcome": "supported_within_scope", "completion_state": "verified",
                  "requirements": [{"id": "R1", "status": "passed_on_stated_checks", "provenance": "stated",
                                    "checks": [{"id": "t::a", "verdict": "passed"}]}]}
    built = view.build(project, task, assessment=assessment, intent={"goal": "add returns the sum"}, provider=PROVIDER)
    assert built["value_result"] == "not_measured" and built["value_scope"] == "this task, as G6 observed it"
    said = "\n".join(view.block(built))
    assert "**Value to your work:** not measured." in said
    assert not [word for word in ("improved", "faster", "saved", "impact") if word in said.lower()]
    assert set(view.VALUE_RESULTS) == {"not_measured", "observed", "self_reported", "controlled"}


def test_the_view_carries_g6s_own_cost_with_what_was_not_measured(granted, repo, tmp_path):
    project, task = granted
    _loop_run(repo, tmp_path)
    adapters.observe_call(repo, TASK_TEXT, "g6_review_until_clean", "r1", "a" * 64, started_at=at(1), seconds=0.5)
    built = view.build(project, task, provider=PROVIDER)
    assert built["friction"]["tool_calls"] == 1 and built["friction"]["provider_retries"] is None
    cost = view.block(built)[-1]
    assert cost.startswith("- **G6's own cost on this task:** 0 question(s), 0 approval(s), 1 tool call(s), 0.5 s of G6 time")
    assert built["policy_version"] == policy.POLICY_VERSION and built["level"] == "narrow"
