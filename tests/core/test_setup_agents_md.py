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


BLOCK = instructions.agents_block()


@pytest.mark.parametrize("before,after", [
    (b"# My rules\r\nUse tabs.\r\n", b""),                                  # Windows newlines
    ("﻿# Mine\n".encode("utf-8"), b""),                               # a byte-order mark
    (b"# Mine\nUse tabs.\n\n\n\n", b""),                                    # trailing blank lines
    (b"# Mine\r\n\r\n", b"\r\n## Later\r\nKeep this.\r\n"),                  # a block between the person's lines
])
def test_only_the_blocks_bytes_change(codex, before, after):
    codex.parent.mkdir(parents=True)
    newline = "\r\n" if b"\r\n" in before + after else "\n"
    stale = (instructions.AGENTS_BEGIN + newline + "old words" + newline + instructions.AGENTS_END).encode()
    original = before + (stale if after else b"") + after
    codex.write_bytes(original)
    assert _setup("y", []) == 0
    data = codex.read_bytes()
    written = BLOCK.replace("\n", newline).encode("utf-8")
    assert data.startswith(before) and data.endswith(after) and written in data, data
    middle = data[len(before):len(data) - len(after)]
    assert middle.strip(b"\r\n") == written                                 # nothing but the block and line ends
    assert b"\n" not in middle.replace(b"\r\n", b"") if newline == "\r\n" else True
    assert _setup("y", []) == 0 and codex.read_bytes() == data              # a re-run changes nothing


def _left_alone(codex, events, original):
    assert codex.read_bytes() == original if original is not None else not codex.is_file()
    assert not codex.parent.exists() or not [p for p in codex.parent.iterdir() if p.name.startswith(".g6-agents-")]
    said = " ".join(text for kind, text in events if kind == "say")
    assert BLOCK in said and "add this block" in said, said                 # why, and how to add it by hand


@pytest.mark.parametrize("content", [
    f"# Rules\n{instructions.AGENTS_BEGIN}\nmy notes\n".encode(),                       # a lone begin line
    f"# Rules\nmy notes\n{instructions.AGENTS_END}\n".encode(),                         # a lone end line
    (BLOCK + "\nmine\n" + BLOCK + "\n").encode(),                                       # two blocks
    f"{instructions.AGENTS_END}\nmine\n{instructions.AGENTS_BEGIN}\n".encode(),        # end before begin
    "# Règles\n".encode("cp1252"),                                                      # not UTF-8
    "# Mine\n".encode("utf-16"),                                                        # Notepad's "Unicode"
])
def test_a_file_g6_cannot_read_as_one_block_is_left_alone(codex, content):
    codex.parent.mkdir(parents=True)
    codex.write_bytes(content)
    events = []
    assert _setup("y", events) == 0
    _left_alone(codex, events, content)
    assert not any(kind == "ask" for kind, _ in events)


def test_a_directory_named_agents_md_is_left_alone(codex):
    codex.mkdir(parents=True)
    events = []
    assert _setup("y", events) == 0
    assert codex.is_dir() and not list(codex.iterdir())
    said = " ".join(text for kind, text in events if kind == "say")
    assert BLOCK in said and "add this block" in said


def test_a_read_only_agents_md_is_left_alone(codex):
    import os
    import stat

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")
    os.chmod(codex, stat.S_IREAD)
    events = []
    try:
        assert _setup("y", events) == 0
        _left_alone(codex, events, b"# Mine\n")
    finally:
        os.chmod(codex, stat.S_IREAD | stat.S_IWRITE)


def test_a_hard_linked_agents_md_is_left_alone(codex, tmp_path):
    import os

    codex.parent.mkdir(parents=True)
    dotfile = tmp_path / "dotfiles-AGENTS.md"
    dotfile.write_bytes(b"# Mine\n")
    try:
        os.link(dotfile, codex)
    except OSError as exc:
        pytest.skip(f"no hard links here: {exc}")
    events = []
    assert _setup("y", events) == 0
    _left_alone(codex, events, b"# Mine\n")
    assert os.path.samefile(dotfile, codex)


