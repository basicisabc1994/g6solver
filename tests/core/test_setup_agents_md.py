# SPDX-License-Identifier: Apache-2.0
"""``g6 setup --client codex`` offers G6's instructions to Codex as a delimited block in the person's own
``AGENTS.md`` (plan revision 8, A): shown first, written only on a yes, the rest of the file untouched, and a re-run
replaces only the block. Every test uses a temporary home and ``CODEX_HOME``; nothing reads the person's own."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.cli import setup
from g6.mcp import instructions


@pytest.fixture
def codex(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup, "STEPS", ())
    monkeypatch.setattr("g6.core.outside.find_program", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr("subprocess.run", lambda command, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    return home / "AGENTS.md"


def _setup(answer, events):
    def ask(prompt):
        events.append(("ask", prompt))
        if isinstance(answer, BaseException):
            raise answer
        return answer
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    args = parser.parse_args(["--client", "codex", "--refuse-unconfined"])
    return setup.main(args, ask=ask, say=lambda text: events.append(("say", text)))


def test_a_yes_writes_the_block_after_showing_it(codex):
    events = []
    assert _setup("y", events) == 0
    block = instructions.agents_block()
    assert codex.read_text(encoding="utf-8") == block + "\n"
    shown = next(i for i, (kind, text) in enumerate(events) if kind == "say" and block in text)
    asked = next(i for i, (kind, text) in enumerate(events) if kind == "ask" and "AGENTS.md" in text)
    assert shown < asked
    assert instructions.TEXT in block
    assert block.startswith(instructions.AGENTS_BEGIN) and block.endswith(instructions.AGENTS_END)


def test_the_rest_of_the_file_is_untouched_and_a_rerun_replaces_only_the_block(codex):
    codex.parent.mkdir(parents=True)
    before, after = "# My rules\n\nUse tabs.\n\n", "\n## Later\nKeep this.\n"
    stale = f"{instructions.AGENTS_BEGIN}\nan earlier G6's words\n{instructions.AGENTS_END}"
    codex.write_text(before + stale + after, encoding="utf-8")
    assert _setup("yes", []) == 0
    assert codex.read_text(encoding="utf-8") == before + instructions.agents_block() + after
    once = codex.read_bytes()
    events = []
    assert _setup("y", events) == 0
    assert codex.read_bytes() == once
    assert not any(kind == "ask" and "AGENTS.md" in text for kind, text in events)   # nothing to change, nothing asked


def test_a_file_without_the_block_gets_it_appended(codex):
    codex.parent.mkdir(parents=True)
    codex.write_text("# My rules\nUse tabs.", encoding="utf-8")
    assert _setup("y", []) == 0
    assert codex.read_text(encoding="utf-8") == "# My rules\nUse tabs.\n\n" + instructions.agents_block() + "\n"


@pytest.mark.parametrize("answer", ["", "n", "no", EOFError(), OSError("stdin is captured")])
def test_anything_but_a_yes_changes_nothing(codex, answer):
    assert _setup(answer, []) == 0
    assert not codex.exists()
    codex.parent.mkdir(parents=True)
    codex.write_text("# Mine\n", encoding="utf-8")
    events = []
    assert _setup(answer, events) == 0
    assert codex.read_text(encoding="utf-8") == "# Mine\n"
    assert any(kind == "say" and "Nothing was written" in text for kind, text in events)


def test_claude_registration_offers_no_agents_md(codex):
    events = []
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    args = parser.parse_args(["--client", "claude", "--refuse-unconfined"])
    assert setup.main(args, ask=lambda prompt: events.append(("ask", prompt)) or "y",
                      say=lambda text: events.append(("say", text))) == 0
    assert not codex.exists() and not any(kind == "ask" for kind, _ in events)
