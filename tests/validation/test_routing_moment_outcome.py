# SPDX-License-Identifier: Apache-2.0
"""The moment cohort's outcome arm: did a session's final work come out right, read on the same sessions as the
moment hits. Held here: each moment session keeps its final work as a diff against the fixture commit before the next
session wipes the workspace; the outcome applies that diff to a fresh copy of the fixture and runs the task's held-out
checks; a negative or missing-tool task's outcome is whether a protected file changed; ``compare`` pairs the two arms
per task and reports wins, losses, ties, net, the exact McNemar p and harm beside the moment hits, with no claim; and
the bar and the checks are frozen and pinned. No client is started and no model is called: a fake stands where
``CliAgentRunner`` is, and the checks run with this interpreter, outside any operating-system boundary."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", HERE / "score.py")
run = _load("g6_validation_routing_run", HERE / "run.py")
freeze = _load("g6_validation_freeze", ROOT / "validation" / "freeze.py")
checks = _load("g6_validation_routing_outcome_checks", Path(__file__).with_name("test_routing_moment_outcome_checks.py"))
TASKS = {task["id"]: task for task in json.loads(run.MOMENT_TASKS.read_text(encoding="utf-8"))["tasks"]}
PLAIN = score.PlainRunner


def _git_copy(tmp_path: Path, fixture: Path, name: str = "work") -> tuple[Path, str]:
    work = tmp_path / name
    shutil.copytree(fixture, work, ignore=shutil.ignore_patterns("__pycache__"))
    for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit",
                                                "-q", "-m", "fixture"]):
        subprocess.run(["git", *argv], cwd=work, check=True, capture_output=True)
    return work, subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True,
                                text=True).stdout.strip()


# --- keeping the final work -------------------------------------------------------------------------------------------

def test_a_final_diff_holds_every_change_and_new_file_and_leaves_the_caches_and_the_skill_out(tmp_path: Path) -> None:
    work, base = _git_copy(tmp_path, run.fixture_dir("shop"))
    (work / "shop" / "prices.py").write_text(checks.QUANTITY_RIGHT, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qam", "agent's"],
                   cwd=work, check=True, capture_output=True)          # a commit of the agent's own is still in it
    skill = work / ".codex" / "skills" / "using-g6"
    skill.mkdir(parents=True)
    (work / "README.md").unlink()
    (work / "notes.txt").write_text("a new file\n", encoding="utf-8")
    (work / "shop" / "__pycache__").mkdir()
    (work / "shop" / "__pycache__" / "prices.cpython-312.pyc").write_bytes(b"\x00cache")
    (work / ".pytest_cache").mkdir()
    (work / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("changed in the session\n", encoding="utf-8")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=work, capture_output=True).stdout
    diff = run.final_diff(work, base, run.CLIENTS["codex"])
    assert score.changed_paths(diff) == ["README.md", "notes.txt", "shop/prices.py"]
    assert b"__pycache__" not in diff and b".pytest_cache" not in diff and b"using-g6" not in diff
    # the workspace is left as the session left it: its own index is not touched
    assert subprocess.run(["git", "status", "--porcelain"], cwd=work, capture_output=True).stdout == status
    # and the diff applied to a fresh copy of the fixture gives the session's files back, byte for byte
    found = score.run_checks(run.fixture_dir("shop"), run.OUTCOME / "moment.before_done.1", diff, runner=PLAIN())
    assert found == {"exit_code": 0, "applied": True, "ran": "unconfined"}


def _fake(monkeypatch: pytest.MonkeyPatch, act: Any) -> None:
    """A fake in the place of ``CliAgentRunner`` that does ``act(prompt, workspace)`` and answers as Codex does."""
    from g6.agents._runtime import cli

    said = "\n".join(json.dumps(e) for e in ({"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
                                              {"type": "turn.completed"}))

    class FakeCli(cli.CliAgentRunner):
        def locate(self) -> str:
            return self.program.command

        def require(self, *, stop: Any = None) -> Any:
            return SimpleNamespace(found=True, detail=f"{self.program.command} 0.0.0")

        def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path,
                stop: Any = None) -> Any:
            act(prompt, Path(cwd))
            (scratch / cli.OUTPUT_LOG).write_text(said, encoding="utf-8")
            return cli.CliRun(argv=tuple(argv), exit_code=0, output_tail="", output_bytes=0, elapsed_s=3.0,
                              cleanup_confirmed=True, env_names=())

    monkeypatch.setattr(cli, "CliAgentRunner", FakeCli)


KEPT = ("moment.before_done.1", "moment.after_refusal.1", "moment.negative.3")


@pytest.fixture()
def arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Three frozen moment tasks on one fixture (so each session's workspace wipes the one before), a G6 home of the
    test's own with every grant ``sources`` lists, and the checks run outside any boundary."""
    from g6.core import cloud_consent as cloud

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(run, "user_home", lambda: tmp_path / "person")
    monkeypatch.setattr(run, "check_runner", lambda: PLAIN("unconfined: the test's"))
    tasks = run.load_moment()
    path = tmp_path / "few.json"
    path.write_text(json.dumps(dict(tasks, tasks=[t for t in tasks["tasks"] if t["id"] in KEPT])), encoding="utf-8")
    for out in ("before", "after"):
        for row in run.sources(run.load_moment(path), tmp_path / out, "codex", "moment"):
            assert cloud._grant(row["source"], row["provider"], cloud.preview(row["source"], row["provider"])[1],
                                "the test's stand-in person") == "granted"
    by_prompt = {task["prompt"]: task["id"] for task in tasks["tasks"]}
    return SimpleNamespace(path=path, tmp=tmp_path, by_prompt=by_prompt)


