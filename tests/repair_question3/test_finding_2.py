"""Ordinary template reviews must retain their persisted fields off the confirmed path."""
import subprocess
import sys
import json
from types import ModuleType
from pathlib import Path

from g6.harness import patch_review
from g6.harness import verification_search as vs
from g6.mcp import behaviour, review_reply
from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, result, stated


def test_no_confirmed_expectation_keeps_base_template_review_fields(tmp_path, monkeypatch):
    checkout = Path(__file__).resolve().parents[2]
    source = subprocess.run(
        ["git", "show", "e47dc96:src/g6/harness/verification_templates.py"],
        cwd=checkout, check=True, capture_output=True, timeout=20,
    ).stdout.decode("utf-8")
    # Read the producer at the specified base, without changing the repository's refs.
    base = ModuleType("g6.harness._review_base_templates")
    monkeypatch.setitem(sys.modules, base.__name__, base)
    exec(compile(source, "<e47dc96 verification_templates>", "exec"), base.__dict__)

    root = repo(tmp_path)
    stated(monkeypatch)
    monkeypatch.setattr(vs, "_run_check", lambda change, placed, source, node, **kw:
                        result(node + "::test_relation"))
    instance = {"id": "ordinary", "template": "relation.v1", "requirement_id": "R1", "intent": "change",
                "bindings": {"function": "calc.add", "relation": "idempotence", "inputs": [2]}}
    # add takes two arguments; use a separate unary callable for the ordinary relation.
    instance["bindings"]["function"] = "calc.identity"
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n\ndef identity(value):\n    return value\n",
                                  encoding="utf-8")
    assert behaviour.enforced(str(root), TASK) is None
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK,
                               requirements=REQUIREMENTS, template_instances=[instance])
    assert "confirmed_expectations" not in reply["assessment"]
    saved = review_reply.save(reply, "e" * 32)
    assert saved is not None
    persisted = json.loads(Path(saved).read_bytes())
    activity = next(a for a in persisted["assessment"]["completion"]["activities"] if a.get("check") == "ordinary")
    assert activity["result"] == "not_refuted", activity
    before = base.instance_digest(base.TemplateInstance(**instance))
    assert activity["instance_sha256"] == before, (
        "a persisted template-instance field changed from e47dc96 with nothing confirmed")