def test_a_failed_write_leaves_the_file_and_no_temporary_file(codex, monkeypatch):
    import os

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")

    def refuse(*args, **kwargs):
        raise PermissionError("the file is open elsewhere")
    monkeypatch.setattr(os, "replace", refuse)
    events = []
    assert _setup("y", events) == 0
    _left_alone(codex, events, b"# Mine\n")


def _changed_while_waiting(codex, events, now):
    _left_alone(codex, events, now)
    said = " ".join(text for kind, text in events if kind == "say")
    assert "changed while setup waited" in said, said


def test_an_edit_saved_while_setup_waits_at_its_question_is_kept(codex):
    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# My rules\nUse tabs.\n")
    events = []

    def ask(prompt):
        with open(codex, "ab") as stream:       # the person saves the file in their editor, then answers yes
            stream.write(b"Never push to main.\n")
        return "y"
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    args = parser.parse_args(["--client", "codex", "--refuse-unconfined"])
    assert setup.main(args, ask=ask, say=lambda text: events.append(("say", text))) == 0
    _changed_while_waiting(codex, events, b"# My rules\nUse tabs.\nNever push to main.\n")


def test_a_file_made_while_setup_waits_at_its_question_is_kept(codex):
    events = []

    def ask(prompt):
        codex.parent.mkdir(parents=True, exist_ok=True)
        codex.write_bytes(b"# Made meanwhile\n")
        return "y"
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    args = parser.parse_args(["--client", "codex", "--refuse-unconfined"])
    assert setup.main(args, ask=ask, say=lambda text: events.append(("say", text))) == 0
    _changed_while_waiting(codex, events, b"# Made meanwhile\n")


@pytest.mark.parametrize("existing", [True, False])
def test_an_edit_saved_just_before_the_replace_is_kept(codex, monkeypatch, existing):
    import tempfile

    codex.parent.mkdir(parents=True)
    if existing:
        codex.write_bytes(b"# Mine\n")
    real = tempfile.mkstemp

    def mkstemp(*args, **kwargs):              # the person's save lands while G6 writes its temporary file
        with open(codex, "ab") as stream:
            stream.write(b"Saved meanwhile.\n")
        return real(*args, **kwargs)
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    events = []
    assert _setup("y", events) == 0
    _changed_while_waiting(codex, events, (b"# Mine\n" if existing else b"") + b"Saved meanwhile.\n")


@pytest.mark.parametrize("line", [
    f"Setup keeps its text between `{instructions.AGENTS_BEGIN}` and `{instructions.AGENTS_END}`.\n",
    f"G6's block starts at a line {instructions.AGENTS_BEGIN} alone.\n",
])
def test_markers_quoted_inside_a_line_are_the_persons_text(codex, line):
    codex.parent.mkdir(parents=True)
    original = ("# My rules\n" + line + "Use tabs.\n").encode()
    codex.write_bytes(original)
    assert _setup("y", []) == 0
    assert codex.read_bytes() == original + b"\n" + BLOCK.encode() + b"\n"     # the block appended, the line whole
    assert _setup("y", []) == 0 and codex.read_bytes() == original + b"\n" + BLOCK.encode() + b"\n"


def _temporary_files(codex):
    return [p.name for p in codex.parent.iterdir() if p.name.startswith(".g6-agents-")]


def test_a_file_made_read_only_while_setup_waits_is_left_alone(codex):
    """The person protects their file while the question waits: the check G6 made at its first read (writable) runs
    again before the write, so nothing is written, no temporary file is left, and setup ends normally."""
    import os
    import stat

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")
    events = []

    def ask(prompt):
        os.chmod(codex, stat.S_IREAD)
        return "y"
    try:
        assert setup.main(_args(), ask=ask, say=lambda text: events.append(("say", text))) == 0
        _left_alone(codex, events, b"# Mine\n")
        assert "read-only" in " ".join(text for kind, text in events if kind == "say")
    finally:
        for path in codex.parent.iterdir():
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX modes: on Windows the test above is this case")
def test_a_chmod_0444_made_while_setup_waits_is_kept(codex):
    import os

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")
    events = []

    def ask(prompt):
        os.chmod(codex, 0o444)
        return "y"
    try:
        assert setup.main(_args(), ask=ask, say=lambda text: events.append(("say", text))) == 0
        _left_alone(codex, events, b"# Mine\n")
        assert os.stat(codex).st_mode & 0o777 == 0o444
    finally:
        os.chmod(codex, 0o644)