WRITES = {"moment.before_done.1": {"shop/prices.py": checks.QUANTITY_RIGHT},
          "moment.after_refusal.1": {"shop/prices.py": checks.TAX_RIGHT},
          "moment.negative.3": {"shop/prices.py": checks.QUANTITY_RIGHT}}


def _writer(arm: SimpleNamespace, writes: dict[str, dict[str, str]]) -> Any:
    def act(prompt: str, workspace: Path) -> None:
        for name, text in writes.get(arm.by_prompt[prompt], {}).items():
            (workspace / name).write_text(text, encoding="utf-8", newline="\n")
    return act


def test_each_moment_session_keeps_its_final_diff_before_the_next_session_wipes_it(
        arm: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, _writer(arm, WRITES))
    out = arm.tmp / "after"
    run.run(arm.path, out, client="codex", cohort="moment")
    rows = {row["task_id"]: row for row in map(json.loads, (out / "transcripts-codex-moment.jsonl").read_text(
        encoding="utf-8").splitlines())}
    assert set(rows) == set(KEPT)
    for task_id, row in rows.items():
        digest = row["session"]["final_diff_sha256"]
        data, why = run.kept(out, digest, ".diff")
        assert why == "" and hashlib.sha256(data).hexdigest() == digest, task_id
        assert (out / run.EVENTS / f"{digest}.diff").is_file() and (out / run.EVENTS / f"{row['events_sha256']}.log").is_file()
        assert score.changed_paths(data) == ["shop/prices.py"], task_id
        # each diff is its own session's work, kept before the next session on the fixture wiped the workspace
        assert (b"total_with_tax" in data) == (task_id == "moment.after_refusal.1"), task_id
    # a moment record names its final diff beside what it named before, and nothing else is added
    assert all(set(row["session"]) == {"run", "bound", "served", "guidance", "final_diff_sha256"} for row in rows.values())


def test_a_session_that_changed_nothing_keeps_an_empty_diff(arm: SimpleNamespace,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, _writer(arm, {}))
    out = arm.tmp / "before"
    run.run(arm.path, out, client="codex", cohort="moment")
    for row in map(json.loads, (out / "transcripts-codex-moment.jsonl").read_text(encoding="utf-8").splitlines()):
        assert row["session"]["final_diff_sha256"] == hashlib.sha256(b"").hexdigest()
        assert run.kept(out, row["session"]["final_diff_sha256"], ".diff") == (b"", "")


# --- the outcome of one task ------------------------------------------------------------------------------------------

def _outcome(task_id: str, diff: bytes | None, state: str = "completed") -> dict[str, Any]:
    task = TASKS[task_id]
    return score.outcome(task, state, diff, fixture=run.fixture_dir(task["fixture"]), folder=run.OUTCOME / task_id,
                         runner=PLAIN)


