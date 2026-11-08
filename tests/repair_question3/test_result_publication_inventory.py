"""Inventory the result, publication, persisted-template and exact-rule boundaries."""
import ast
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from g6.harness import behaviour_question as bq, verification_templates as vt
from g6.mcp import behaviour
from tests.repair_question2.helpers import TASK, candidate


@pytest.mark.parametrize("rule", ['prefix `a  b`', 'first\n  second\tthird', 'spaces  at  end  '])
def test_every_rule_rendering_is_exact(rule):
    one = bq.caller_candidates(TASK, [candidate(rule=rule)])[0]
    case, stated = one.questions("b" * 64)
    message = bq.form(case, one, stated)[0]
    assert "Rule offered, in full: " + rule in message
    scoped = bq.form(stated, one, stated)[0]
    assert stated.scope in scoped


def test_report_keeps_the_exact_rule_including_newlines_and_long_literals():
    from g6.harness import patch_report

    rule = 'prefix `a  b`\n  suffix ' + 'x' * 200
    one = bq.caller_candidates(TASK, [candidate(rule=rule)])[0]
    _case, stated = one.questions("b" * 64)
    report = patch_report._confirmed({"confirmed_expectations": {"expectations": [
        {"question": stated.to_dict(), "answer": {"option": "A"}, "check": {}}]}})
    assert stated.scope in "\n".join(line for item in report for line in item)


def test_every_unconfirmed_template_identity_equals_the_base_producer(monkeypatch):
    source = subprocess.run(["git", "show", "e47dc96:src/g6/harness/verification_templates.py"],
                            check=True, capture_output=True).stdout.decode("utf-8")
    base = ModuleType("g6.harness._repair3_base_templates")
    monkeypatch.setitem(sys.modules, base.__name__, base)
    exec(compile(source, "<base template producer>", "exec"), base.__dict__)
    for ident, instance in vt.EXAMPLES.items():
        one = vt.TemplateInstance(**instance)
        old = base.TemplateInstance(**instance)
        assert vt.instance_digest(one) == base.instance_digest(old), ident
    # The inventory above comes from the actual registry, including the static and formal templates.
    assert set(vt.EXAMPLES) == set(vt.REGISTRY)


def test_the_public_ordinary_template_is_the_text_that_is_compiled(tmp_path):
    one = vt.TemplateInstance(**vt.EXAMPLES["example.v1"])
    data = {"function": ["module", "calc", "add", "calc.py"], "cases": [
        {"args": [2, 3], "kwargs": {}, "expect": "returns", "value": 5}]}
    check, _placed, _origin = vt.compiled_check(tmp_path, one, data, [])
    node = next(n for n in ast.parse(check.source).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "BINDINGS" for t in n.targets))
    literal = ast.get_source_segment(check.source, node.value)
    assert check.source.replace(literal, "") == vt.TEMPLATES["example.v1"].text.replace(vt.SLOT, "")


def test_confirmed_fixed_text_keeps_its_previous_digest():
    from g6.harness.confirmed_expectation import confirm
    import hashlib

    source = subprocess.run(["git", "show", "d6e37be:src/g6/harness/verification_templates.py"],
                            check=True, capture_output=True).stdout.decode("utf-8")
    fixed = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(source).body
             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id in {"_HEADER", "_COMMON", "_EXAMPLE"}}
    previous = (fixed["_HEADER"] + "\nTEMPLATE = 'example.v1'\n" + fixed["_COMMON"]
                + "\n\nBINDINGS = " + vt.SLOT + "\n" + fixed["_EXAMPLE"])
    one = bq.caller_candidates(TASK, [candidate()])[0]
    held = confirm(one.question("b" * 64), "A", answered_by="person:Maintainer")
    assert vt.instance_definition(held.instance).text_sha256 == hashlib.sha256(previous.encode()).hexdigest()


def test_every_review_branch_passes_one_publication_boundary():
    tree = ast.parse(Path(behaviour.__file__).read_text(encoding="utf-8"))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reviewed")
    returns = [ast.unparse(n.value) for n in ast.walk(run) if isinstance(n, ast.Return)]
    assert sum(text.startswith("bound(") for text in returns) == 1
    assert "guard = store.counting" in ast.unparse(run)
    assert "with guard(admitted):" in ast.unparse(run)


def test_every_confirmed_run_summary_keeps_its_availability():
    from g6.harness import confirmed_checks as cc
    source = Path(cc.__file__).read_text(encoding="utf-8")
    run = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "run")
    normalizers = [n for n in ast.walk(run) if isinstance(n, ast.Call)
                   and ast.unparse(n.func) == "normal_reading"]
    assert len(normalizers) == 1
    for result in ("passed", "failed"):
        raw = {"result": result, "verdict": "collection_error", "collection": True, "detail": "unavailable"}
        assert cc.normal_reading(raw)["result"] == "error"
    assert cc.normal_reading({"result": "failed", "verdict": "failed"})["result"] == "failed"


def test_a_standard_created_after_empty_admission_refuses_publication(tmp_path, monkeypatch):
    from g6.harness import patch_review, question_store, standard_publication
    from tests.repair_question2.helpers import NODE, REQUIREMENTS, ask, repo, result

    root = repo(tmp_path)
    store = question_store.TaskQuestions(question_store.task_identity(root, TASK))
    assert not store.exists()

    def execute(*args, **kwargs):
        if not store.exists():
            assert ask(root)["status"] == "confirmed"
        return result(NODE)

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda *args, **kwargs: result(NODE, "failed"))
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assert reply.get("ok") is False and "assessment" not in reply
    assert reply["warning_card"]["code"] == standard_publication.CODE
