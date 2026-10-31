# SPDX-License-Identifier: Apache-2.0
"""A1, the readable report: ``assessment.report``, Markdown a coding agent shows the person verbatim
(``g6.harness.patch_report.render``). It is derived from the decided fields and decides nothing: reproduced findings
first (integrity first among them), then what is not established, then what the person decides, then how the review
was decided in four steps, then the standing note. Text the repository or the caller wrote is shown inert.

Development fixtures only: the engineering trial's six cases (``validation/patch_review/run.py``, confined runs) and
small repositories with recorded runs; no model is called."""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import patch_report, patch_review
from g6.harness.patch_review import review_patch
from g6.invariants import check_envelope

TRIAL = Path(__file__).resolve().parents[2] / "validation" / "patch_review" / "run.py"
BANNED = ("verified", "proven", "proves", "safe", "guaranteed", "guarantee", "certificate", "confidence")
BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_negative():\n    assert add(-2, -3) == -5\n")
ADD, NEGATIVE = "test_calc.py::test_add", "test_calc.py::test_negative"
SUM = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": [ADD]},
       {"id": "R2", "text": "add returns the sum of two negative integers", "checks": [NEGATIVE]}]


def _trial_replies() -> dict[str, dict]:
    """Each engineering case's reply, from the trial's own calls."""
    spec = importlib.util.spec_from_file_location("patch_review_trial_report", TRIAL)
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    real, got = trial.review_patch, []

    def recorded(*args, **kwargs):
        reply = real(*args, **kwargs)
        got.append(reply)
        return reply

    trial.review_patch = recorded
    rows = trial.run()["cases"]
    return {row["case"]: reply for row, reply in zip(rows, got, strict=True)}


def _sections(report: str) -> list[str]:
    return [line.strip("# ").strip() for line in report.splitlines() if line.startswith("## ")]


def _outside_code(text: str) -> str:
    """The report with every code span taken out, as Markdown reads it: what is left is G6's own words."""
    return re.sub(r"(`+)(?:(?!\1).)+?\1", "", text)


def _common(report: str) -> None:
    lines = report.splitlines()
    assert len(lines) <= patch_report.MAX_LINES + 2, len(lines)
    words = set(re.findall(r"[a-z]+", report.lower()))
    assert not words & set(BANNED), words & set(BANNED)
    assert "merging needs your separate approval" in lines[0].lower()
    assert "qualified draft" in lines[0].lower()
    assert not re.search(r"\d", _outside_code(lines[0]).split("(")[0]), lines[0]   # no score or count opens it
    assert "## How this was decided" in report
    assert not re.search(r"\btier\b", report, re.IGNORECASE), report   # no internal names: plain ones
    assert "/dev/null" not in report and "--confcutdir" not in report
    assert [line.split(":")[0].strip("-* ") for line in lines if line.startswith(("- **Proposed", "- **Tested",
                                                                                 "- **Gate", "- **Approve"))] == [
        "Proposed", "Tested", "Gate", "Approve"]
    assert patch_report.STANDING_NOTE in report


_REPLIES: dict[str, dict] = {}


@pytest.fixture
def trial(bwrap_backend) -> dict[str, dict]:
    """The trial's replies, made once for this module's confined tests."""
    if not _REPLIES:
        _REPLIES.update(_trial_replies())
    return _REPLIES


def test_confined_the_correct_patch_reads_supported_with_its_demonstrated_requirements(trial):
    report = trial["correct"]["assessment"]["report"]
    _common(report)
    assert report.splitlines()[0].startswith("**Supported within its stated scope**")
    demonstrated = report.split("## Demonstrated on the stated checks", 1)[1].split("##", 1)[0]
    assert "`R1`" in demonstrated and "`R2`" in demonstrated and ADD in demonstrated
    # the base code subtracts, so both stated checks fail there: they exercise the change, and the report says so
    assert all("fails at the base commit, passes on the patch" in line for line in demonstrated.strip().splitlines())
    tested = next(line for line in report.splitlines() if line.startswith("- **Tested"))
    assert "the stated checks fail at the base commit, so they exercise the change" in tested, tested
    assert "the stated checks" in tested and "tests that touch the changed code" in tested
    assert "deeper checks (not run)" in tested