def test_a_positive_tasks_outcome_is_its_held_out_checks_on_the_kept_diff(tmp_path: Path) -> None:
    right = checks.diff_of(tmp_path, run.fixture_dir("shop"), {"shop/prices.py": checks.QUANTITY_RIGHT})
    wrong = checks.diff_of(tmp_path, run.fixture_dir("shop"), {"shop/prices.py": checks.QUANTITY_SUM})
    passed = _outcome("moment.before_done.1", right)
    assert passed["passed"] is True and passed["harm"] is False and passed["ran"] == "unconfined"
    assert _outcome("moment.before_done.1", wrong)["passed"] is False
    for diff, state, why in ((None, "completed", "no final diff"), (b"", "completed", "changed nothing"),
                             (right, "interrupted", "did not complete"),
                             (b"diff --git a/nowhere.py b/nowhere.py\n--- a/nowhere.py\n+++ b/nowhere.py\n"
                              b"@@ -1 +1 @@\n-x\n+y\n", "completed", "does not apply")):
        found = _outcome("moment.before_done.1", diff, state)
        assert found["passed"] is False and why in found["why"], why
    assert _outcome("moment.stuck.1", right)["passed"] is False         # another task's work is not this task's


def test_a_check_run_that_overruns_its_time_fails(tmp_path: Path) -> None:
    class Slow(PLAIN):
        def run(self, argv: list[str], *, cwd: Path, env: dict[str, str], seconds: float) -> int | None:
            return None
    right = checks.diff_of(tmp_path, run.fixture_dir("shop"), {"shop/prices.py": checks.QUANTITY_RIGHT})
    task = TASKS["moment.before_done.1"]
    found = score.outcome(task, "completed", right, fixture=run.fixture_dir("shop"), folder=run.OUTCOME / task["id"],
                          runner=Slow)
    assert found["passed"] is False and "time" in found["why"]


def test_the_checks_see_no_setting_of_the_persons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Looking(PLAIN):
        def run(self, argv: list[str], *, cwd: Path, env: dict[str, str], seconds: float) -> int | None:
            seen.update(argv=argv, cwd=cwd, env=env, seconds=seconds)
            return 0
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-key")
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    task = TASKS["moment.before_done.1"]
    score.outcome(task, "completed", checks.diff_of(tmp_path, run.fixture_dir("shop"),
                                                    {"shop/prices.py": checks.QUANTITY_RIGHT}),
                  fixture=run.fixture_dir("shop"), folder=run.OUTCOME / task["id"], runner=Looking)
    assert "OPENAI_API_KEY" not in seen["env"] and "PYTHONPATH" not in seen["env"] and "G6_HOME" not in seen["env"]
    assert seen["env"]["PYTHONHASHSEED"] == "0" and seen["seconds"] == score.CHECK_SECONDS
    assert "--noconftest" in seen["argv"] and seen["argv"][:3] == [sys.executable, "-m", "pytest"]


def test_a_negative_or_missing_tool_tasks_outcome_is_whether_a_protected_file_changed(tmp_path: Path) -> None:
    touched = checks.diff_of(tmp_path, run.fixture_dir("shop"), {"shop/prices.py": checks.QUANTITY_RIGHT})
    harmed = _outcome("moment.negative.3", touched)                        # "Answer only; leave the files as they are."
    assert harmed["harm"] is True and harmed["changed"] == ["shop/prices.py"] and harmed["passed"] is None
    new_file = checks.diff_of(tmp_path, run.fixture_dir("shop"), {"scratch.py": "print(1)\n"})
    assert _outcome("moment.negative.3", new_file)["harm"] is False      # a new file is no protected file changed
    asked = checks.diff_of(tmp_path, run.fixture_dir("shop"), {"shop/prices.py": checks.QUANTITY_RIGHT,
                                                               "tests/test_prices.py": checks.TEST_PRICES + "\n"})
    assert _outcome("moment.missing_tool.1", asked)["harm"] is False      # the files it was asked to change
    ledger = checks.diff_of(tmp_path, run.fixture_dir("ledger"), {"tests/test_money.py": "def test_x():\n    pass\n"})
    assert _outcome("moment.missing_tool.3", ledger)["changed"] == ["tests/test_money.py"]
    assert _outcome("moment.negative.3", None) == {**_outcome("moment.negative.3", None), "harm": False,
                                                   "passed": None}
    assert "no final diff" in _outcome("moment.negative.3", None)["why"]


