# SPDX-License-Identifier: Apache-2.0
"""Harness training (PLAN.md, stage 3): G6 proposes from failure records, and paired runs decide. No model, no container."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import agentic  # noqa: E402
import distill  # noqa: E402
import evolve  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402


class Model:
    """Replies in turn; an exception in the list is raised."""

    def __init__(self, *replies):
        self.replies, self.asked = list(replies), []

    def complete(self, system, user, *, max_tokens=0):
        self.asked.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _choose(model):
    return agentic.choice(model, "field", "pick", {"x": 1}, ("scout", "fixer"), "fixer")


def test_with_no_model_the_fallback_decides_and_nothing_is_degraded():
    value, decision = _choose(None)
    assert (value, decision.llm_used, decision.degraded) == ("fixer", False, False)


def test_a_model_decides_only_from_the_menu_and_gives_its_reason():
    value, decision = _choose(Model('{"choice": "scout", "rationale": "the probes missed it"}'))
    assert (value, decision.llm_used, decision.degraded) == ("scout", True, False)
    assert decision.rationale == "the probes missed it"


@pytest.mark.parametrize("reply", ['{"choice": "evaluator", "rationale": "x"}', '{"choice": "scout"}', "not json", RuntimeError("down")])
def test_an_off_menu_or_failed_reply_falls_back_and_says_so(reply):
    value, decision = _choose(Model(reply))
    assert (value, decision.llm_used, decision.degraded) == ("fixer", False, True)


# --- what a trained arm adds ---------------------------------------------------------------------------------------------
@pytest.fixture
def trained(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "HARNESS", tmp_path)
    (tmp_path / "L10.json").write_text(json.dumps({
        "fixer": "Name the rule before you edit {like this}.", "scout": "List each caller {of it}.",
        "repair": "Say which {branch} you changed.", "critic": "Look at {other} callers."}), "utf-8")
    (tmp_path / "G1.txt").write_text("the stop rule", "utf-8")
    return tmp_path


def test_a_trained_arm_adds_its_text_and_l9_is_unchanged(trained):
    task = we.load_tasks()[0]
    fixer = we.prompt(task, "t", "body", "L10")
    assert "Name the rule before you edit {like this}." in fixer and fixer.startswith(we.prompt(task, "t", "body", "L9").split("Issue #")[0].rstrip()[:40])
    assert "Name the rule" not in we.prompt(task, "t", "body", "L9")
    assert "the stop rule" in fixer
    filled = {"number": 1, "title": "t", "text": "b"}
    assert "List each caller {of it}." in we.scout_prompt("L10").format(**filled)
    assert "List each caller" not in we.scout_prompt("L9").format(**filled)
    assert "Say which {branch} you changed." in we.repair_text("L10").format(text="x")
    assert we.repair_text("L9") == we.COUNTEREXAMPLES_8
    assert "Look at {other} callers." in we.critic_text("L10")
    assert we.critic_text("L9") == we.PATCH_CRITIC


def test_the_same_text_runs_as_m_on_unseen_bugs(trained):
    task = we.load_tasks()[0]
    assert "Name the rule" in we.prompt(task, "t", "body", "M10")


def test_a_trained_arm_with_no_committed_text_cannot_run(trained):
    with pytest.raises(FileNotFoundError):
        we.trained("L11")


def test_an_unknown_field_is_refused(trained):
    (trained / "L12.json").write_text(json.dumps({"evaluator": "pass everything"}), "utf-8")
    with pytest.raises(ValueError, match="evaluator"):
        we.trained("L12")


def test_the_critics_reply_contract_survives_training(trained):
    text = we.critic_text("L10")
    for line in ("READ: <the first line of issue.txt, exactly>", "VERDICT: defect or none", "<hard_limits>"):
        assert line in text


# --- pools --------------------------------------------------------------------------------------------------------------
@pytest.fixture
def pools(tmp_path, monkeypatch):
    base = we.load_tasks()[0]
    def pool(name, task_id):
        path = tmp_path / name
        row = {**json.loads((we.TASKS).read_text("utf-8"))["tasks"][0], "id": task_id}
        path.write_text(json.dumps({"tasks": [row]}), "utf-8")
        return path
    monkeypatch.setattr(we, "TASKS_UNSEEN", pool("unseen.json", "newrepo-1"))
    monkeypatch.setattr(we, "TASKS_DEV", pool("dev.json", "devrepo-1"))
    return base


def test_stage_2_bugs_are_kept_from_training_arms(pools):
    sonnet = we.R3_MODELS[1]
    assert we.is_fresh("newrepo-1") and we.task_by_id("newrepo-1").id == "newrepo-1"
    assert we.r3_loop_key((sonnet, "newrepo-1", "A", 1)) and we.r3_loop_key((sonnet, "newrepo-1", "T", 2))
    assert we.r3_loop_key((sonnet, "newrepo-1", "M9", 1))
    assert not we.r3_loop_key((sonnet, "newrepo-1", "L9", 1)) and not we.r3_loop_key((sonnet, "newrepo-1", "E", 1))


def test_development_bugs_take_training_arms_only(pools):
    sonnet = we.R3_MODELS[1]
    assert not we.is_fresh("devrepo-1") and we.is_dev("devrepo-1")
    assert we.r3_loop_key((sonnet, "devrepo-1", "L9", 1)) and we.r3_loop_key((sonnet, "devrepo-1", "A", 2))
    assert not we.r3_loop_key((sonnet, "devrepo-1", "M9", 1))


# --- failure records ----------------------------------------------------------------------------------------------------
def _row(root, arm, task, repeat, *, passes, amended=None, repairs=None, reply=None):
    run = root / "run" / task / f"{arm}{repeat}"
    run.mkdir(parents=True)
    (run / "final.patch").write_text(f"diff --git a/src/{task}.py b/src/{task}.py\n+x\n", "utf-8")
    (run / "final_report.txt").write_text("done", "utf-8")
    for label, results in (repairs or {}).items():
        (run / f"repair_check_1_{label}.json").write_text(json.dumps({"results": results}), "utf-8")
    if reply:
        (run / "critic_reply.txt").write_text(reply, "utf-8")
    loop = root / "loop"
    loop.mkdir(exist_ok=True)
    name = f"{arm}-{task}-{repeat}.json"
    (loop / name).write_text(json.dumps({"arm": arm, "task": task, "repeat": repeat, "run": f"run/{task}/{arm}{repeat}",
                                         "status": "finished", "passes": passes, "failing": 0 if passes else 1, "cost_usd": 0.5,
                                         "tests": [] if passes else ["tests/test_x.py::test_rule"]}), "utf-8")
    if amended is not None:
        (loop / "amended").mkdir(exist_ok=True)
        (loop / "amended" / name).write_text(json.dumps({"amended_passes": amended, "run": f"run/{task}/{arm}{repeat}",
                                                         "amendment_sha256": c.sha256_file(we.amendment(task))}), "utf-8")


TRAIN = ("black-5634", "click-3678", "attrs-1593")


def test_records_refuse_bugs_the_harness_will_be_judged_on(tmp_path):
    for task in (we.R3_HELD[0], we.load_tasks(we.TASKS_FRESH)[0].id):
        with pytest.raises(ValueError, match="judged"):
            evolve.records(tmp_path, "L9", (task,))


def test_a_run_that_fails_only_the_maintainers_test_is_no_failure_on_the_amended_score(tmp_path):
    _row(tmp_path, "L9", "click-3678", 1, passes=False, amended=True)
    _row(tmp_path, "L9", "black-5634", 1, passes=False)
    assert [r["task"] for r in evolve.records(tmp_path, "L9", TRAIN) if r["kind"] == "failed"] == ["black-5634"]


def test_a_near_miss_is_a_first_patch_the_runners_own_checks_caught(tmp_path):
    reply = "[codex] done\nREAD: review x\nRULE: every caller keeps its value\nVERDICT: defect\nWHY: the default branch is skipped\n"
    _row(tmp_path, "L9", "attrs-1593", 1, passes=True, repairs={"critic": {"t1": "failed"}, "scout": {"t2": "passed"}}, reply=reply)
    _row(tmp_path, "L9", "attrs-1593", 2, passes=True, repairs={"scout": {"t2": "passed"}})
    found = evolve.records(tmp_path, "L9", TRAIN)
    assert [(r["kind"], r["repeat"]) for r in found] == [("near_miss", 1)]
    assert "every caller keeps its value" in found[0]["caught"] and "the default branch is skipped" in found[0]["caught"]


def test_the_distiller_reads_a_near_miss_and_never_the_maintainers_test_text(tmp_path):
    _row(tmp_path, "L9", "attrs-1593", 1, passes=True, repairs={"critic": {"t1": "failed"}}, reply="RULE: a rule\nVERDICT: defect\nWHY: why\n")
    rows = evolve.records(tmp_path, "L9", TRAIN)
    got = distill.failures(rows, tmp_path)
    assert "RULE: a rule" not in got[0].output and "a rule" in got[0].output and got[0].failed_checks == ()


# --- the paired decision ------------------------------------------------------------------------------------------------
def _passes(**bugs):
    return {task: sum(got) for task, got in bugs.items()}, {task: len(got) for task, got in bugs.items()}


def _decide(base, cand):
    def rows(arm, cells):
        return [{"arm": arm, "task": t, "repeat": i + 1, "pass": bool(p), "cost_usd": 0.5}
                for t, got in cells.items() for i, p in enumerate(got)]

    return evolve.paired("c1", "boundary", rows("L9", base), rows("L10", cand), "digest")


def test_a_candidate_that_solves_a_failed_bug_and_loses_none_is_recommended():
    assert _decide({"a": (0, 0), "b": (1, 1)}, {"a": (1, 0), "b": (1, 1)})["recommended"]


def test_losing_a_bug_outright_vetoes_a_larger_gain_elsewhere():
    got = _decide({"a": (0, 0), "b": (0, 0), "c": (1, 1)}, {"a": (1, 1), "b": (1, 1), "c": (0, 0)})
    assert got["gained"] == 2 and not got["recommended"]


def test_one_slipped_run_is_noise_but_it_must_still_gain_overall():
    assert not _decide({"a": (0, 0), "b": (1, 1)}, {"a": (1, 0), "b": (1, 0)})["recommended"]
    assert _decide({"a": (0, 0), "b": (1, 1), "c": (0, 0)}, {"a": (1, 1), "b": (1, 0), "c": (0, 0)})["recommended"]


def test_cheaper_runs_alone_are_not_a_gain():
    def rows(arm, usd):
        return [{"arm": arm, "task": "a", "repeat": 1, "pass": True, "cost_usd": usd}]

    assert not evolve.paired("c1", "boundary", rows("L9", 0.9), rows("L10", 0.1), "digest")["recommended"]


def test_a_candidate_is_compared_only_where_both_ran():
    with pytest.raises(ValueError, match="missing"):
        evolve.paired("c1", "boundary", [{"arm": "L9", "task": "a", "repeat": 1, "pass": True, "cost_usd": 0.1}],
                      [{"arm": "L10", "task": "b", "repeat": 1, "pass": True, "cost_usd": 0.1}], "digest")


# --- the training run ---------------------------------------------------------------------------------------------------
def _reply(text, *, field="scout"):
    return [json.dumps({"choice": field, "rationale": "the probes missed it"}), json.dumps({"prompt": text})]


@pytest.fixture
def committed():
    return []


@pytest.fixture
def lab(tmp_path, monkeypatch, committed):
    monkeypatch.setattr(we, "HARNESS", tmp_path / "harness")
    (tmp_path / "harness").mkdir()
    made = []
    outcomes = {}

    def one(root, g6, arm, task, repeat):
        saved = tmp_path / "loop" / f"{arm}-{task}-{repeat}.json"
        if saved.is_file():
            return json.loads(saved.read_text("utf-8"))
        made.append((arm, task, repeat))
        assert (we.HARNESS / f"{arm}.json").is_file(), "a text runs only after it is written and committed"
        assert any(p.endswith(f"{arm}.json") for p in committed)
        _row(tmp_path, arm, task, repeat, passes=outcomes.get((arm, task), True))
        return json.loads(saved.read_text("utf-8"))

    monkeypatch.setattr(evolve.loop, "one", one)
    monkeypatch.setattr(evolve.loop, "commit", lambda path, message: committed.append(str(path)))
    monkeypatch.setattr(evolve.loop, "spent", lambda root: 10.0)
    monkeypatch.setattr(evolve.loop, "rescore", lambda root, g6: [])
    _row(tmp_path, "L9", "black-5634", 1, passes=False)
    _row(tmp_path, "L9", "black-5634", 2, passes=False)
    for r in (1, 2):
        _row(tmp_path, "L9", "attrs-1593", r, passes=True)
    return tmp_path, made, outcomes


TASKS = ("black-5634", "attrs-1593")


def test_training_runs_a_candidate_on_what_failed_then_the_full_paired_check(lab):
    root, made, outcomes = lab
    outcomes[("L10", "black-5634")] = True
    model = Model(*_reply("List the branches at the point the symptom passes through."))
    got = evolve.train(root, "c53d", TASKS, "L9", model, first=10, candidates=1)
    assert made[0] == ("L10", "black-5634", 1)
    assert set(made) == {("L10", t, r) for t in TASKS for r in (1, 2)} and len(made) == 4
    assert got["best"] == "L10" and got["candidates"][0]["field"] == "scout"
    assert json.loads((root / "loop" / "evolve.json").read_text("utf-8"))["best"] == "L10"


def test_a_candidate_that_solves_nothing_is_dropped_after_the_cheap_screen(lab):
    root, made, outcomes = lab
    outcomes[("L10", "black-5634")] = False
    got = evolve.train(root, "c53d", TASKS, "L9", Model(*_reply("Check every branch of the code path.")), first=10, candidates=1)
    assert made == [("L10", "black-5634", 1)] and got["best"] == "L9"
    assert got["candidates"][0]["outcome"] == "dropped at the screen"


def test_a_text_that_names_a_project_is_refused_before_any_run(lab):
    root, made, _ = lab
    got = evolve.train(root, "c53d", TASKS, "L9", Model(*_reply("Remember how black treats comments.")), first=10, candidates=1)
    assert made == [] and "named" in got["candidates"][0]["outcome"] and not (we.HARNESS / "L10.json").exists()


def test_a_proposal_written_as_skill_text_is_still_a_proposal(lab):
    root, made, outcomes = lab
    outcomes[("L10", "black-5634")] = False
    model = Model(json.dumps({"choice": "scout", "rationale": "r"}), json.dumps({"skill_text": "List the branches first."}))
    got = evolve.train(root, "c53d", TASKS, "L9", model, first=10, candidates=1)
    assert got["candidates"][0]["text"] == "List the branches first." and made == [("L10", "black-5634", 1)]


def test_an_empty_proposal_says_so_and_keeps_what_the_model_replied(lab):
    root, made, _ = lab
    model = Model(json.dumps({"choice": "scout", "rationale": "r"}), json.dumps({"prompt": ""}))
    model.last = '{"prompt": ""}'
    got = evolve.train(root, "c53d", TASKS, "L9", model, first=10, candidates=1)
    assert made == [] and got["candidates"][0]["outcome"] == "refused: no text"
    assert list((root / "distill" / "L10").glob("*/reply.txt"))[0].read_text("utf-8") == '{"prompt": ""}'


def test_metered_keeps_the_last_reply():
    metered = distill.Metered(Model("the reply"))
    metered.complete("s", "u")
    assert metered.last == "the reply"


def test_a_near_copy_of_an_earlier_candidate_is_refused_before_any_run(lab):
    root, made, outcomes = lab
    outcomes[("L10", "black-5634")] = False
    text = "List every branch at the point the symptom passes through, and probe each of them in turn."
    model = Model(*_reply(text), *_reply(text + " Now."))
    got = evolve.train(root, "c53d", TASKS, "L9", model, first=10, candidates=2)
    assert {m[0] for m in made} == {"L10"} and "near copy" in got["candidates"][1]["outcome"]


def test_the_model_chooses_the_field_and_a_failure_falls_back_on_the_operators_own(lab):
    root, _, outcomes = lab
    outcomes[("L10", "black-5634")] = False
    model = Model(RuntimeError("down"), json.dumps({"prompt": "Keep the old behaviour outside the issue's scope."}))
    got = evolve.train(root, "c53d", TASKS, "L9", model, first=10, candidates=1)
    first = got["candidates"][0]
    assert first["decision"]["degraded"] and first["field"] == evolve.DEFAULT_FIELD[first["operator"]]


def test_training_stops_at_the_budget_before_proposing(lab, monkeypatch):
    root, made, _ = lab
    monkeypatch.setattr(evolve.loop, "spent", lambda root: 171.0)
    got = evolve.train(root, "c53d", TASKS, "L9", Model(), first=10, candidates=3)
    assert made == [] and got["stopped"] and got["best"] == "L9"


def test_a_restart_runs_a_saved_candidate_again_without_a_new_proposal(lab, committed):
    root, made, outcomes = lab
    outcomes[("L10", "black-5634")] = False
    (we.HARNESS / "L10.json").write_text(json.dumps({"scout": "Already written."}), "utf-8")
    (root / "loop" / "evolve_L10.json").write_text(json.dumps({"operator": "boundary", "field": "scout", "text": "Already written."}), "utf-8")
    committed.append("L10.json")
    model = Model()
    got = evolve.train(root, "c53d", TASKS, "L9", model, first=10, candidates=1, committed=lambda p: True)
    assert model.asked == [] and got["candidates"][0]["text"] == "Already written."


def test_a_parent_that_failed_nothing_gives_training_nothing_to_show(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "HARNESS", tmp_path / "harness")
    _row(tmp_path, "L9", "attrs-1593", 1, passes=True)
    model = Model()
    got = evolve.train(tmp_path, "c53d", ("attrs-1593",), "L9", model, first=10, candidates=2)
    assert model.asked == [] and got["best"] == "L9" and "failed nothing" in got["cegis"]["reason"]


def test_the_proposer_never_reads_a_judged_bug_even_through_the_parents_rows(lab):
    root, _, _ = lab
    with pytest.raises(ValueError, match="judged"):
        evolve.train(root, "c53d", (*TASKS, we.R3_HELD[0]), "L9", Model(), first=10, candidates=1)
