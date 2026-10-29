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


def test_confined_a_defect_comes_first_with_a_command_that_reproduces_it(trial):
    report = trial["unfixed"]["assessment"]["report"]
    _common(report)
    assert report.splitlines()[0].startswith("**A defect was found**")
    assert _sections(report)[0] == "Reproduced findings"
    findings = report.split("## Reproduced findings", 1)[1].split("##", 1)[0]
    assert "`python -m pytest" in findings and NEGATIVE in findings and "`R2`" in findings
    assert "not run at the base commit" in findings


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