def test_a_hard_link_made_while_setup_waits_is_not_split(codex, tmp_path):
    import os

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")
    other = tmp_path / "AGENTS.shared.md"
    events = []

    def ask(prompt):
        try:
            os.link(codex, other)
        except OSError as exc:
            pytest.skip(f"no hard links here: {exc}")
        return "y"
    assert setup.main(_args(), ask=ask, say=lambda text: events.append(("say", text))) == 0
    _left_alone(codex, events, b"# Mine\n")
    assert os.path.samefile(codex, other)
    assert "hard-linked" in " ".join(text for kind, text in events if kind == "say")


@pytest.mark.parametrize("when", ["after the yes", "just before the replace"])
def test_the_check_before_the_write_is_the_first_reads_check(codex, monkeypatch, when):
    """One function makes every check, at the first read and again before the write, so the two cannot drift."""
    import shutil

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")
    calls = []
    real = setup._read

    def counted(target):
        calls.append(target)
        return real(target)
    monkeypatch.setattr(setup, "_read", counted)
    if when == "just before the replace":
        copymode = shutil.copymode

        def link_meanwhile(src, dst, **kwargs):
            copymode(src, dst, **kwargs)
            try:
                __import__("os").link(codex, codex.parent / "linked.md")
            except OSError as exc:
                pytest.skip(f"no hard links here: {exc}")
        monkeypatch.setattr(shutil, "copymode", link_meanwhile)
    events = []
    assert _setup("y", events) == 0
    if when == "just before the replace":
        _left_alone(codex, events, b"# Mine\n")
        assert "hard-linked" in " ".join(text for kind, text in events if kind == "say")
    assert len(calls) == 3


def test_a_temporary_file_that_cannot_be_removed_is_named_and_setup_ends_normally(codex, monkeypatch):
    import os

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")

    def refuse(*args, **kwargs):
        raise PermissionError("the file is open elsewhere")
    for name in ("replace", "unlink", "remove"):     # Path.unlink calls os.unlink
        monkeypatch.setattr(os, name, refuse)
    events = []
    assert _setup("y", events) == 0
    assert codex.read_bytes() == b"# Mine\n"
    left = _temporary_files(codex)
    assert len(left) == 1
    assert left[0] in " ".join(text for kind, text in events if kind == "say")
    monkeypatch.undo()
    (codex.parent / left[0]).unlink()


def test_a_read_only_temporary_file_is_removed(codex, monkeypatch):
    """A failed replace after the mode was copied leaves no temporary file, however that mode came out."""
    import os
    import shutil
    import stat

    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"# Mine\n")
    monkeypatch.setattr(shutil, "copymode", lambda src, dst, **kw: os.chmod(dst, stat.S_IREAD))

    def refuse(*args, **kwargs):
        raise PermissionError("the file is open elsewhere")
    monkeypatch.setattr(os, "replace", refuse)
    events = []
    try:
        assert _setup("y", events) == 0
        _left_alone(codex, events, b"# Mine\n")
    finally:
        for path in codex.parent.iterdir():
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("fence,close", [("```", "```"), ("~~~", "~~~~"), ("   ````text", "````  ")])
def test_markers_inside_a_fenced_code_block_are_the_persons_text(codex, fence, close):
    codex.parent.mkdir(parents=True)
    example = f"{instructions.AGENTS_BEGIN}\nmy own example\n{instructions.AGENTS_END}\n"
    original = f"# Notes\n\n{fence}\n{example}{close}\n\nMore notes.\n".encode()
    codex.write_bytes(original)
    events = []
    assert _setup("y", events) == 0
    if fence.startswith(" "):
        # Follow-up repair 1 allows only column-zero opening fences; an indented fence is left for a by-hand edit.
        _left_alone(codex, events, original)
        assert not any(kind == "ask" for kind, _ in events)
        return
    assert codex.read_bytes() == original + b"\n" + BLOCK.encode() + b"\n"
    assert _setup("y", []) == 0 and codex.read_bytes() == original + b"\n" + BLOCK.encode() + b"\n"


