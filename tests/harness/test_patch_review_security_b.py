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

# --- B2: the patch cannot silence a counterexample ---------------------------------------------------------------------

NODE = "g6_check_x/test_keep.py::test_neg"


def _ran(kind: str, node: str = NODE) -> dict:
    """One recorded run of a check: passed, failed (an assertion), os_error (a failure G6's own record says an
    operating-system error was raised from or during), or timed_out. Printed text that names a refusal is in every
    failed one's output and message, as code under test can print it."""
    printed = "cache: Connection refused, using local value\nE   OSError: [Errno 111] Connection refused\n"
    if kind == "passed":
        return {"verdict": "passed", "seconds": 0.1, "output": printed + "1 passed",
                "test_outcomes": {node: {"setup": "passed", "call": "passed", "teardown": "passed"}}}
    if kind == "timed_out":
        return {"verdict": "timed_out", "said": "it ran out of time", "seconds": 1.0, "output": ""}
    found = {"verdict": "failed", "seconds": 0.1, "output": printed + f"FAILED {node} - assert 2 == -2",
             "test_outcomes": {node: {"setup": "passed", "call": "failed", "teardown": "passed"}},
             "failure_messages": {node: "assert 2 == -2\n" + printed}}
    if kind == "os_error":
        found.update(os_errors=[node], failure_messages={node: "RuntimeError: the cache is unreachable"})
    return found


def test_a_refusal_is_read_from_g6s_own_record_of_the_run_never_from_text_the_code_printed():
    from g6.harness import verification_search as vs

    assert vs.reading(_ran("failed"), NODE)["result"] == "failed"   # the printed words name a refusal: not read
    assert vs.reading(_ran("os_error"), NODE)["result"] == "error"   # the record says so: a refused access, perhaps
    collected = {"verdict": "collection_error", "seconds": 0.1, "test_outcomes": {}, "os_errors": ["g6_check_x/test_keep.py"],
                 "output": "ERROR collecting g6_check_x/test_keep.py\n",
                 "failure_messages": {"g6_check_x/test_keep.py": "ImportError while importing test module"}}
    assert vs.reading(collected, NODE)["result"] == "error"


def _search(tmp_path, monkeypatch, on_patch: str, at_base: str):
    """``verification_search.search`` of one preserve check, its run on the patch giving ``on_patch`` (``_ran``'s
    kinds) and at the base commit ``at_base``; what it did, and which runs it asked for (True: at the base)."""
    from g6.harness import verification_search as vs

    root = _repo(tmp_path, {"calc.py": "def neg(a):\n    return -a\n"}, {"calc.py": "def neg(a):\n    return a\n"})
    one = vs.ProposedCheck.model_validate({"id": "keep-neg", "requirement_id": "R1", "intent": "preserve",
                                           "file": "test_keep.py",
                                           "source": "from calc import neg\n\n\ndef test_neg():\n    assert neg(2) == -2\n"})
    asked: list[bool] = []

    def run_check(change, placed, source, node, *, at_base, timeout_s, **_):
        asked.append(at_base)
        return _ran(base if at_base else patch, node)

    patch, base = on_patch, at_base
    monkeypatch.setattr(vs, "_run_check", run_check)
    done = vs.search(repo_tests.change(root), [one], {one.id: vs.placement(one)}, deadline=time.monotonic() + 120,
                     artifact="x")
    return done, asked


@pytest.mark.parametrize("on_patch", ["os_error", "timed_out"])
def test_a_check_that_errors_on_the_patch_and_runs_to_a_result_at_the_base_is_insufficient_evidence(
        tmp_path, monkeypatch, on_patch):
    done, asked = _search(tmp_path, monkeypatch, on_patch, "passed")
    assert asked == [False, True]   # run at the base commit too, though the patch's run errored
    [activity] = done.activities
    assert activity["result"] == "check_error", activity
    assert [code for code, _ in done.reasons] == ["check_error_on_patch"], done.reasons
    assert any("keep-neg" in action for action in done.next_actions)


def test_a_broken_check_that_errors_at_the_base_and_on_the_patch_adds_no_reason(tmp_path, monkeypatch):
    done, _ = _search(tmp_path, monkeypatch, "os_error", "os_error")
    assert done.activities[0]["result"] == "check_error" and done.reasons == []


