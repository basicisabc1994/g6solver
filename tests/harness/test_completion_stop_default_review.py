# SPDX-License-Identifier: Apache-2.0
"""The owner's ruling R1 (2026-11-23): with no reviewer approved in the person's key file, the Stop hook runs G6's
model-free patch review (``completion_loop._final_review``) of the change and shows its report, with how long it took and
how to turn the reviewer loop on. It calls no model and never blocks the turn. These go through the real hook process
entry (``completion_hook.main``) on a real repository; tests/harness/test_stop_default_review.py (stop-exact-review
38628f4c, ported as it is) holds the same at ``advance``.

Every model caller G6 has is replaced by one that fails the test, and G6_CLAUDE and G6_CODEX name a program that does not
exist (tests/conftest.py), so nothing here can reach a model."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core import consent_callers, host_models, llm
from g6.harness import completion_hook as hook
from g6.harness import completion_loop as loop
from g6.harness import verification_proposer as proposer

REPORT = "THE MODEL-FREE REVIEW: changed-module tests ran; the test-weakening scan ran."


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    (tmp_path / "g6").mkdir()
    (tmp_path / "g6" / ".env").write_text("G6_HINTS=off\n", encoding="utf-8")    # no reviewer line: the default
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def no_model(monkeypatch):
    """Every way G6 builds or calls a model fails the test."""
    called: list[str] = []

    def forbidden(name):
        def fail(*args, **kwargs):
            called.append(name)
            raise AssertionError(f"the default Stop review reached a model: {name}")
        return fail

    for module, names in ((loop, ("provider_from_env", "ToolSession", "_start_model_call")),
                          (llm, ("provider_from_env", "_post_json", "_start_model_call")),
                          (consent_callers, ("provider_from_env",)),
                          (host_models, ("CodexCaller", "ClaudeCodeCaller", "_run", "_start_model_call"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden(f"{module.__name__}.{name}"))
    return called


@pytest.fixture
def reviewed(monkeypatch):
    """The model-free review, standing in for ``patch_review.review_patch`` (its own tests are its own), and a boundary."""
    calls: list[tuple[Path, dict]] = []

    def review(root, **kwargs):
        calls.append((Path(root), kwargs))
        return {"assessment": {"report": REPORT}}

    monkeypatch.setattr(loop, "review_patch", review)
    monkeypatch.setattr(proposer, "boundary", lambda root: "")
    return calls


def hooked(monkeypatch, capsys, name: str, event: dict) -> tuple[int, str, str]:
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(event).encode("utf-8")), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    code = hook.main([name])
    out, err = capsys.readouterr()
    return code, out, err


def prompt(repo: Path, text: str = "fix the parser") -> dict:
    return {"session_id": "s1", "cwd": str(repo), "prompt": text, "hook_event_name": "UserPromptSubmit"}


def stop(repo: Path) -> dict:
    return {"session_id": "s1", "cwd": str(repo), "hook_event_name": "Stop", "stop_hook_active": False}


def edit(repo: Path, body: str = "def value():\n    return 2\n") -> None:
    (repo / "pkg" / "mod.py").write_text(body, encoding="utf-8")


def test_a_default_stop_runs_the_model_free_review_and_shows_its_report(repo, no_model, reviewed, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    edit(repo)
    code, out, _ = hooked(monkeypatch, capsys, "stop", stop(repo))
    said = json.loads(out)
    assert code == 0 and set(said) == {"systemMessage"}          # shown to the person; no "decision": "block"
    message = said["systemMessage"]
    assert message.startswith(REPORT)
    assert "model-free" in message and "seconds" in message and "untested" in message
    assert f"{loop.CRITIC_LINE}=codex" in message and "Nothing was tested" not in message
    assert reviewed == [(repo.resolve(), {"task": "fix the parser", "base": git(repo, "rev-parse", "HEAD"),
                                          "requirements": [{"id": "task", "text": "fix the parser", "checks": []}],
                                          "timeout_s": loop.REVIEW_S})]
    assert no_model == []


def test_a_default_stop_reviews_each_patch_once(repo, no_model, reviewed, monkeypatch, capsys):
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    edit(repo)
    hooked(monkeypatch, capsys, "stop", stop(repo))
    again = json.loads(hooked(monkeypatch, capsys, "stop", stop(repo))[1])
    assert set(again) == {"systemMessage"} and len(reviewed) == 1          # the same patch: its report stands
    hooked(monkeypatch, capsys, "prompt", prompt(repo, "thanks"))
    thanks = json.loads(hooked(monkeypatch, capsys, "stop", stop(repo))[1])
    assert thanks == {"systemMessage": loop.ALREADY_READ} and len(reviewed) == 1
    edit(repo, "def value():\n    return 3\n")
    code, out, _ = hooked(monkeypatch, capsys, "stop", stop(repo))
    assert code == 0 and set(json.loads(out)) == {"systemMessage"} and len(reviewed) == 2     # a new patch is read
    assert no_model == []


@pytest.mark.parametrize("line", ["", "G6_LLM_PROVIDER=openai\n", "G6_LOOP_MODE=scouts\n"])
def test_an_agents_environment_or_another_provider_approves_no_model(repo, no_model, reviewed, monkeypatch, capsys,
                                                                     line):
    (repo.parent / "g6" / ".env").write_text("G6_HINTS=off\n" + line, encoding="utf-8")
    monkeypatch.setenv("G6_LLM_PROVIDER", "claude_code")          # an agent's environment is not the person's approval
    monkeypatch.setenv(loop.CRITIC_LINE, "codex")
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    edit(repo)
    code, out, _ = hooked(monkeypatch, capsys, "stop", stop(repo))
    said = json.loads(out)
    assert code == 0 and set(said) == {"systemMessage"} and len(reviewed) == 1
    assert ("G6_LLM_PROVIDER=claude_code" if "scouts" in line else f"{loop.CRITIC_LINE}=codex") in said["systemMessage"]
    assert no_model == []


def test_a_review_that_fails_never_holds_the_turn(repo, no_model, reviewed, monkeypatch, capsys):
    def broken(root, **kwargs):
        raise RuntimeError("the review broke")

    monkeypatch.setattr(loop, "review_patch", broken)
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    edit(repo)
    code, out, err = hooked(monkeypatch, capsys, "stop", stop(repo))
    assert (code, out) == (1, "") and "the review broke" in err      # any exit but 2 ends the turn
    assert no_model == []


def test_the_default_review_keeps_the_boundary_rule(repo, no_model, reviewed, monkeypatch, capsys):
    monkeypatch.setattr(proposer, "boundary", lambda root: "no backend here")
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    edit(repo)
    said = json.loads(hooked(monkeypatch, capsys, "stop", stop(repo))[1])
    assert set(said) == {"systemMessage"} and "boundary" in said["systemMessage"] and reviewed == []


def test_the_real_model_free_review_calls_no_model_and_never_blocks(repo, no_model, monkeypatch, capsys):
    """``patch_review.review_patch`` itself, where this machine has an operating-system boundary for it."""
    if why := proposer.boundary(repo):
        pytest.skip(f"no operating-system boundary here: {why}")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_mod.py").write_text("from pkg.mod import value\n\n\ndef test_value():\n    assert value() == 1\n",
                                                encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "a test")
    hooked(monkeypatch, capsys, "prompt", prompt(repo))
    edit(repo)
    code, out, _ = hooked(monkeypatch, capsys, "stop", stop(repo))
    said = json.loads(out)
    assert code == 0 and set(said) == {"systemMessage"} and "model-free" in said["systemMessage"]
    assert no_model == []