def test_confined_a_defect_comes_first_with_a_command_that_reproduces_it(trial):
    report = trial["unfixed"]["assessment"]["report"]
    _common(report)
    assert report.splitlines()[0].startswith("**A defect was found**")
    assert _sections(report)[0] == "Reproduced findings"
    findings = report.split("## Reproduced findings", 1)[1].split("##", 1)[0]
    assert f"`python -m pytest {NEGATIVE}`" in findings and "`R2`" in findings   # the repository's plain command
    assert "not run at the base commit" in findings
    assert "isolating options" in findings and "reproduction.command" in findings


@pytest.mark.parametrize("case", ["weakened-test", "runner-changed"])
def test_confined_an_integrity_finding_leads(trial, case):
    report = trial[case]["assessment"]["report"]
    _common(report)
    assert _sections(report)[0] == "Reproduced findings"
    first = report.split("## Reproduced findings", 1)[1].strip().splitlines()[0]
    assert first.startswith("- **Test integrity") and "(" in first, first


def test_confined_a_requirement_with_no_check_is_not_established(trial):
    report = trial["unchecked-requirement"]["assessment"]["report"]
    _common(report)
    assert report.splitlines()[0].startswith("**Not enough evidence**")
    missing = report.split("## Not established", 1)[1].split("##", 1)[0]
    assert "`R2`" in missing and "no check was stated" in missing
    assert "stale-artifact" in trial and "assessment" not in trial["stale-artifact"]   # a refusal has no report


# --- recorded runs: the rules of the text -------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return tmp_path


def result(passed=(), failed=()):
    outcomes = {**{n: {"setup": "passed", "call": "passed", "teardown": "passed"} for n in passed},
                **{n: {"setup": "passed", "call": "failed", "teardown": "passed"} for n in failed}}
    return {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
            "not_passed": 0, "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [],
            "output": "".join(f"FAILED {n} - AssertionError: assert -1 == -5\n" for n in failed), "seconds": 0.25,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": "", "rootdir": ""}


def _runs(monkeypatch, failing=()):
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **_: result(
        [i for i in ids if i not in failing], [i for i in ids if i in failing]))
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **_: {"verdict": "not_run", "said": "not recorded"})


HOSTILE = "R `x` `` ``` <script>alert(1)</script> [link](https://example.invalid) \n# heading\n<b>bold</b> |a|b|"


