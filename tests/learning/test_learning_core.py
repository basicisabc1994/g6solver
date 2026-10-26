# SPDX-License-Identifier: Apache-2.0
"""Behaviour of the g6.learning port outside the audit's findings: store, addendum, scoring, judge, bundles,
envelopes, observers and the baselines module. Offline: every agent and judge is a fake."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import g6.learning
from g6.core.llm import FakeLLM
from g6.invariants.envelope import check_envelope
from g6.learning import (AgentReply, ArmComparison, CallbackObserver, Dataset, DatasetBuilder, DefaultClassifier,
                         JsonlObserver, PromptAddendum, ReviewRecord, SkillDocument, T3Strategy, TaskInstance,
                         TaskResult, Theory, TheoryStore, build_addendum, export_bundle, import_bundle, render_addendum,
                         run_learning, score_instance, score_output, sign_test)
from g6.learning.baselines import T0Strategy, TemplateStrategy, render_skill
from g6.learning.scoring import AGENT_SYSTEM_PROMPT, call_agent


def _tasks(prefix: str, n: int, split: str = "train") -> Dataset:
    return Dataset(prefix, [TaskInstance(f"{prefix}: echo {i}", str(i), f"{prefix}{i}") for i in range(n)], split=split)


def _echo(prompt: str) -> str:
    return re.search(r"echo (\d+)", prompt).group(1)


def test_store_needs_an_explicit_path_and_scope(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        TheoryStore(scope="x")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="G6_E_LEARNING_NO_SCOPE"):
        TheoryStore(tmp_path / "t.db", scope=" ")
    with TheoryStore(tmp_path / "t.db", scope="x") as store:
        assert (store.path, store.ephemeral) == (str(tmp_path / "t.db"), False)


def test_add_candidates_validates_and_forces_candidate_state() -> None:
    with TheoryStore(":memory:", scope="x") as store:
        with pytest.raises(ValueError, match="G6_E_LEARNING_BAD_THEORY"):
            store.add_candidates([Theory.new("WRONG_FORMAT", "")])
        claimed = Theory.new("WRONG_FORMAT", "Hint.")
        claimed.status, claimed.confirmations, claimed.strength = "active", 40, 0.99
        [stored] = store.add_candidates([claimed])
        assert (stored.status, stored.confirmations, stored.strength, stored.scope) == ("candidate", 0, 0.5, "x")
        assert store.theories(status="active") == [] and build_addendum(store).text == ""


def test_addendum_is_deterministic_trigger_matched_and_bounded() -> None:
    a = Theory("th_a", "WRONG_FORMAT", "Use digits.", strength=0.7)
    b = Theory("th_b", "TIMEOUT", "Be brief.", trigger="domain:math", strength=0.7)
    assert render_addendum([a, b], {"domain": "MATH"}).theory_ids == ("th_a", "th_b")
    assert render_addendum([a, b], {"domain": "law"}).theory_ids == ("th_a",)
    assert render_addendum([a, b], {"domain": "math"}, max_chars=70).theory_ids == ("th_a",)  # 38 + 1 + 28 fits
    assert render_addendum([a, b], {"domain": "math"}, max_chars=60).theory_ids == ()
    assert render_addendum([a, b], {"domain": "math"}, max_theories=1).theory_ids == ("th_a",)
    assert render_addendum([], None) == PromptAddendum("")


def test_score_modes_and_thresholds() -> None:
    assert score_output(" '42' ", "42") == 1.0 and score_output("the answer is 42", "42") == 0.0
    assert score_output("the answer is 42", "42", "contains") == 1.0
    assert score_output("a b c", "a b d", "token_f1") == pytest.approx(2 / 3)
    partial = TaskInstance("q", "a b d", "x", {"score_mode": "token_f1", "score_threshold": 0.6})
    assert score_instance(partial, "a b c")[1] is True


def test_rubric_instances_need_a_judge_and_a_bad_judge_scores_zero() -> None:
    rubric = TaskInstance("Write a haiku", "", "r1", {"rubric": "Three lines, 5-7-5 syllables."})
    assert score_instance(rubric, "x")[2]["judge_error"] == "judge_unavailable"
    good = score_instance(rubric, "x", judge=FakeLLM(default='{"score": 0.9, "reason": "ok"}'))
    assert good[:2] == (0.9, True)
    bad = score_instance(rubric, "x", judge=lambda prompt: "no json here")
    assert bad[1] is False and bad[2]["judge_error"].startswith("judge_failed")
    refused = run_learning(T3Strategy(), Dataset("r", [rubric]), _echo, holdout=_tasks("h", 2, "holdout"))
    assert (refused.code, refused.completion_state) == ("G6_E_LEARNING_NO_JUDGE", "blocked-escalated")


def test_call_agent_records_a_failure_and_sends_the_system_prompt_to_a_model() -> None:
    def broken(prompt: str) -> str:
        raise RuntimeError("tool down")

    reply = call_agent(broken, "task")
    assert reply.output == "" and "tool down" in reply.trace.error
    result = TaskResult("i", reply.output, False, trace=reply.trace)
    assert DefaultClassifier().classify(result).primary_code == "TOOL_ERROR"
    llm = FakeLLM(default="7")
    assert call_agent(llm, "task").output == "7" and llm.calls == [(AGENT_SYSTEM_PROMPT, "task")]
    assert call_agent(lambda p: AgentReply("x"), "task") == AgentReply("x")


@pytest.mark.parametrize("agent, holdout, code", [
    (None, "ok", "G6_E_LEARNING_NO_AGENT"),
    (lambda p: (_ for _ in ()).throw(RuntimeError("down")), "ok", "G6_E_LEARNING_AGENT_FAILED"),
    (_echo, None, "G6_E_LEARNING_NO_HOLDOUT"),
])
def test_refusals_are_blocked_escalated_with_a_code(agent, holdout, code) -> None:
    report = run_learning(T3Strategy(), _tasks("t", 3), agent, holdout=_tasks("h", 3, "holdout") if holdout else None)
    assert (report.code, report.completion_state) == (code, "blocked-escalated")
    assert check_envelope(report.to_envelope()) == []


def test_expected_missing_is_refused_before_any_call() -> None:
    calls: list[str] = []
    data = Dataset("t", [TaskInstance("q", "", "t0")])
    report = run_learning(T3Strategy(), data, lambda p: calls.append(p) or "x", holdout=_tasks("h", 1, "holdout"))
    assert report.code == "G6_E_LEARNING_EXPECTED_MISSING" and calls == []


def test_dataset_jsonl_and_unique_ids(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    path.write_text("\n".join(json.dumps(o) for o in (
        {"question": "1+1?", "answer": "2", "id": "a"},
        {"prompt": "Summarise", "rubric": "One sentence.", "id": "b"},
    )) + "\n", encoding="utf-8")
    data = DatasetBuilder.from_jsonl(path)
    assert [(i.instance_id, i.expected_output, i.needs_judge) for i in data.instances] == [("a", "2", False),
                                                                                           ("b", "", True)]
    (tmp_path / "bad.jsonl").write_text(json.dumps({"question": "no answer"}), encoding="utf-8")
    with pytest.raises(ValueError, match="no expected answer or rubric"):
        DatasetBuilder.from_jsonl(tmp_path / "bad.jsonl")
    with pytest.raises(ValueError, match="G6_E_LEARNING_DUPLICATE_INSTANCE"):
        Dataset("dup", [TaskInstance("a", "1", "x"), TaskInstance("b", "2", "x")])
    train, val = DatasetBuilder.split(_tasks("t", 10), 0.8, seed=3)
    assert (train.split, val.split, len(train.instances), len(val.instances)) == ("train", "validation", 8, 2)


def test_arms_must_pair_and_sign_test_is_exact() -> None:
    a, b = TaskResult("x", "", True), TaskResult("y", "", False)
    with pytest.raises(ValueError, match="G6_E_LEARNING_UNPAIRED"):
        ArmComparison.from_results([a], [b])
    assert sign_test(5, 0) == 1 / 32 and sign_test(0, 0) == 1.0 and sign_test(1, 1) == 0.75


def test_bundle_round_trip_lands_as_candidates_in_another_store(tmp_path: Path) -> None:
    review = ReviewRecord("reviewer", "2026-10-18", "two hints for the echo task")
    with TheoryStore(tmp_path / "a.db", scope="a") as source, TheoryStore(tmp_path / "b.db", scope="b") as target:
        [t] = source.add_candidates([Theory.new("WRONG_FORMAT", "Answer with the number only.")])
        skill = render_skill(t)
        manifest = export_bundle(tmp_path / "bundle", source, [PromptAddendum("hint", (t.theory_id,)), skill], review)
        assert (manifest["n_theories"], manifest["n_artifacts"], manifest["review"]["reviewer_id"]) == (1, 2, "reviewer")
        imported = import_bundle(tmp_path / "bundle")
        assert imported.review == review and isinstance(imported.artifacts[1], SkillDocument)
        assert check_envelope(imported.to_envelope()) == []
        [landed] = target.add_candidates(imported.theories)
        assert (landed.theory_id, landed.status, landed.scope) == (t.theory_id, "candidate", "b")
    theories = json.loads((tmp_path / "bundle" / "theories.json").read_text(encoding="utf-8"))
    theories[0]["instruction"] = "Edited after review."
    (tmp_path / "bundle" / "theories.json").write_text(json.dumps(theories), encoding="utf-8")
    edited = import_bundle(tmp_path / "bundle")
    assert (edited.code, edited.completion_state) == ("G6_E_LEARNING_BUNDLE_DIGEST_MISMATCH", "blocked-escalated")
    assert check_envelope(edited.to_envelope()) == []


def test_review_fields_must_not_be_blank(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="G6_E_LEARNING_REVIEW_INCOMPLETE"):
        ReviewRecord("", "2026-10-18", "scope")
    with TheoryStore(":memory:", scope="x") as store:
        export_bundle(tmp_path / "b", store, [], ReviewRecord("r", "2026-10-18", "s"))
    manifest = json.loads((tmp_path / "b" / "manifest.json").read_text(encoding="utf-8"))
    manifest["review"]["reviewer_id"] = ""
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert import_bundle(tmp_path / "b").code == "G6_E_LEARNING_REVIEW_INCOMPLETE"


def test_export_refuses_risky_text(tmp_path: Path) -> None:
    with TheoryStore(":memory:", scope="x") as store:
        store.add_candidates([Theory.new("TOOL_ERROR", "Run subprocess.call to fix it.")])
        with pytest.raises(ValueError, match="G6_E_LEARNING_BUNDLE_UNSAFE"):
            export_bundle(tmp_path / "b", store)


def test_observers_record_failures_and_write_jsonl(tmp_path: Path) -> None:
    events: list[str] = []

    def broken(event):
        raise RuntimeError("observer down")

    log = tmp_path / "events.jsonl"
    report = run_learning(T0Strategy(), _tasks("t", 2), _echo, holdout=_tasks("h", 2, "holdout"),
                          observers=[CallbackObserver(broken), CallbackObserver(lambda e: events.append(e.event_type)),
                                     JsonlObserver(log)])
    assert "run_complete" in events and report.observer_failures
    assert report.observer_failures[0].error_message == "observer down"
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["event_type"] == "run_complete"


def test_the_baselines_and_the_skill_renderer_are_off_by_default() -> None:
    assert not {"T0Strategy", "TemplateStrategy", "render_skill"} & set(g6.learning.__all__)
    report = run_learning(TemplateStrategy(), _tasks("t", 4), lambda p: "wrong", holdout=_tasks("h", 4, "holdout"))
    assert report.cycles[0].proposed and report.completion_state == "qualified-draft"
    skill = render_skill(Theory.new("WRONG_FORMAT", "Answer with digits."))
    assert skill.content.startswith("# Handling WRONG_FORMAT") and "Unmeasured" in skill.content


def test_max_theories_caps_what_one_cycle_measures() -> None:
    with TheoryStore(":memory:", scope="x") as store:
        store.add_candidates([Theory.new(code, f"Hint {code}.") for code in ("A", "B", "C")])
        report = run_learning(T3Strategy(), _tasks("t", 3), _echo, holdout=_tasks("h", 3, "holdout"), store=store,
                              max_theories=2, max_cycles=1)
        assert len(report.cycles[0].measured) == 2


def test_no_source_file_emits_verified() -> None:
    # DECISIONS #22: nothing here may lift a result to "verified"; the word appears only in prose
    package = Path(g6.learning.__file__).parent
    for path in package.glob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


def test_successful_report_envelope_is_consistent() -> None:
    report = run_learning(T3Strategy(), _tasks("t", 4), _echo, holdout=_tasks("h", 4, "holdout"), run_id="r1",
                          request_id="q1")
    envelope = report.to_envelope()
    assert check_envelope(envelope) == []
    assert (envelope["completion_state"], envelope["run_id"], envelope["request_id"]) == ("qualified-draft", "r1", "q1")
    assert envelope["warning_card"]["code"] == "G6_E_LEARNING_UNVERIFIED_MEASUREMENT"
    assert envelope["evidence"][0]["n"] == 4
