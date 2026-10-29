# SPDX-License-Identifier: Apache-2.0
"""The launch's security fix pass, part B: reviewer B's findings (templates, search, proposer, report), with the
owner's rulings. Development fixtures only; no model is called (``FakeLLM``). Tests that take ``bwrap_backend`` run
real confined runs (Linux bubblewrap; in Docker on Windows)."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM
from g6.harness import verification_templates as vt
from g6.harness.patch_review import review_patch
from g6.workforce import repo_tests


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], check=True, capture_output=True)


def _repo(root: Path, files: dict[str, str], patch: dict[str, str]) -> Path:
    """A repository whose base commit holds ``files``, with ``patch`` written over it."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    for rel, text in patch.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _codes(got) -> list[tuple[str, str]]:
    return [(r["stage"], r["code"]) for r in got["assessment"]["reasons"]]


# --- B1: a model's reply never chooses a normative provenance ---------------------------------------------------------

PYPROJECT = ('[tool.importlinter]\nroot_package = "app"\n\n[[tool.importlinter.contracts]]\n'
             'name = "core never imports web"\ntype = "forbidden"\nsource_modules = ["app.core"]\n'
             'forbidden_modules = ["app.web"]\n')
CORE = {"pyproject.toml": PYPROJECT, "app/__init__.py": "", "app/web.py": "def page():\n    return 'page'\n",
        "app/core.py": "def total(xs):\n    return sum(xs)\n",
        "test_core.py": "from app.core import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"}
IMPORTS_WEB = {"app/core.py": "from app import web\n\n\ndef total(xs):\n    return sum(xs)\n"}
TOTALS = [{"id": "R1", "text": "total sums the list", "checks": ["test_core.py::test_total"]}]


def _instance(provenance: str) -> vt.TemplateInstance:
    return vt.TemplateInstance(id="layers", template="forbidden_dependency.v1", requirement_id="R1",
                               intent="preserve", bindings={"contract": "repository"}, provenance=provenance)


@pytest.mark.parametrize(("provenance", "normative", "verdict"), [("model_hypothesis", False, "counterexample"),
                                                                  ("stated", True, "failed")])
def test_an_instance_a_model_proposed_is_never_normative_whatever_contract_it_names(tmp_path, provenance, normative,
                                                                                   verdict):
    root = _repo(tmp_path, CORE, IMPORTS_WEB)
    prepared = vt.prepare(root, repo_tests.change(root), [_instance(provenance)], deadline=time.monotonic() + 60)
    [found] = prepared.structural
    assert (found["normative"], found["verdict"]) == (normative, verdict), found["detail"]
    assert found["contract_provenance"] == "repository_stated"   # the contract is still the repository's


def test_an_instance_a_model_proposed_that_does_not_apply_is_never_normative(tmp_path):
    files = {rel: text for rel, text in CORE.items() if rel != "pyproject.toml"}   # no contract at the base commit
    root = _repo(tmp_path, files, IMPORTS_WEB)
    prepared = vt.prepare(root, repo_tests.change(root), [_instance("model_hypothesis")],
                          deadline=time.monotonic() + 60)
    [found] = prepared.structural
    assert found["verdict"] == "not_run" and found["normative"] is False


def _proposing(stage_reply: dict) -> FakeLLM:
    """A stand-in for the model: each stage's reply from ``stage_reply``, else nothing proposed."""
    def respond(system, user):
        stage = json.loads(json.loads(user)["input"])["stage"]
        if stage in stage_reply:
            return json.dumps(stage_reply[stage])
        return json.dumps({"scenarios": {"questions": []}, "free_form": {"checks": []}}.get(stage, {"instances": []}))
    return FakeLLM(respond)


def test_confined_a_model_proposed_repository_contract_gives_at_most_insufficient_evidence(tmp_path, bwrap_backend):
    """Reviewer B's probe (probe_proposer_repository_contract.py)."""
    root = _repo(tmp_path, CORE, IMPORTS_WEB)
    llm = _proposing({"forbidden_dependency.v1": {"instances": [
        {"requirement_id": "R1", "intent": "preserve", "bindings": {"contract": "repository"}}]}})
    got = review_patch(root, task="Keep totals.", requirements=TOTALS, search={"propose": True}, llm=llm,
                       timeout_s=240)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert ("evidence", "unconfirmed_counterexample") in _codes(got), assessment["reasons"]
    assert next(r for r in assessment["requirements"] if r["id"] == "R1")["status"] == "passed_on_stated_checks"


def test_confined_the_same_instance_the_caller_states_is_a_defect_as_before_b1(tmp_path, bwrap_backend):
    root = _repo(tmp_path, CORE, IMPORTS_WEB)
    got = review_patch(root, task="Keep totals.", requirements=TOTALS, timeout_s=240, template_instances=[
        {"id": "layers", "template": "forbidden_dependency.v1", "requirement_id": "R1", "intent": "preserve",
         "bindings": {"contract": "repository"}}])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]["reasons"]
    assert ("tier_1", "forbidden_dependency_added") in _codes(got)
