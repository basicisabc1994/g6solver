"""Trusted local fixtures; no model, network or real confinement executor."""
import asyncio
import subprocess
import sys
from types import SimpleNamespace

from g6.harness import behaviour_question as bq
from g6.harness import patch_review
from g6.harness import verification_templates as vt
from g6.harness.confirmed_expectation import ExpectationStandard, confirm
from g6.mcp import behaviour

TASK = "Repair the calculation."
NODE = "test_calc.py::test_add"
REQUIREMENTS = [{"id": "R1", "text": "calculation", "checks": [NODE]}]


def repo(parent, *, extra=None):
    root = parent / "repo"
    root.mkdir()
    files = {"calc.py": "def add(a, b):\n    return a - b\n",
             "test_calc.py": "from calc import add\n\ndef test_add():\n    assert isinstance(add(2, 3), int)\n",
             **(extra or {})}
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in [("init", "-q"), ("add", "."),
                 ("-c", "user.name=Review", "-c", "user.email=review@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-qm", "base")]:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=20)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return root


def candidate(**extra):
    return {"requirement_id": "R1", "function": "calc.add", "args": [2, 3],
            "outcomes": {"A": {"returns": 5}, "B": {"returns": -1}},
            "cited": TASK, "evidence": ["calc.py: add"], **extra}


class Dialog:
    def __init__(self, content=None, action="accept"):
        self.session = self
        self.request_id = "review-fixture"
        self.content = content or {"choice": "A", "name": "Maintainer"}
        self.action = action
        self.messages = []

    async def elicit_form(self, **kwargs):
        self.messages.append(kwargs["message"])
        return SimpleNamespace(action=self.action, content=self.content)


def ask(root, *, dialog=None, data=None):
    return asyncio.run(behaviour.confirm(dialog or Dialog(), str(root), TASK,
                                        candidates=[candidate()] if data is None else data))


def standard():
    one = bq.caller_candidates(TASK, [candidate()])[0]
    return ExpectationStandard().with_expectation(confirm(one.question("b" * 64), "A",
                                                          answered_by="person:Maintainer"))


def result(node, status="passed"):
    outcomes = {node: {"setup": "passed", "call": status, "teardown": "passed"}}
    return {"verdict": status, "said": "", "passed": int(status == "passed"),
            "failed": int(status == "failed"), "not_passed": 0, "total": 1, "deselected": 0,
            "test_outcomes": outcomes, "collected_ids": [node], "omitted_ids": [], "changed": [],
            "output": "", "seconds": 0.01, "python": sys.executable, "interpreter_source": "g6",
            "isolation": {"backend": "trusted-review-fixture", "why": ""},
            "failed_ids": [node] if status == "failed" else [], "setup_errors": {}, "degraded": False,
            "receipt": "", "failure_messages": {node: "E   Failed: expected 5, got -1"}
            if status == "failed" else {}}


def stated(monkeypatch):
    monkeypatch.setattr(patch_review, "_execute", lambda *a, **k: result(NODE))
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda *a, **k: result(NODE, "failed"))


def namespace(path, instance):
    tree = vt.Tree(path, SimpleNamespace(base_commit="fixture"), frozenset({"calc.py"}), frozenset(),
                   _texts={("calc.py", vt.PATCH): "def add(*args, **kwargs):\n    return None\n"})
    definition = vt.TEMPLATES[instance.template]
    applied = definition.applies(tree, definition.bindings.model_validate(instance.bindings), vt.PATCH)
    found = {}
    exec(compile(vt.compile_text(definition, applied.data), "<G6 fixed template>", "exec"), found)
    return found
