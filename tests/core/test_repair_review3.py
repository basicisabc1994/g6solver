# SPDX-License-Identifier: Apache-2.0
"""Repair 3: retain useful instructions and keep log contention off replies."""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager

import pytest

from g6.core import agent_guidance, repair_log


@pytest.mark.parametrize("instruction", [
    "Run g6 setup.", "Run G6\nsetup now.", "Set G6_ALLOW_UNCONFINED.",
    "Use --allow=lean.", "Use --allow lean.", "Call store.approve(item).", "Call grant (source).",
])
def test_skill_drops_only_authority_instructions(instruction):
    text = "Read the expression and symbols.\n\n" + instruction + "\n\nCheck the returned evidence.\n"
    assert agent_guidance.skill_for_agent(text) == (
        "Read the expression and symbols.\n\n\nCheck the returned evidence.\n")


@pytest.mark.parametrize("fence", ["```python", "~~~python"])
def test_skill_removes_a_whole_authority_code_block_but_keeps_safe_examples(fence):
    close = fence[:3]
    text = (f"Use these fields.\n\n{fence}\nprepare()\nallow_execution()\nexecute()\n{close}\n\n"
            "```python\ncheck(expression, symbols)\n```\n")
    assert agent_guidance.skill_for_agent(text) == (
        "Use these fields.\n\n\n```python\ncheck(expression, symbols)\n```\n")


def test_clean_skill_is_unchanged():
    text = "# Fields\n\nUse `expression` and `symbols`.\n\n```python\ncheck(expression, symbols)\n```\n"
    assert agent_guidance.skill_for_agent(text) == text
    assert agent_guidance.skill_for_agent(None) is None


def test_sensitive_denial_keeps_its_code_person_decision_and_safe_warning(tmp_path, monkeypatch):
    from g6.mcp import invoke_reply

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    reply = invoke_reply("lean", "check_proof", "{}", allow_sensitive=frozenset())
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert "The person must allow it themselves." in reply["error"]
    assert "You are responsible for what you run." in reply["error"]
    for text in (reply["error"], reply["warning_card"]["message"], reply["degradation_reason"]):
        assert "g6 setup" not in text and "~/.g6/.env" not in text


def test_help_preserves_safe_fields_and_params_template(tmp_path, monkeypatch):
    from g6.mcp import invoke_reply

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    help_text = invoke_reply("algebra", "help", "{}", allow_sensitive=frozenset())["result"]["help"]
    assert "expression" in help_text and "symbols" in help_text
    assert "params_json template" in help_text
    assert not agent_guidance._AUTHORITY.search(help_text)


def test_help_drops_both_lines_of_a_split_command_and_keeps_unrelated_fields():
    text = agent_guidance.help_for_agent("algebra", [
        "simplify(expression, symbols)", "Run g6\nsetup.", "Keep the returned evidence."])
    assert "simplify(expression, symbols)" in text and "Keep the returned evidence." in text
    assert "Run g6" not in text and "setup." not in text


def test_sensitive_help_keeps_its_template_with_the_persons_decision():
    text = agent_guidance.help_for_agent("lean")
    line = next(line for line in text.splitlines() if line.startswith("lean.check_proof:"))
    assert "params_json template" in line and "The person must allow it themselves." in line


def test_append_does_not_wait_for_the_writer_and_flush_preserves_rows(tmp_path, monkeypatch):
    assert repair_log.flush(5)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    entered, release = threading.Event(), threading.Event()

    @contextmanager
    def busy(path):
        entered.set()
        assert release.wait(5)
        yield

    monkeypatch.setattr(repair_log, "_process_lock", busy)
    try:
        started = time.monotonic()
        repair_log.append({"event": "refused", "id": "first", "code": "G6_E_INPUT_VALIDATION", "secret": "omit"})
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert entered.wait(2)
    finally:
        release.set()
        assert repair_log.flush(5)
    rows = (tmp_path / "home/repairs/repairs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in rows] == [
        {"event": "refused", "id": "first", "code": "G6_E_INPUT_VALIDATION"}]


def test_queue_overflow_is_counted_and_does_not_delay_a_reply(tmp_path, monkeypatch):
    assert repair_log.flush(5)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    writer = repair_log._writer
    monkeypatch.setattr(writer.rows, "maxsize", 1)
    monkeypatch.setattr(writer, "dropped", 0)
    entered, release = threading.Event(), threading.Event()

    @contextmanager
    def busy(path):
        entered.set()
        assert release.wait(5)
        yield

    monkeypatch.setattr(repair_log, "_process_lock", busy)
    try:
        repair_log.append({"id": "first"})
        assert entered.wait(2)
        repair_log.append({"id": "second"})
        started = time.monotonic()
        repair_log.append({"id": "dropped"})
        assert time.monotonic() - started < 0.5
        assert repair_log.repair_stats()["dropped"] == 1
    finally:
        release.set()
        assert repair_log.flush(5)
    rows = (tmp_path / "home/repairs/repairs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in rows] == ["first", "second"]


def test_pending_rows_keep_the_home_at_enqueue_time(tmp_path, monkeypatch):
    assert repair_log.flush(5)
    entered, release = threading.Event(), threading.Event()

    @contextmanager
    def busy(path):
        entered.set()
        assert release.wait(5)
        yield

    monkeypatch.setattr(repair_log, "_process_lock", busy)
    try:
        monkeypatch.setenv("G6_HOME", str(tmp_path / "one"))
        repair_log.append({"id": "one"})
        assert entered.wait(2)
        monkeypatch.setenv("G6_HOME", str(tmp_path / "two"))
        repair_log.append({"id": "two"})
    finally:
        release.set()
        assert repair_log.flush(5)
    for home in ("one", "two"):
        rows = (tmp_path / home / "repairs/repairs.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["id"] for line in rows] == [home]


def test_process_lock_uses_only_nonblocking_attempts_and_bounds_its_wait(tmp_path, monkeypatch):
    attempts = []
    monkeypatch.setattr(repair_log, "LOCK_WAIT_S", 0.05)
    if os.name == "nt":
        import msvcrt

        def busy(fd, mode, size):
            attempts.append(mode)
            raise OSError("busy")

        monkeypatch.setattr(msvcrt, "locking", busy)
        expected = msvcrt.LK_NBLCK
    else:
        import fcntl

        def busy(fd, mode):
            attempts.append(mode)
            raise OSError("busy")

        monkeypatch.setattr(fcntl, "flock", busy)
        expected = fcntl.LOCK_EX | fcntl.LOCK_NB
    started = time.monotonic()
    with pytest.raises(OSError, match="busy"), repair_log._process_lock(tmp_path / "repairs.lock"):
        pytest.fail("a busy lock must never enter the write section")
    assert 0.04 <= time.monotonic() - started < 0.5
    assert len(attempts) > 1 and set(attempts) == {expected}