def test_text_the_repository_or_the_caller_wrote_is_shown_inert(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    _runs(monkeypatch)
    hostile_node = "test_gone.py::test_x[`]](https://example.invalid)<script>`]"
    got = review_patch(repo, task="t", requirements=[
        {"id": "R1", "text": HOSTILE, "checks": [ADD]},
        {"id": "R2", "text": HOSTILE, "checks": [hostile_node]},
        {"id": "R3", "text": "# heading\n- [x](y)", "checks": []}])
    report = got["assessment"]["report"]
    _common(report)
    plain = _outside_code(report)
    for token in ("<script>", "</script>", "<b>", "[link]", "](", "https://example.invalid", "|a|b|"):
        assert token not in plain, (token, plain)
    assert not any(line.lstrip().startswith(("# heading", "#heading")) for line in report.splitlines())
    assert all(not line.startswith("#") or line.startswith("## ") for line in report.splitlines())
    assert "<script>" in report   # shown, inside a code span, never dropped silently


def test_the_report_is_cut_at_its_limit_and_says_where_the_rest_is(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    _runs(monkeypatch)
    many = [{"id": f"R{i}", "text": f"behaviour number {i}", "checks": []} for i in range(30)]
    got = review_patch(repo, task="t", requirements=many)
    report = got["assessment"]["report"]
    _common(report)
    assert re.search(r"and \d+ more \(`assessment\.completion\.not_established`\)", report), report


def test_base_evidence_is_said_in_plain_words_only_where_it_means_the_check_exercises_the_change(repo, monkeypatch):
    """A requirement's stated check that fails at the base commit and passes on the patch exercises the change; for a
    requirement that keeps behaviour, a failure at the base keeps its meaning and is not shown as that."""
    (repo / "calc.py").write_text(GOOD)
    _runs(monkeypatch)
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **_: {**result(failed=list(ids)), "isolation": {
                            "backend": "synthetic-unit-fixture"}})
    got = review_patch(repo, task="t", requirements=[
        patch_review.Requirement(id="R1", text="adding stays as it was", kind="preserve", checks=[ADD]),
        {"id": "R2", "text": "add returns the sum of two negative integers", "checks": [NEGATIVE]}])
    assert "assessment" in got, got
    demonstrated = got["assessment"]["report"].split("## Demonstrated on the stated checks", 1)[1].split("##", 1)[0]
    lines = {line.split("`")[1]: line for line in demonstrated.strip().splitlines()}
    assert "fails at the base commit, passes on the patch" in lines["R2"]
    assert "passes on the patch" not in lines["R1"]


def test_a_run_that_exits_with_an_error_after_passing_is_said_in_plain_words(repo, monkeypatch):
    """The owner's ruling (2026-10-29): not enough evidence, never an accusation of tampering."""
    from g6.workforce import repo_tests

    (repo / "calc.py").write_text(GOOD)
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **_: {
        **result(list(ids)), "verdict": "run_inconsistent", "said": repo_tests.SAID["run_inconsistent"]})
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **_: {"verdict": "not_run", "said": "not recorded"})
    report = review_patch(repo, task="t", requirements=SUM)["assessment"]["report"]
    assert report.startswith("**Not enough evidence**"), report
    assert "Test integrity" not in report and "test_integrity_compromised" not in report
    tested = next(line for line in report.splitlines() if line.startswith("- **Tested"))
    assert "passed by pytest's own count, but the process exited with an error afterwards" in tested, tested
    assert "run_inconsistent" in next(line for line in report.splitlines() if line.startswith("- **Gate"))


