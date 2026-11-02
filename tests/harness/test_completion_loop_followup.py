# SPDX-License-Identifier: Apache-2.0
"""What the last review of claude/k-fixes left open, held here once repaired: a patch is known by its bytes, a repeat over MCP
says it tested nothing, and a run stopped after a finding keeps the finding."""
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import keys
from g6.harness import completion_loop as loop
from g6.harness import verification_proposer as proposer
from g6.harness import verification_search as vs
from g6.mcp import review_loop
from g6.workforce import repo_tests

FIRST, SECOND = b"# coding: latin-1\nvalue = '\xe9'\n", b"# coding: latin-1\nvalue = '\xf1'\n"
TASK = "Keep value equal to one"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """One untracked file under review, a reviewer that answers from a script, and no test of a reviewer's ever run."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    source = repo / "mod.py"
    source.write_bytes(b"value = 1\n")
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.setattr(keys, "read", lambda: {})
    monkeypatch.setattr(proposer, "boundary", lambda _: "")
    monkeypatch.setattr(loop, "_final_review", lambda *args: "Recorded final assessment.")
    change = repo_tests.Change(repo, "a" * 40, "a" * 40, "a" * 40, ("mod.py",), {}, {}, ("mod.py",), ())
    monkeypatch.setattr(repo_tests, "checked_repo", lambda _: repo)
    monkeypatch.setattr(repo_tests, "change", lambda *args, **kwargs: change)
    monkeypatch.setattr(repo_tests, "_private", lambda *args: [b"", b"mod.py\0"])   # git's empty tracked diff and one new file
    monkeypatch.setattr(repo_tests, "_tracked_new", lambda *args: set())
    monkeypatch.setattr(repo_tests, "read_at", lambda *args: None)
    monkeypatch.setattr(repo_tests, "snapshot", lambda *args: {"mod.py": hashlib.sha256(source.read_bytes()).hexdigest()})

    def never(*args, **kwargs):
        raise AssertionError("no reviewer's test runs here")

    monkeypatch.setattr(vs, "_run_check", never)
    world = SimpleNamespace(repo=repo, home=home, source=source, change=change, session=home / "completion_loop" / "one",
                            base="a" * 40, calls=[], on_review=None)

    class Clean:
        model = "recorded-reviewer"

        def complete(self, system, user):
            world.calls.append(user)
            if world.on_review:
                world.on_review()
            proof = re.search(r"review [0-9a-f]{32}", user)[0]
            return f"READ: {proof}\nRULE: no defect\nVERDICT: none\nWHY: no failure reproduced\n"

    world.critic = Clean()
    monkeypatch.setattr(loop, "critic_host", lambda: world.critic)
    return world


def test_a_file_whose_bytes_change_during_a_review_in_another_encoding_has_no_clean_review(rig):
    rig.source.write_bytes(FIRST)
    reviewed = repo_tests.diff_text(rig.change)
    rig.on_review = lambda: rig.source.write_bytes(SECOND)
    ended = loop.advance(rig.repo, TASK, rig.base, rig.session, critic=rig.critic)
    assert len(rig.calls) == 1 and reviewed in rig.calls[0]
    assert ended.code == loop.NO_CLEAN_REVIEW


def test_a_repeat_over_mcp_after_an_edit_says_it_tested_nothing(rig):
    first = review_loop.step(str(rig.repo), TASK, "same-task", base=rig.base)
    assert first["ending"] == "clean" and first["completion_state"] == "qualified-draft"
    rig.source.write_bytes(b"value = 999\n")
    repeated = review_loop.step(str(rig.repo), TASK, "same-task", base=rig.base)
    assert len(rig.calls) == 1 and repeated["completion_state"] == "blocked-escalated"
    assert review_loop.CHANGED in repeated["report"] and repeated["ending"] == "untested"


def test_a_repeat_over_mcp_of_the_same_patch_gives_its_report_again_and_says_nothing_ran(rig):
    first = review_loop.step(str(rig.repo), TASK, "same-task", base=rig.base)
    again = review_loop.step(str(rig.repo), TASK, "same-task", base=rig.base)
    assert len(rig.calls) == 1 and again["completion_state"] == first["completion_state"] == "qualified-draft"
    assert again["ending"] == "clean" and again["report"] == first["report"] and again["note"] == loop.ALREADY_ENDED


def test_a_run_stopped_by_its_boundary_after_a_finding_keeps_the_finding(rig, monkeypatch):
    class FindsOne:
        model = "recorded-reviewer"

        def complete(self, system, user):
            proof = re.search(r"review [0-9a-f]{32}", user)[0]
            return (f"READ: {proof}\nRULE: value must be one\nVERDICT: defect\nWHY: the value is wrong\n```python\n"
                    "def test_value():\n    assert False\n```\n")

    monkeypatch.setattr(loop, "run_set", lambda *args, **kwargs: loop.ProbeRun(      # a recorded run: no test executes
        1, {"test_critic.py::test_value": "failed"}, {"test_critic.py::test_value": "assert False"}, boundary="recorded-boundary"))
    assert loop.advance(rig.repo, TASK, rig.base, rig.session, critic=FindsOne()).action == loop.CONTINUE
    monkeypatch.setattr(proposer, "boundary", lambda _: "boundary became unavailable")
    ended = loop.advance(rig.repo, TASK, rig.base, rig.session, critic=FindsOne())
    assert ended.action == loop.STOP and ended.code == "G6_E_ISOLATION_UNAVAILABLE"
    assert json.loads((rig.home / "completion_loop" / "failures.jsonl").read_text())["independently_observed"] is True
    assert ended.ending == "open" and "**Open:**" in ended.text and "boundary became unavailable" in ended.text


def _repo(tmp_path: Path) -> Path:
    from tests.workforce.test_repo_tests import make_repo     # the module's own repository, as its tests build it

    return make_repo(tmp_path)


def test_a_new_file_in_another_encoding_is_another_patch_once_its_bytes_change(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new.py").write_bytes(FIRST)
    before = repo_tests.diff_text(repo_tests.change(repo))
    (repo / "new.py").write_bytes(SECOND)
    after = repo_tests.diff_text(repo_tests.change(repo))
    assert before != after and hashlib.sha256(SECOND).hexdigest() in after


def test_a_tracked_file_in_another_encoding_is_another_patch_once_its_bytes_change(tmp_path):
    repo = _repo(tmp_path)
    (repo / "calc.py").write_bytes(FIRST)
    before = repo_tests.diff_text(repo_tests.change(repo))
    (repo / "calc.py").write_bytes(SECOND)
    assert repo_tests.diff_text(repo_tests.change(repo)) != before


def test_the_line_that_names_the_bytes_is_no_line_of_code(tmp_path):
    from g6.coding_agent_gate import scan_diff

    repo = _repo(tmp_path)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_new.py").write_bytes(b"# coding: latin-1\ndef test_one():\n    assert '\xe9'\n")
    text = repo_tests.diff_text(repo_tests.change(repo))
    marked = [line for line in text.splitlines() if line.startswith("\\ G6:")]
    assert len(marked) == 1 and not scan_diff(text)
