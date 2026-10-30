# SPDX-License-Identifier: Apache-2.0
"""Where no operating-system backend confines a run, G6 says so in one set of words wherever a person meets it
(CORE_SANDBOX-01; owner ruling 2026-10-27: G6 refuses such a run unless the person allows it once; the refusal itself
is tested in tests/core/test_isolation_choice.py, and here the run is allowed)."""
from __future__ import annotations

from pathlib import Path

import pytest

import g6.mcp
from g6.core import confine, discovery, sandbox

ROOT = Path(__file__).resolve().parents[2]
OFF = "unavailable: switched off by this test"


def _words(text: str | None) -> str:
    return " ".join((text or "").split())


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead


def test_the_warning_names_the_gap_and_ends_on_the_persons_responsibility() -> None:
    # Fails if confine.UNCONFINED_WARNING loses the platforms, the refusal and how to allow it, the key file, the network
    # guard's limit or its last words.
    said = confine.UNCONFINED_WARNING
    assert said.endswith("You are responsible for what you run.")
    for words in ("macOS, Linux without bubblewrap", "Windows before the one-time access grant", "G6 refuses",
                  "allow it once with g6 setup", "~/.g6/.env", "your own rights", "inside Python only"):
        assert words in said


def test_an_unconfined_sandbox_run_carries_the_warning_and_keeps_every_field(monkeypatch) -> None:
    # Fails if confine.Plan.record stops adding "warning" when backend is None, or who allowed the run.
    _off(monkeypatch)
    run = sandbox.run_script("print(1)", timeout_s=30)
    assert run.status == "ok"
    assert run.isolation == {"backend": None, "why": OFF, "network": False, "container": None, "profile": None,
                             "warning": confine.UNCONFINED_WARNING, "allowed_by": confine.ALLOW}


def test_a_confined_record_carries_no_warning_and_a_child_that_never_started_does() -> None:
    # Fails if Plan.record adds the warning to a confined run, or confine.not_started drops it.
    confined = confine.Plan(confine.APPCONTAINER, "", confine.Confinement(read_write=()))
    assert "warning" not in confined.record()
    assert confine.not_started(confined.record(), OSError("refused"))["warning"] == confine.UNCONFINED_WARNING


def test_the_mcp_refusal_of_a_sensitive_op_carries_the_warning() -> None:
    # Fails if g6.mcp.invoke_reply's denial text stops appending confine.UNCONFINED_WARNING.
    reply = g6.mcp.invoke_reply("llm_router", "pull_model", "{}", allow_sensitive=frozenset())
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert "G6_MCP_SENSITIVE" in reply["error"] and _words(confine.UNCONFINED_WARNING) in _words(reply["error"])


def test_doctor_warns_where_no_backend_confines_runs_and_says_what_to_do(monkeypatch) -> None:
    # Fails if g6.cli.doctor._isolation drops the warning from its detail or leaves the person no fix.
    from g6.cli.doctor import _isolation

    _off(monkeypatch)
    check = _isolation()
    assert check.status == "warn" and OFF in check.detail
    assert _words(confine.UNCONFINED_WARNING) in _words(check.detail)
    assert check.fix


@pytest.mark.parametrize("where", ["g6.mcp docstring", "discovery.HELP", "AGENT_GUIDE.md", "README.md",
                                   "g6.core.sandbox docstring", "g6.core.confine docstring"])
def test_the_same_words_are_where_a_person_meets_them(where: str) -> None:
    # Fails if any of these places drops or rewords the warning (one set of words: confine.UNCONFINED_WARNING).
    text = {"g6.mcp docstring": g6.mcp.__doc__, "discovery.HELP": discovery.HELP,
            "AGENT_GUIDE.md": (ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8"),
            "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
            "g6.core.sandbox docstring": sandbox.__doc__, "g6.core.confine docstring": confine.__doc__}[where]
    assert _words(confine.UNCONFINED_WARNING) in _words(text)


def test_the_readme_says_it_under_install_and_use() -> None:
    # Fails if the README paragraph moves out of the install and use sections.
    readme = _words((ROOT / "README.md").read_text(encoding="utf-8"))
    at = readme.index(_words(confine.UNCONFINED_WARNING))
    assert readme.index("## Install") < at < readme.index("## What is in it")
