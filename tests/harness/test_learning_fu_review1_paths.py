"""wire-learn-fu review 1, finding 1: every MCP tool that takes a repository or folder refuses a network or device path
on its words before any file system call. The repository tools behind ``repo_tests.checked_repo`` are tested in
``test_learning_review4``; the workflow tools share ``workflow._project``; the self-improvement tools are below."""
import os

import pytest
from pathlib import Path

from g6.core.storage import _elsewhere
from g6.mcp import workflow

ELSEWHERE = [r"\\review-host.invalid\share\repo", "//review-host.invalid/share/repo", r"\\?\C:\repo",
             r"\\.\pipe\repo", "\\??\\C:\\repo"] if os.name == "nt" else ["//review-host.invalid/share/repo"]


def _no_filesystem(monkeypatch):
    """Any file system method asked about a network or device path fails the test; any other path is served."""
    touched = []
    for name in ("is_dir", "exists", "resolve", "stat", "is_file", "lstat", "iterdir", "mkdir", "open"):
        original = getattr(type(Path()), name)

        def probe(path, *args, _original=original, _name=name, **kwargs):
            if _elsewhere(str(path)) or _elsewhere(os.fspath(path).replace("\\", "/")):
                touched.append((_name, str(path)))
                raise AssertionError(f"a network or device path reached the file system: {_name} {path}")
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(type(Path()), name, probe)
    return touched


@pytest.mark.parametrize("repo", ELSEWHERE)
def test_every_workflow_tool_s_project_refuses_before_filesystem(monkeypatch, repo):
    # export, correct, delete, suggestion_decide and feedback all find their project through _project first.
    touched = _no_filesystem(monkeypatch)
    assert workflow._project(repo) is None and not touched


@pytest.mark.parametrize("repo", ELSEWHERE)
@pytest.mark.parametrize("tool", ["start", "enrol", "status"])
def test_the_self_improvement_tools_refuse_a_network_or_device_folder_before_filesystem(monkeypatch, repo, tool):
    from g6.mcp import improve

    touched = _no_filesystem(monkeypatch)
    reply = {"start": lambda: improve.asked(repo), "enrol": lambda: improve.asked_act("enrol", repo),
             "status": lambda: improve.status(repo)}[tool]()
    assert isinstance(reply, dict) and reply["completion_state"] == "blocked-escalated", reply
    assert improve.NOT_A_FOLDER in str(reply), reply
    assert not touched

@pytest.mark.parametrize("repo", [
    r"\\review-host.invalid\share\repo",
    "//review-host.invalid/share/repo",
    r"\\?\C:\repo",
    r"\\.\pipe\repo",
], ids=["unc", "slash-unc", "extended", "device"])
def test_workflow_export_refuses_before_filesystem(monkeypatch, repo):
    assert _elsewhere(repo)
    touched = []
    original = type(Path()).is_dir
    def is_dir(path):
        if str(path) == str(Path(repo)):
            touched.append(str(path))
            return False
        return original(path)
    monkeypatch.setattr(type(Path()), "is_dir", is_dir)
    reply = workflow.export(repo)
    assert reply["ok"] is False
    assert "G6_E_INPUT_VALIDATION" in str(reply)
    assert touched == [], f"network/device path probed before refusal: {touched}"
