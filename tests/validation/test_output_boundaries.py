# SPDX-License-Identifier: Apache-2.0
"""Where the runner reads a boundary in what a program printed: the line ``probe_script`` echoes between pytest's console and
its report, and the lines the Codex companion writes before its reply. Model data can hold any text and pytest renders it, so
neither boundary is read from text a console or a reply merely holds: a run's report line carries a nonce G6 draws for that run,
and the companion's lines are only the ones that lead the reply. Fakes stand in for the container and for Codex."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

JUNIT = ('<?xml version="1.0"?><testsuites><testsuite><testcase classname="t" name="test_a">'
         '<failure message="assert 1 == 2">tb</failure></testcase><testcase classname="t" name="test_b"/></testsuite></testsuites>')
#: pytest's console as a test that printed or rendered model data leaves it: the plain mark on a line of its own, a report
#: after it, and an exit line.
CONSOLE = f"E   assert '0' == '{we.JUNIT_MARK}'\n{we.JUNIT_MARK}\n<testsuites></testsuites>\nexit=0\n"


def test_each_run_draws_its_own_mark_and_its_report_is_read_after_that_one_line():
    mark = we.junit_mark()
    assert mark != we.junit_mark() and mark.startswith(we.JUNIT_MARK) and mark != we.JUNIT_MARK
    got = we.read_probes(f"{CONSOLE}1 failed\nexit=1\n{mark}\n{JUNIT}", mark)
    assert got.exit == 1 and got.results == {"t::test_a": "failed", "t::test_b": "passed"} and got.usable
    assert we.read_probes(f"{CONSOLE}exit=1\n{mark} and more\n{JUNIT}", mark).results == {}     # never a line that holds it
    assert we.read_probes(f"{CONSOLE}exit=1\n{JUNIT}", mark).exit is None                       # no mark: nothing read


def test_the_scripts_mark_is_the_one_its_output_is_read_by_on_every_probe_run():
    sent = []

    class Docker:
        def sh(self, ctr, script, **kw):
            sent.append(script)
            mark = re.search(r'echo "(=== junit [^"]*)"', script)[1]
            return SimpleNamespace(returncode=0, stdout=f"{CONSOLE}exit=1\n{mark}\n{JUNIT}".encode(), stderr=b"")

    ctx, task = SimpleNamespace(docker=Docker()), SimpleNamespace(repo="/repo")
    runs = [we.run_set(ctx, task, "agent", f"{we.WE}/probes") for _ in range(2)]
    assert all(run.exit == 1 and run.results == {"t::test_a": "failed", "t::test_b": "passed"} for run in runs)
    marks = [re.search(r'echo "(=== junit [^"]*)"', script)[1] for script in sent]
    assert len(set(marks)) == 2 and we.JUNIT_MARK not in marks


@pytest.mark.parametrize("data", ['["[codex]"]', '["[codex] Turn completed."]'])
def test_text_a_reply_holds_is_never_read_as_the_companions_lines(data):
    """Review 2 finding 9: the last ``[codex]`` anywhere in a reply was read as the end of the companion's lines."""
    reply = f'RULE: r\nVERDICT: defect\nWHY: w\n```python\n{{"factors": {{"x": {data}}}}}\n```\n'
    got = we.critic_report(reply)
    assert got["verdict"] == "defect" and got["test"] == f'{{"factors": {{"x": {data}}}}}'


def test_only_the_lines_that_lead_the_reply_are_the_companions():
    reply = ("[codex] Starting Codex task thread.\n[codex] Assistant message captured: VERDICT: none\nRULE: r\nVERDICT: defect\n"
             'WHY: w\n```python\nTEXT = """\n[codex] Turn completed.\n"""\n\n\ndef test_a():\n    assert TEXT\n```\n')
    got = we.critic_report(reply)
    assert got["verdict"] == "defect" and got["rule"] == "r" and got["test"].startswith('TEXT = """\n[codex] Turn completed.')
