"""Folder admission belongs after expansion and before filesystem access."""
import ast
import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_hook
from g6.mcp import behaviour, improve, knowledge, learning_history, mission, review_loop, workflow
from g6.workforce import repo_tests


REFUSED = [
    r"\\review-host.invalid\share\repo", r"\\?\C:\repo", r"\\.\pipe\repo", r"\??\C:\repo",
    r"C:\work\NUL\repo", r"C:\work\CON.txt\repo", r"C:\work\COM1\repo",
    r"C:\work\LPT¹\repo", r"C:\work\name.\repo", "C:\\work\\name \\repo",
    r"C:\work\repo:stream", r"C:\work\repo::$INDEX_ALLOCATION",
]


def test_mcp_request_folder_transform_census():
    root = Path(__file__).parents[2]
    files = subprocess.run(["git", "ls-files", "src/g6/mcp/*.py"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    # Fixed server/module and inherited environment paths are not request fields.
    internal = {("improve.py", "_environment_path"), ("improve.py", "_import_path"),
                ("registrations.py", "package_root"), ("catalogue.py", "<module>"),
                ("behaviour.py", "confirm")}
    bad = []
    for rel in files:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        imported = {alias.asname or alias.name: alias.name for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) for alias in node.names}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = (node.func.attr if isinstance(node.func, ast.Attribute) else
                      imported.get(node.func.id, node.func.id) if isinstance(node.func, ast.Name) else "")
            if called not in {"expanduser", "expandvars", "resolve"}:
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            name = owner.name if owner else "<module>"
            if (Path(rel).name, name) not in internal:
                bad.append((rel, name, node.lineno))
    assert not bad, bad


@pytest.mark.skipif(os.name != "nt", reason="Windows folder grammar")
@pytest.mark.parametrize("operation", [
    "start", "enrol", "resume", "demote", "status", "review", "stop",
    "workflow-export", "workflow-correct", "workflow-delete", "workflow-decide", "workflow-feedback",
    "behaviour", "mission-premortem", "mission-execution", "mission-submit", "history",
])
@pytest.mark.parametrize("expanded", [False, True], ids=["literal", "environment"])
@pytest.mark.parametrize("raw", REFUSED)
def test_entries_refuse_final_folder_before_filesystem(monkeypatch, operation, expanded, raw):
    # Load the act module before instrumenting filesystem entry points.
    from g6.metaharness import runner  # noqa: F401
    from g6.harness.workflow import feedback, suggest  # noqa: F401
    from g6.self_maintenance.envelope import _STOP_PENDING

    pending = dict(_STOP_PENDING)
    monkeypatch.setattr(mission.api, "bind", lambda: SimpleNamespace(runtime=SimpleNamespace(
        confirmation_pending=lambda ident: False, awaiting=lambda ident: [SimpleNamespace(key="patch")])) )
    monkeypatch.setattr(mission.coding, "frozen", lambda *args: {"baseline_revision": "HEAD"})

    monkeypatch.setenv("G6_TEST_FOLDER", raw)
    given = "%G6_TEST_FOLDER%" if expanded else raw
    touched = []

    def probe(path, *args, **kwargs):
        touched.append(str(path))
        raise AssertionError("filesystem reached before admission")

    with monkeypatch.context() as guard:
        for name in ("resolve", "is_file", "is_dir", "exists", "stat", "lstat", "open", "mkdir",
                     "read_text", "read_bytes", "write_text", "write_bytes", "iterdir"):
            guard.setattr(type(Path()), name, probe)
        guard.setattr(completion_hook, "folder", probe)
        if operation == "review":
            reply = review_loop.step(given, "Keep behavior", "admission-fixture")
            code = "G6_E_INPUT_VALIDATION"
        elif operation == "start":
            reply, code = improve.asked(given), improve.NOT_A_FOLDER
        elif operation == "status":
            reply, code = improve.status(given), improve.NOT_A_FOLDER
        elif operation == "stop":
            # A stop reserves its process-local denial first, then admits the folder.
            reply, code = improve.stop(given), improve.STOP_FAILED
        elif operation.startswith("workflow-"):
            action = operation.removeprefix("workflow-")
            reply = {"export": lambda: workflow.export(given), "correct": lambda: workflow.correct(given),
                     "delete": lambda: asyncio.run(workflow.delete(given, None)),
                     "decide": lambda: asyncio.run(workflow.decide(given, "task", "fixture", None)),
                     "feedback": lambda: asyncio.run(workflow.feedback(given, "task", None))}[action]()
            code = "G6_E_INPUT_VALIDATION"
        elif operation == "behaviour":
            reply, code = asyncio.run(behaviour.confirm(None, given, "task")), "G6_E_INPUT_VALIDATION"
        elif operation == "mission-premortem":
            reply, code = mission.premortem(given, "task"), "G6_E_INPUT_VALIDATION"
        elif operation == "mission-execution":
            with pytest.raises(repo_tests.RepoRefused):
                mission.register(None, given)
            reply, code = {"completion_state": "blocked-escalated", "code": "G6_E_INPUT_VALIDATION"}, "G6_E_INPUT_VALIDATION"
        elif operation == "mission-submit":
            reply, code = mission.submit("fixture", given), "G6_E_INPUT_VALIDATION"
        elif operation == "history":
            with pytest.raises(repo_tests.RepoRefused):
                learning_history._read(given)
            reply, code = {"completion_state": "blocked-escalated", "code": "G6_E_INPUT_VALIDATION"}, "G6_E_INPUT_VALIDATION"
        else:
            reply, code = improve.asked_act(operation, given), improve.NOT_A_FOLDER
    for token in list(_STOP_PENDING):
        if token not in pending:
            _STOP_PENDING.pop(token)
    assert touched == [], touched
    assert reply["completion_state"] == "blocked-escalated"
    assert code in str(reply)


def test_resolved_form_is_checked_again(monkeypatch, tmp_path):
    monkeypatch.setattr(type(Path()), "resolve", lambda path: Path("//review-host.invalid/share"))
    with pytest.raises(repo_tests.RepoRefused, match="network|device"):
        repo_tests.admit_folder(str(tmp_path))


@pytest.mark.skipif(os.name != "nt", reason="Windows folder grammar")
@pytest.mark.parametrize("suffix", [".", " ", "::$INDEX_ALLOCATION"])
def test_knowledge_add_existing_admission_precedes_filesystem(monkeypatch, suffix):
    from g6._context_engine._scope import Binding

    functions = {}

    class Server:
        def tool(self):
            def keep(fn):
                functions[fn.__name__] = fn
                return fn
            return keep

    knowledge.register(Server(), object)
    root = Path.cwd()
    # Host setup is independent of the request path; use an in-memory host binding.
    monkeypatch.setattr(knowledge, "host_binding", lambda: Binding("fixture", "0" * 64, (root,), root))
    touched = []

    def probe(path, *args, **kwargs):
        touched.append(str(path))
        raise AssertionError("knowledge path reached filesystem before admission")

    with monkeypatch.context() as guard:
        for name in ("resolve", "stat", "lstat", "is_dir", "exists", "open"):
            guard.setattr(type(Path()), name, probe)
        reply = asyncio.run(functions["g6_knowledge_add"](str(root / "folder") + suffix, None))
    assert touched == [], touched
    assert reply["completion_state"] == "blocked-escalated"


def test_expansion_and_relative_folder_are_admitted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("G6_TEST_FOLDER", "ordinary")
    assert repo_tests.admit_folder("%G6_TEST_FOLDER%" if os.name == "nt" else "$G6_TEST_FOLDER") == tmp_path / "ordinary"


def test_input_refusal_has_no_repository_origin(monkeypatch):
    from g6.core import cloud_consent as cloud

    refused = {"ok": False, "error": "[G6_E_INPUT_VALIDATION] refused", "request_id": "fixture"}
    monkeypatch.setattr(review_loop, "_step", lambda *args: refused)
    with cloud.collect_sources():
        reply = review_loop.step(str(Path.cwd()), "Keep behavior", "fixture")
        assert reply == refused
        _, refs, metadata = cloud.freeze_output(reply)
        assert refs == ()
        assert metadata["error_code"] == "G6_E_INPUT_VALIDATION"


@pytest.mark.skipif(os.name != "nt", reason="Windows folder grammar")
@pytest.mark.parametrize("raw", REFUSED)
def test_repository_validation_uses_admission_before_filesystem(monkeypatch, raw):
    touched = []

    def probe(path, *args, **kwargs):
        touched.append(str(path))
        raise AssertionError("repository probed before admission")

    with monkeypatch.context() as guard:
        guard.setattr(type(Path()), "is_dir", probe)
        guard.setattr(type(Path()), "resolve", probe)
        with pytest.raises(repo_tests.RepoRefused) as refused:
            repo_tests.checked_repo(raw)
    assert refused.value.code == "G6_E_INPUT_VALIDATION"
    assert not touched