def test_a_fence_closed_by_the_other_character_stays_open(codex):
    """CommonMark: a ``` fence is closed only by backticks; a ~~~ line inside it is code."""
    codex.parent.mkdir(parents=True)
    original = (f"```\n~~~\n{instructions.AGENTS_BEGIN}\nx\n{instructions.AGENTS_END}\n```\n").encode()
    codex.write_bytes(original)
    assert _setup("y", []) == 0
    assert codex.read_bytes() == original + b"\n" + BLOCK.encode() + b"\n"


def test_a_file_ending_inside_an_open_fence_is_left_alone(codex):
    """A block added after an unclosed fence would be part of it, and a re-run would add another."""
    codex.parent.mkdir(parents=True)
    original = b"# Notes\n\n```python\nprint(1)\n"
    codex.write_bytes(original)
    events = []
    assert _setup("y", events) == 0
    _left_alone(codex, events, original)


@pytest.mark.parametrize("newline", [b"\r", b"\r\n", b"\n"])
def test_the_newline_style_is_the_files_first_line_end(codex, newline):
    codex.parent.mkdir(parents=True)
    head = b"# Mine" + newline + b"line two" + newline
    codex.write_bytes(head)
    assert _setup("y", []) == 0
    after = codex.read_bytes()
    written = BLOCK.encode().replace(b"\n", newline)
    assert after == head + newline + written + newline
    assert _setup("y", []) == 0 and codex.read_bytes() == after
    stale = head + (instructions.AGENTS_BEGIN + "\nold\n" + instructions.AGENTS_END).encode().replace(b"\n", newline)
    codex.write_bytes(stale + newline + b"tail" + newline)
    assert _setup("y", []) == 0
    assert codex.read_bytes() == head + written + newline + b"tail" + newline


@pytest.mark.parametrize("inside", ["\n", "\r"])
def test_a_block_with_other_line_ends_than_the_file_is_still_one_block(codex, inside):
    """Markers are whole lines ended by any line end (an editor or an earlier write can mix them); the block G6
    writes takes the file's own style."""
    codex.parent.mkdir(parents=True)
    before, after = b"# Mine\r\n\r\n", b"\r\n## Later\r\nKeep this.\r\n"
    stale = (instructions.AGENTS_BEGIN + inside + "old words" + inside + instructions.AGENTS_END).encode()
    codex.write_bytes(before + stale + after)
    assert _setup("y", []) == 0
    assert codex.read_bytes() == before + BLOCK.encode().replace(b"\n", b"\r\n") + after


def test_a_byte_order_mark_leading_a_later_marker_line_is_the_persons_text(codex):
    # Follow-up F1 supersedes repair 3: nothing may precede a marker at column zero, except the file's encoding BOM.
    codex.parent.mkdir(parents=True)
    bom = "﻿"
    original = f"# Mine\n{bom}{instructions.AGENTS_BEGIN}\nold\n{bom}{instructions.AGENTS_END}\ntail\n".encode()
    codex.write_bytes(original)
    assert _setup("y", []) == 0
    assert codex.read_bytes() == original + b"\n" + BLOCK.encode() + b"\n"


def _args():
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    return parser.parse_args(["--client", "codex", "--refuse-unconfined"])


def test_without_a_terminal_nothing_is_written_and_the_block_is_shown(codex, monkeypatch):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    events = []
    assert setup.main(parser.parse_args(["--client", "codex", "--refuse-unconfined"]),
                      say=lambda text: events.append(("say", text))) == 0          # setup's own ask: input
    assert sys.stdin.read() == "y\n"                                                 # nothing read the piped answer
    _left_alone(codex, events, None)
    assert "terminal" in " ".join(text for kind, text in events if kind == "say")


def test_claude_registration_offers_no_agents_md(codex):
    events = []
    parser = argparse.ArgumentParser()
    setup.add_arguments(parser)
    args = parser.parse_args(["--client", "claude", "--refuse-unconfined"])
    assert setup.main(args, ask=lambda prompt: events.append(("ask", prompt)) or "y",
                      say=lambda text: events.append(("say", text))) == 0
    assert not codex.exists() and not any(kind == "ask" for kind, _ in events)