LOUD = {"calc.py": "def add(a, b):\n    return b + a\n\n\ndef neg(a):\n    print('cache: Connection refused, using "
                   "local value')\n    return a\n"}
VERSIONS = {
    "quiet": {"calc.py": "def add(a, b):\n    return b + a\n\n\ndef neg(a):\n    return a\n"},
    "loud": LOUD,   # reviewer B's probe: the broken function prints a refusal's words
    "raises": {"calc.py": "def add(a, b):\n    return b + a\n\n\ndef neg(a):\n    raise OSError('Connection "
                          "refused')\n"},   # it errors on the patch, as a refused access would
}
NEG_BASE = {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef neg(a):\n    return -a\n",
            "test_add.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
KEEP_NEG = {"id": "keep-neg", "requirement_id": "R1", "intent": "preserve", "file": "test_keep_neg.py",
            "source": "from calc import neg\n\n\ndef test_neg():\n    assert neg(2) == -2\n"}
NEG_EXAMPLE = {"id": "neg-example", "template": "example.v1", "requirement_id": "R1", "intent": "preserve",
               "bindings": {"function": "calc.neg", "cases": [{"args": [2], "returns": -2}]}}
ADD = [{"id": "R1", "text": "add returns the sum", "checks": ["test_add.py::test_add"]}]


@pytest.mark.parametrize("version", sorted(VERSIONS))
def test_confined_the_patch_cannot_silence_a_counterexample(tmp_path, bwrap_backend, version):
    """Reviewer B's probe (probe_classifier_refusal_text.py), with a third version that raises what a refused access
    raises: every version gives the same outcome."""
    root = _repo(tmp_path, NEG_BASE, VERSIONS[version])
    got = review_patch(root, task="Tidy add.", requirements=ADD, proposed_checks=[KEEP_NEG],
                       template_instances=[NEG_EXAMPLE], timeout_s=240)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", (version, assessment["reasons"])
    codes = {code for _, code in _codes(got)}
    assert codes & {"unconfirmed_counterexample", "check_error_on_patch"}, (version, codes)

# --- B7: public text says what is true --------------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]


def _words(text: str) -> str:
    return " ".join(text.replace("`", "").split())


def _read(rel: str) -> str:
    return _words((ROOT / rel).read_text(encoding="utf-8"))


def test_the_docs_say_what_the_plugin_check_refuses_and_what_it_does_not_detect():
    from g6.harness import verification_proposer

    guide = _read("docs/AI_FOR_AI_WORKFLOW.md")
    assert ("A proposed conftest, a runner configuration and a plain plugin declaration (pytest_plugins) are "
            "refused; a check that builds one at run time is not detected, and the checks you propose yourself are "
            "not filtered for one; either way, a proposed check never passes a requirement.") in guide
    assert "a conftest, a configuration or a plugin is refused" not in guide
    proposer = _words(verification_proposer.__doc__)
    assert ("A proposed conftest, runner configuration or plain plugin declaration (pytest_plugins) is refused; a "
            "check that builds one at run time is not detected; either way, a proposed check never passes a "
            "requirement") in proposer


def test_the_docs_say_the_proposers_limits_hold_for_every_request_the_provider_receives():
    from g6.harness import verification_proposer

    assert ("Each holds for every request the provider receives: G6 counts and caps the provider itself, never a "
            "retry around it.") in _read("docs/AI_FOR_AI_WORKFLOW.md")
    assert '"max_context_chars" per call, each holding for every request the provider receives' in _read(
        "src/g6/mcp/__init__.py")
    assert "so each holds for every request the provider receives" in _words(verification_proposer.__doc__)


def test_the_docs_say_a_model_proposed_instance_gives_at_most_insufficient_evidence_whatever_contract_it_names():
    guide = _read("docs/AI_FOR_AI_WORKFLOW.md")
    assert "(its bindings' provenance is model_hypothesis), whatever contract it names" in guide
    assert "What it proposes gives at most insufficient_evidence, with its reproduction." in guide


def test_the_readme_says_the_reproduction_is_the_repositorys_own_command_without_g6s_boundary():
    readme = _read("README.md")
    assert ("a reproduction you can run with plain pytest: the repository's own command, without G6's boundary "
            "(with PYTHONPATH=src where G6 put src on the import path)") in readme
    assert "the way G6 ran it" not in readme
