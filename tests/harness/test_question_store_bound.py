# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A task's question store refuses damage and stays with its task (question slice, repair 1; review 1's findings 2, 7
and 3, one root: a store that can be emptied, rolled back or moved).

- Every entry names the task's identity; an entry read under another identity is refused, so a ledger copied to
  another task's or repository's name enforces nothing there.
- The chain's head is kept beside the store after each write and checked on every open: an emptied file, an earlier
  valid prefix, a head with no store, or anything but a regular file in either place reads as unreadable, never empty.
  A stop between a write and its head is the one gap taken, and the head catches up.
- A budget setting that is blank allows no question.
- An inventory read from the code: every read of a store entry, and of a saved review, checks its binding.

No real dialog: the client is a fake. Development fixtures only.
"""
from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger
from g6.harness import question_store as qs
from g6.mcp import behaviour

ROOT = Path(__file__).resolve().parents[2]
TASK = "Repair addition for positive integers."


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    return tmp_path / "home"


def candidate(**extra) -> dict:
    return {"requirement_id": "R1", "function": "calc.add", "args": [2, 3],
            "outcomes": {"A": {"returns": 5}, "B": {"returns": -1}}, "cited": TASK, "evidence": ["calc.py: add"],
            **extra}


def make_repo(parent: Path) -> Path:
    root = parent / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                                "-c", "commit.gpgsign=false", "commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=20)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return root


class Dialog:
    """A client whose dialog returns what it is given, and records each form it was shown."""

    def __init__(self, content=None, action="accept"):
        self.session, self.request_id, self.messages = self, "fixture", []
        self.content = content if content is not None else {"choice": "A", "name": "Ada"}
        self.action = action

    async def elicit_form(self, **kwargs):
        self.messages.append(kwargs["message"])
        return SimpleNamespace(action=self.action, content=self.content)


def ask(root: Path, *, data=None, dialog=None, task=TASK, **kwargs) -> dict:
    return asyncio.run(behaviour.confirm(dialog or Dialog(), str(root), task,
                                         candidates=[candidate()] if data is None else data, **kwargs))


# --- review 1's findings, brought in as this change's own ---------------------------------------------------------


@pytest.mark.parametrize("damage", ["empty", "valid_prefix", "directory",
                                    # members the old pattern missed: the head's place, and the store gone
                                    "head_directory", "store_removed", "head_removed_with_a_longer_chain"])
def test_finding_2_a_store_emptied_cut_back_or_replaced_is_unreadable_never_empty(tmp_path, damage):
    root = make_repo(tmp_path)
    first = ask(root, dialog=Dialog({"choice": "A", "name": "Ada", "further": "one_more"}))
    assert first.get("status") == "confirmed", first
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    assert len(store.state().standard().expectations) == 1
    old_prefix = store.path.read_bytes()
    (root / "calc.py").write_text((root / "calc.py").read_text() + "\n", encoding="utf-8")
    assert ask(root, dialog=Dialog({"choice": "B", "name": "Ada"}))["status"] == "confirmed"
    assert store.state().standard().expectations[0].option == "B"
    if damage == "directory":
        store.path.unlink()
        store.path.mkdir()
    elif damage == "head_directory":
        store.head.unlink()
        store.head.mkdir()
    elif damage == "store_removed":
        store.path.unlink()
    elif damage == "head_removed_with_a_longer_chain":
        store.head.unlink()
    else:
        store.path.write_bytes(b"" if damage == "empty" else old_prefix)
    with pytest.raises(qs.StoreUnreadable):
        behaviour.enforced(str(root), TASK)
    with pytest.raises(qs.StoreUnreadable):
        store.state()


@pytest.mark.parametrize("other", ["task", "repository", "task_with_its_head"])
def test_finding_7_a_valid_store_copied_from_another_task_cannot_confirm_this_task(tmp_path, other):
    root = make_repo(tmp_path)
    assert ask(root)["status"] == "confirmed"
    source = qs.TaskQuestions(qs.task_identity(root, TASK))
    other_task = TASK if other == "repository" else "A different task where nobody has answered a question."
    if other == "repository":
        parent = tmp_path / "other"
        parent.mkdir()
        root = make_repo(parent)
    destination = qs.TaskQuestions(qs.task_identity(root, other_task))
    destination.path.parent.mkdir(parents=True, exist_ok=True)
    destination.path.write_bytes(source.path.read_bytes())
    if other == "task_with_its_head":
        destination.head.write_bytes(source.head.read_bytes())
    try:
        enforced = behaviour.enforced(str(root), other_task)
    except qs.StoreUnreadable as exc:
        assert "bound to another task" in str(exc) or "head" in str(exc)
        return
    assert enforced is None, "another task's answer became normative without a dialog for this task"


@pytest.mark.parametrize("raw", ["", " ", "\t\n"])
def test_finding_3_a_set_but_blank_budget_allows_no_question(tmp_path, monkeypatch, raw):
    root = make_repo(tmp_path)
    monkeypatch.setenv(qs.BUDGET_ENV, raw)
    dialog = Dialog()
    reply = ask(root, data=[candidate()], dialog=dialog)
    assert reply["status"] == "budget_spent", "an unreadable configured budget opened a dialog"
    assert dialog.messages == [] and qs.BUDGET_ENV in reply["budget"]["note"]


# --- the head, and a stop between a write and it ------------------------------------------------------------------


def _store(task: str = TASK) -> qs.TaskQuestions:
    return qs.TaskQuestions(qs.task_identity(Path("/repo"), task))


def _ask(where: qs.TaskQuestions, args=(2, 3)):
    from g6.harness import behaviour_question as bq

    [one] = bq.caller_candidates(TASK, [candidate(args=list(args))])
    case, rule = one.questions("b" * 64)
    return where.ask(case, rule, one, at="2026-11-07T10:00:00Z")


class Stop(Exception):
    pass


@pytest.mark.parametrize("where", ["after_the_entry", "with_the_head_half_written"])
def test_a_stop_between_a_write_and_its_head_is_taken_and_the_head_catches_up(monkeypatch, where):
    store = _store()
    _ask(store)
    kept = store.head.read_text(encoding="ascii")
    real = qs.TaskQuestions._keep

    def stopped(self, head):
        if where == "with_the_head_half_written":
            self.head.with_name(self.head.name + ".part").write_text(head[:10], encoding="ascii")
        raise Stop(head)

    asked = store.state().asked[0]
    monkeypatch.setattr(qs.TaskQuestions, "_keep", stopped)
    with pytest.raises(Stop):   # the entry is written, then the stop, before its head
        store.allow_more(asked, by="person:Ada", at="2026-11-07T10:00:00Z")
    monkeypatch.setattr(qs.TaskQuestions, "_keep", real)
    assert store.head.read_text(encoding="ascii") == kept   # the head did not move
    state = qs.TaskQuestions(store.identity).state()   # after a restart: one entry past the head, from it, is taken
    assert state.more == 1 and store.head.read_text(encoding="ascii").strip() == ChainLedger(store.path).head()
    assert qs.TaskQuestions(store.identity).state().more == 1   # and reads the same again


def test_two_entries_past_the_head_are_refused():
    store = _store()
    asked = _ask(store)
    kept = store.head.read_bytes()
    store.allow_more(asked, by="person:Ada", at="2026-11-07T10:00:00Z")
    store.allow_more(asked, by="person:Ada", at="2026-11-07T10:00:00Z")
    store.head.write_bytes(kept)   # rolled back two writes
    with pytest.raises(qs.StoreUnreadable, match="head"):
        store.state()


def test_a_store_with_no_head_and_one_entry_is_a_first_write_stopped_before_its_head():
    store = _store()
    _ask(store)
    store.head.unlink()
    assert len(store.state().asked) == 1 and store.head.exists()


def test_an_entry_with_no_task_or_another_task_is_refused_on_read():
    store = _store()
    _ask(store)
    ledger = ChainLedger(store.path)   # no rules: as an edit would write it
    ledger.append(qs.MORE, "f" * 64, {"question_sha256": store.state().asked[0].question.sha256, "by": "person:Ada",
                                      "at": "2026-11-07T10:00:00Z", "task": "0" * 64})
    store._keep(ledger.head())
    with pytest.raises(qs.StoreUnreadable, match="bound to another task"):
        store.state()


# --- inventory: every read of a store entry, or of a saved review, checks its binding -------------------------------


def _calls(rel: str) -> list[tuple[str, str]]:
    """(function, call) for each call in ``rel`` that opens a ledger, reads its entries, or reads a saved review."""
    tree = ast.parse((ROOT / "src" / "g6" / rel).read_text(encoding="utf-8"))
    found = []

    def visit(node: ast.AST, where: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            where = node.name
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in ("ChainLedger", "entries", "entry", "get_entry", "_open", "_read") or (
                    name == "state_path" and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ("reviews", "questions")):
                found.append((where, ast.unparse(node)[:100]))
        for child in ast.iter_child_nodes(node):
            visit(child, where)

    visit(tree, "<module>")
    return sorted(set(found))


#: Each such call, and how its binding is checked.
READS = {
    "harness/question_store.py": [
        ("__init__", "state_path('questions', f'{identity}.head')"),
        ("__init__", "state_path('questions', f'{identity}.jsonl')"),
        ("_open", "ChainLedger(self.path, validate=self._validate)"),
        ("_open", "ledger.entries()"),
        ("_read", "ledger.entries()"),
        ("allow_more", "ledger.entries()"), ("allow_more", "self._open()"),
        ("answer", "self._open()"), ("answer", "self._read(ledger, default, note)"),
        ("ask", "self._open()"), ("ask", "self._read(ledger, default, note)"),
        ("counting", "self._open()"), ("counting", "self._read(self._open(), default, note)"),
        ("show", "ledger.entries()"), ("show", "self._open()"),
        ("state", "self._open()"), ("state", "self._read(self._open(), default, note)"),
    ],
    "mcp/behaviour.py": [("_review_candidates", "state_path('reviews', f'{review_job}.json')")],
}


def test_every_read_of_a_store_entry_or_a_saved_review_checks_its_binding():
    """Read from the code: the store opens its ledger in one place, with its rules, which end with the entry's binding
    to this task (``_rules``), and checked against its head; every read of an entry is of a ledger that place
    returned. The one read of a saved review checks the task it is bound to and its artefact digest."""
    found = {rel: _calls(rel) for rel in READS}
    assert found == {rel: sorted(calls) for rel, calls in READS.items()}
    store = ast.parse((ROOT / "src" / "g6" / "harness" / "question_store.py").read_text(encoding="utf-8"))
    ledgers = [n for n in ast.walk(store) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ChainLedger"]
    assert [ast.unparse(n) for n in ledgers] == ["ChainLedger(self.path, validate=self._validate)"]
    rules = next(n for n in ast.walk(store) if isinstance(n, ast.FunctionDef) and n.name == "_rules")
    assert "body.get('task') != identity" in ast.unparse(rules)
    validate = next(n for n in ast.walk(store) if isinstance(n, ast.FunctionDef) and n.name == "_validate")
    assert ast.unparse(validate.body[-1]) == "return _rules(kind, ident, body, seen, self.identity)"
    # every ledger an entry is read from is one _open returned: the methods that read take it from there
    for method in ("state", "ask", "show", "answer", "allow_more", "counting"):
        body = next(n for n in ast.walk(store) if isinstance(n, ast.FunctionDef) and n.name == method)
        assert "self._open()" in ast.unparse(body), method
    for name, rel in (("_review_candidates", "mcp/behaviour.py"),):
        tree = ast.parse((ROOT / "src" / "g6" / rel).read_text(encoding="utf-8"))
        body = ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name))
        assert "saved.get('task_identity') != identity" in body and "saved.get('artifact_digest') != artifact" in body
    # nowhere else in the product reads a saved review or a question store's file
    elsewhere = []
    for path in sorted((ROOT / "src" / "g6").rglob("*.py")):
        rel = path.relative_to(ROOT / "src" / "g6").as_posix()
        if rel in READS:
            continue
        text = path.read_text(encoding="utf-8")
        if "state_path(\"questions\"" in text or "state_path('questions'" in text:
            elsewhere.append(rel)
        if ("state_path(\"reviews\"" in text or "state_path('reviews'" in text) and rel != "mcp/review_reply.py":
            elsewhere.append(rel)
    assert elsewhere == []   # review_reply only writes a review (save)