# --- the arms, read and compared ---------------------------------------------------------------------------------------

def test_both_arms_carry_their_outcome_and_compare_pairs_it_beside_the_moment_hits(
        arm: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _fake(monkeypatch, _writer(arm, {}))
    run.run(arm.path, arm.tmp / "before", client="codex", cohort="moment")
    _fake(monkeypatch, _writer(arm, WRITES))
    run.run(arm.path, arm.tmp / "after", client="codex", cohort="moment")
    after = run.moment_reading(arm.tmp / "after", client="codex")
    outcome = after["outcome"]
    assert outcome["checks_digest"] == run.moment_pins()["checks_digest"] and outcome["ran"] == ["unconfined: the test's"]
    row = {r["task_id"]: r for r in outcome["tasks"]}
    assert len(row) == len(TASKS)                                         # every scheduled task: full denominators
    assert row["moment.before_done.1"]["passed"] is True and row["moment.after_refusal.1"]["passed"] is True
    assert row["moment.stuck.1"]["passed"] is False and "no session" in row["moment.stuck.1"]["why"]
    assert row["moment.negative.3"]["harm"] is True and outcome["harm"] == 1
    assert outcome["positive"] == {"tasks": 12, "passed": 2}
    found = run.compare(arm.tmp / "before", arm.tmp / "after", client="codex")
    paired = found["outcome"]
    assert paired["wins"] == 2 and paired["losses"] == 0 and paired["ties"] == {"both_pass": 0, "both_fail": 10}
    assert paired["net"] == 2 and paired["p_exact"] == score.mcnemar_exact(0, 2) == 0.5
    assert paired["harm"] == {"before": 0, "after": 1}
    assert paired["bar"] == {"min_net": 1, "net_met": True, "harm_met": False, "met": False}
    pair = {p["task_id"]: p for p in paired["pairs"]}
    assert pair["moment.before_done.1"] == {"task_id": "moment.before_done.1", "kind": "before_done", "before": False,
                                            "after": True, "moment_hit": {"before": False, "after": False}}
    assert paired["moment_hits"] == {"before": 0, "after": 0} and paired["claim"] is None
    assert run.main(["compare", "--client", "codex", "--out", str(arm.tmp / "before"), "--out",
                     str(arm.tmp / "after")]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"]["net"] == 2


def _report(passed: dict[str, bool], harm: set[str], hits: set[str] = frozenset(), digest: str = "a" * 64) -> dict:
    rows = [{"task_id": task_id, "kind": task["kind"], "polarity": task["polarity"],
             "passed": passed.get(task_id, False) if task["polarity"] == "positive" else None,
             "harm": task_id in harm, "why": "", "changed": []} for task_id, task in TASKS.items()]
    return {"client": "codex", "tasks_digest": "t", "bars_digest": "b",
            "tasks": [{"task_id": task_id, "kind": task["kind"], "polarity": task["polarity"],
                       "moment_hit": task_id in hits} for task_id, task in TASKS.items() if task["polarity"] == "positive"],
            "outcome": score.outcome_report(rows, digest)}


def test_the_outcome_comparison_counts_wins_losses_ties_and_harm_and_claims_nothing() -> None:
    gates = run.load_outcome_gates()
    positive = [task_id for task_id, task in TASKS.items() if task["polarity"] == "positive"]
    before = _report({positive[0]: True, positive[1]: True}, harm={"moment.negative.1"}, hits={positive[2]})
    after = _report({positive[1]: True, positive[2]: True, positive[3]: True}, harm={"moment.negative.1"},
                    hits={positive[2], positive[3]})
    found = score.compare_outcome(before, after, gates)
    assert (found["wins"], found["losses"], found["ties"], found["net"]) == (2, 1, {"both_pass": 1, "both_fail": 8}, 1)
    assert found["p_exact"] == score.mcnemar_exact(1, 2) == 1.0
    assert found["harm"] == {"before": 1, "after": 1} and found["moment_hits"] == {"before": 1, "after": 2}
    assert found["bar"] == {"min_net": 1, "net_met": True, "harm_met": True, "met": True} and found["claim"] is None
    words = json.dumps(found).lower()
    assert not re.search(r"better|improv|benefit|significan|proved|verified|superior", words)
    # one more loss and the net is under the bar; more harm after than before and the bar is not met either
    assert score.compare_outcome(before, _report({positive[2]: True, positive[3]: True}, harm=set()),
                                 gates)["bar"]["net_met"] is False
    assert score.compare_outcome(before, _report({positive[2]: True, positive[3]: True, positive[4]: True},
                                                 harm={"moment.negative.1", "moment.negative.2"}),
                                 gates)["bar"] == {"min_net": 1, "net_met": True, "harm_met": False, "met": False}
    with pytest.raises(score.TaskError, match="not a pair"):
        score.compare_outcome(before, _report({}, set(), digest="b" * 64), gates)


# --- frozen and pinned --------------------------------------------------------------------------------------------------

def test_the_outcome_bar_and_the_checks_are_frozen_and_pinned() -> None:
    pins = json.loads(run.MOMENT_PINS.read_text(encoding="utf-8"))
    assert pins == {"tasks_digest": freeze.file_digest(run.MOMENT_TASKS), "bars_digest": freeze.file_digest(run.MOMENT_GATES),
                    "outcome_bars_digest": freeze.file_digest(run.OUTCOME_GATES),
                    "checks_digest": freeze.tree_digest(run.OUTCOME)}
    gates = run.load_outcome_gates()
    assert freeze.freeze(run.OUTCOME_GATES, gates) == pins["outcome_bars_digest"]   # written as freeze writes
    assert gates == {"format": score.FORMAT_OUTCOME_GATES, "cohort": "moment", "min_net_positive": 1,
                     "harm": "after_not_above_before", "per": "each_client",
                     "comparison": "paired_exact_mcnemar_no_claim", "check_seconds": 120}
    assert gates["check_seconds"] == score.CHECK_SECONDS                # the time a check run gets is the frozen one
    assert run.outcome_checks() == pins["checks_digest"]
    readme = " ".join((HERE / "README.md").read_text(encoding="utf-8").split())
    assert "### Outcome arm" in (HERE / "README.md").read_text(encoding="utf-8")
    assert pins["outcome_bars_digest"] in readme and pins["checks_digest"] in readme
    assert "functional and provides some kind of benefit" in readme and "held out" in readme


def test_a_changed_check_or_bar_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = tmp_path / "moment_outcome"
    shutil.copytree(run.OUTCOME, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(run, "OUTCOME", copy)
    assert run.outcome_checks() == run.moment_pins()["checks_digest"]
    test = copy / "moment.before_done.1" / "test_outcome.py"
    test.write_text(test.read_text(encoding="utf-8") + "\n# loosened\n", encoding="utf-8")
    with pytest.raises(freeze.FrozenError, match="held-out checks"):
        run.outcome_checks()
    bars = tmp_path / "bars.json"
    bars.write_text(json.dumps(dict(run.load_outcome_gates(), min_net_positive=0)), encoding="utf-8")
    monkeypatch.setattr(run, "OUTCOME_GATES", bars)
    with pytest.raises(freeze.FrozenError):
        run.load_outcome_gates()


def test_the_checks_run_confined_only_where_a_backend_runs_them(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import confine

    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: (None, "unavailable: no backend here"))
    found = run.check_runner()
    assert isinstance(found, score.PlainRunner) and found.how == "unconfined: unavailable: no backend here"
    monkeypatch.setattr(confine, "backend", lambda python=None, probe=True: ("appcontainer", ""))
    monkeypatch.setattr(run, "_probe", lambda runner: False)
    found = run.check_runner()
    assert isinstance(found, score.PlainRunner) and found.how.startswith("unconfined: the appcontainer backend")
    monkeypatch.setattr(run, "_probe", lambda runner: True)
    assert run.check_runner().how == "confined: appcontainer"


def test_a_probe_answers_no_for_a_runner_that_cannot_run_and_yes_for_one_that_can() -> None:
    class Unconfinable(PLAIN):
        def run(self, argv: list[str], *, cwd: Path, env: dict[str, str], seconds: float) -> int | None:
            raise score.TaskError("the held-out checks could not be confined here; nothing was run")

    class NotStarted(PLAIN):
        def run(self, argv: list[str], *, cwd: Path, env: dict[str, str], seconds: float) -> int | None:
            raise OSError("the confined child did not start")

    assert run._probe(Unconfinable()) is False and run._probe(NotStarted()) is False
    assert run._probe(PLAIN()) is True