def test_the_report_decides_nothing(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    _runs(monkeypatch, failing=(NEGATIVE,))
    got = review_patch(repo, task="t", requirements=SUM)
    assessment = got["assessment"]
    report = assessment.pop("report")
    assert patch_report.render(assessment) == report   # derived from the other fields alone, the same each time
    assert assessment["outcome"] == "defect_detected"
    assert got["completion_state"] == "qualified-draft"


def test_the_mcp_tool_carries_the_report_and_keeps_its_envelope_and_codes(repo, monkeypatch):
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    (repo / "calc.py").write_text(GOOD)
    _runs(monkeypatch)
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow():
        tool = (await server.get_tools())["g6_review_patch"]
        async with Client(server) as client:
            job = data(await client.call_tool("g6_review_patch", {"repo": str(repo), "task": "t",
                                                                  "requirements": SUM}))
            for _ in range(400):
                got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                if got.get("status") in ("completed", "failed"):
                    return tool, got
                await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    tool, got = asyncio.run(flow())
    assert got["status"] == "completed", got
    reply = got["result"]
    assert reply["completion_state"] == "qualified-draft" and reply["warning_card"]["code"] == "G6_E_PATCH_REVIEW"
    assert check_envelope(reply) == [] and reply["assessment"]["report"].startswith("**Supported")
    assert "assessment.report" in " ".join(tool.description.split())


def test_the_docs_that_describe_the_reply_name_the_report():
    docs = Path(__file__).resolve().parents[2] / "docs" / "AI_FOR_AI_WORKFLOW.md"
    assert "assessment.report" in docs.read_text(encoding="utf-8")


#: An assessment as small as the report reads it: a supported patch on its stated checks.
MINIMAL = {"outcome": "supported_within_scope", "reasons": [], "requirements": [{"id": "R1"}],
           "scope": {"artifact_digest": "a" * 64, "base_commit": "b" * 40, "search": {}, "checks_run": []},
           "completion": {}}


def test_the_report_says_the_checks_were_stated_in_the_request_never_by_you():
    """Reviewer B's B3: in the launch flow the agent states the requirements and their checks, not the person."""
    report = patch_report.render(MINIMAL)
    proposed = next(line for line in report.splitlines() if line.startswith("- **Proposed:**"))
    assert "checks from the checks stated in the request" in proposed, proposed
    assert not re.search(r"\byou\b", proposed), proposed


def test_a_proposed_checks_reproduction_carries_g6s_caution():
    """Reviewer B's B6: the check is code someone other than G6 wrote."""
    assessment = {**MINIMAL, "outcome": "insufficient_evidence", "completion": {"defects_reproduced": {
        "proposed_checks": [{"check": "keep-neg", "requirement": "R1", "result": "counterexample",
                             "code": "unconfirmed_counterexample",
                             "reproduction": {"file": "g6_check_x/test_keep.py", "node": "g6_check_x/test_keep.py::t",
                                              "at_base": "passed: 1 test(s) passed"}}]}}}
    report = patch_report.render(assessment)
    line = next(line for line in report.splitlines() if line.lstrip().startswith("- Reproduce: write its text"))
    assert "Read it before you run it, or run it in a container." in line, line


# --- the owner's pilot (2026-10-30) -------------------------------------------------------------------------------

def _with_base(outcomes: dict[str, str], **fields) -> dict:
    """MINIMAL with one requirement demonstrated on ``outcomes``'s checks, and the base run's verdict for each."""
    return {**MINIMAL, **fields,
            "completion": {"demonstrated": [{"requirement": "R1", "kind": "requirement", "checks": list(outcomes)}],
                           **fields.get("completion", {})},
            "scope": {**MINIMAL["scope"], "checks_run": [
                {"check": "stated checks at the base commit", "verdict": "failed", "outcomes": outcomes}]}}


def _tested(report: str) -> str:
    return next(line for line in report.splitlines() if line.startswith("- **Tested"))


def test_checks_that_could_not_run_at_the_base_commit_are_never_said_to_fail_there():
    """A test file that imports a name the patch adds cannot load at the base commit: its checks did not run there."""
    report = patch_report.render(_with_base({"t.py::a": "not_run", "t.py::b": "not_run"}))
    assert "fails at the base commit" not in report and "so they exercise the change" not in report
    assert ("the stated checks could not run at the base commit, so G6 cannot say whether they exercise the change"
            in _tested(report)), _tested(report)


def test_a_mixed_base_run_counts_each_kind_in_plain_words():
    report = patch_report.render(_with_base({"t.py::a": "failed", "t.py::b": "passed", "t.py::c": "not_run"}))
    tested = _tested(report)
    assert "1 of the 3 stated checks fails at the base commit" in tested, tested
    assert "1 passes there too" in tested and "1 could not run there" in tested, tested


def test_the_unknowns_are_listed_not_only_counted():
    report = patch_report.render({**MINIMAL, "unknowns": ["first unknown", "second unknown"]})
    decide = report.split("## For you to decide", 1)[1].split("##", 1)[0]
    assert "`first unknown`" in decide and "`second unknown`" in decide, decide
    assert "assessment.unknowns" not in decide
    assert decide.strip().splitlines()[-1] == "- Merging: it needs your separate approval; G6 never merges."


def test_many_unknowns_are_cut_at_the_reports_limit_and_say_where_the_rest_are():
    report = patch_report.render({**MINIMAL, "unknowns": [f"unknown {i}" for i in range(9)]})
    decide = report.split("## For you to decide", 1)[1].split("##", 1)[0].strip().splitlines()
    assert len(decide) <= patch_report.BUDGET["decide"], decide
    assert any("more (`assessment.unknowns`)" in line for line in decide), decide
    assert decide[-1] == "- Merging: it needs your separate approval; G6 never merges."


def test_the_agents_own_checks_are_reported_with_their_results_and_decide_nothing():
    """The pilot's T1 and T2 ran and were not refuted, yet the report never named them."""
    activities = [{"activity": "template instance", "check": "T1", "requirement": "R1", "result": "not_refuted"},
                  {"activity": "proposed check", "check": "keep", "requirement": "R2", "result": "fails_on_base_too"},
                  {"activity": "check run", "tier": 1, "what": "stated checks and extra tests", "result": "passed"}]
    report = patch_report.render({**MINIMAL, "completion": {"activities": activities}})
    tested = _tested(report)
    assert "which never pass a requirement" in tested and "decide nothing" not in tested, tested
    assert "`T1` for `R1`: not refuted" in tested and "`keep` for `R2`: fails at the base commit too" in tested, tested


def test_a_structural_instances_result_is_said_in_plain_words_and_not_said_to_decide_nothing():
    """The fresh review of the pilot's fixes: a failed forbidden_dependency.v1 instance against a stated contract is a
    defect, so the Tested step may not call it a check that decides nothing, and says its result in words."""
    activities = [{"activity": "template instance", "check": "FD1", "requirement": "R1", "result": "failed",
                   "provenance": "structural"},
                  {"activity": "template instance", "check": "FD2", "requirement": "R2", "result": "vacuous"}]
    tested = _tested(patch_report.render({**MINIMAL, "outcome": "defect_detected",
                                          "completion": {"activities": activities}}))
    assert "`FD1` for `R1`: failed" in tested and "``failed``" not in tested and "`failed`" not in tested, tested
    assert "`FD2` for `R2`: vacuous" in tested and "decide nothing" not in tested and "(above)" not in tested, tested


def test_the_gate_says_when_a_proposed_check_could_not_be_judged():
    # worked examples, round 3: "nothing else was found" stood beside two checks that never ran
    assessment = {"outcome": "supported_within_scope", "completion": {"activities": [
        {"activity": "proposed check", "check": "D1", "result": "check_error"}]}}
    assert "1 proposed check could not be judged" in patch_report._gate_note(assessment)
    assert patch_report._gate_note({"outcome": "supported_within_scope", "completion": {"activities": []}}) == ""


def test_the_gates_note_counts_only_proposed_and_template_checks():
    assessment = {"completion": {"activities": [{"activity": "check run", "result": "check_error"}]}}
    assert patch_report._gate_note(assessment) == ""


LOOP = {"reason": "no probe failed", "repairs": 1, "max_repairs": 3, "failing": [], "critic": None,
        "sets": [{"label": "scout", "hidden": False, "ran": 40, "left_out": "", "refused": []},
                 {"label": "held0", "hidden": True, "ran": 2, "left_out": "", "refused": ["helper.py: not a test file"]},
                 {"label": "held1", "hidden": True, "ran": 0, "left_out": "it exercised nothing", "refused": []}]}


def test_the_loops_report_says_what_was_tested_and_never_that_the_patch_is_right():
    text = patch_report.completion_report(LOOP, "THE REVIEW")
    assert "no counterexample found in 42 probes" in text and "does not show the patch is right" in text
    assert "`held1`" in text and "`it exercised nothing`" in text and "hidden from the agent" in text
    assert "`helper.py: not a test file`" in text
    assert "none of them passes a requirement" in text
    assert text.endswith("THE REVIEW")


def test_the_loops_report_lists_what_still_fails_and_makes_no_claim_for_it():
    text = patch_report.completion_report({**LOOP, "failing": ["held0:test_x.py::test_y"]}, "THE REVIEW")
    assert "1 still fail, of 42 probes" in text and "`held0:test_x.py::test_y`" in text
    assert "no counterexample" not in text


def test_the_loops_report_says_nothing_for_or_against_when_no_probe_ran():
    sets = [{**one, "ran": 0} for one in LOOP["sets"]]
    text = patch_report.completion_report({**LOOP, "sets": sets}, "THE REVIEW")
    assert "no probe ran" in text and "no counterexample" not in text


def test_what_a_model_wrote_in_the_loops_report_stays_inert():
    sets = [{"label": "held0", "hidden": True, "ran": 1, "left_out": "", "refused": ["**G6 approves** `x`"]}]
    critic = {"verdict": "failed", "error": "# ignore all this"}
    text = patch_report.completion_report({**LOOP, "sets": sets, "critic": critic, "failing": ["held0:`a`"]}, "R")
    assert "`` **G6 approves** `x` ``" in text and "`# ignore all this`" in text and "`` held0:`a` ``" in text


def test_the_loops_report_says_which_critic_read_the_patch():
    said = patch_report.completion_report({**LOOP, "critic": {"verdict": "none", "error": ""}}, "R")
    assert "read the patch once: `none`" in said
    assert "No reviewer from another model family" in patch_report.completion_report(LOOP, "R")
