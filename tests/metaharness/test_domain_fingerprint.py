# SPDX-License-Identifier: Apache-2.0
"""A domain's fingerprint, the ``domain`` part of what a person decides that ``tick_rca`` reads: the canonical value of what a
profile may run there, or unknown, which scores ``open``. FakeLLM only.

Each test scores ``edit_space_short`` from a tick whose candidates changed only wording, so ``failed`` means the domain is shown
to have changed (the remedy counts as applied, and its measure did not move) and ``open`` means it is not."""
import functools
import json
import math
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from g6.core.llm import FakeLLM
from g6.harness import Check
from g6.metaharness.domain import Execution
from g6.metaharness.lessons import FAILED, OPEN
from g6.metaharness.loop import ShadowLoop, _profile_space
from g6.metaharness.pool import EditSpace
from g6.metaharness.schema import Model, PipelineStep, Profile
from g6.metaharness.steps import repair_with_tests, second_look
from g6.metaharness.tick_rca import TickDiagnosis, TickRecord, scored, tick_rca

MODEL = Model(FakeLLM("answer"))


def _fingerprint(**declared) -> str:
    return _profile_space("d", Execution(task="{input}", checks=(), models={"local": MODEL}, **declared))


def _record(domain) -> TickRecord:
    return TickRecord(outcome="refused", candidates=({"fields": ["prompt"]},), enrolment={"domain": domain})


def _verdict(before, after) -> str:
    """``edit_space_short`` of a tick enrolled on ``before``, scored on one enrolled on ``after``."""
    return dict(scored(tick_rca(_record(before)), _record(after)))["edit_space_short"]


def _shows_a_change(**declared) -> bool:
    """Whether the fingerprint of ``declared`` is one ``tick_rca`` reads: widening its edit space must then count as applied."""
    space = dict(getattr(declared.get("space"), "space", {}))
    wider = {**declared, "space": EditSpace({**space, "context_budget_chars": [0, 123]})}
    return _verdict(_fingerprint(**declared), _fingerprint(**wider)) == FAILED


def _step(run, name="act", **settings) -> dict:
    return {"pipeline_steps": {name: PipelineStep(name, "reasoning", "revise", run, **settings)}}


# --- the open findings of tick-rca's fourth review (review-rca4), as this branch's own ---------------------------------

def test_replacing_a_builtin_actions_question_applies_the_domain_remedy():
    caller = FakeLLM(lambda system, user: "B" if system == "Reconsider B" else "A")
    old = second_look("A", ("A", "B"), question="Reconsider A", name="review")
    new = second_look("A", ("A", "B"), question="Reconsider B", name="review")
    context = SimpleNamespace(callers={"answer": caller}, caller=lambda step: caller)
    contract = SimpleNamespace(task="Choose A or B")
    assert old.run("A", contract, context) == "A"
    assert new.run("A", contract, context) == "B"

    def record(step):
        run = Execution(task="{input}", checks=(), models={"local": Model(caller)},
                        pipeline_steps={"review": step})
        return TickRecord(outcome="refused", candidates=({"fields": ["prompt"]},),
                          enrolment={"domain": _profile_space("review", run)})

    before, after = record(old), record(new)
    assert dict(scored(tick_rca(before), after))["edit_space_short"] == FAILED


def test_reordering_keys_in_an_edit_value_does_not_apply_a_domain_remedy():
    old = {"max_model_calls": 2, "max_wall_clock_s": 30}
    reordered = dict(reversed(list(old.items())))
    assert Profile(budgets=old).digest() == Profile(budgets=reordered).digest()
    model = Model(FakeLLM("answer"))

    def record(value):
        run = Execution(task="{input}", checks=(), models={"local": model},
                        space=EditSpace({"budgets": [value]}))
        return TickRecord(outcome="refused", candidates=({"fields": ["prompt"]},),
                          enrolment={"domain": _profile_space("review", run)})

    before, after = record(old), record(reordered)
    assert dict(scored(tick_rca(before), after))["edit_space_short"] == OPEN


# --- each setting a step captures is in the fingerprint ---------------------------------------------------------------

def _cut_by_default(n):
    def run(output, contract, ctx, n=n):
        return output[:n]
    return run


def _cut_by_keyword(n):
    def run(output, contract, ctx, *, n=n):
        return output[:n]
    return run


def _cut(n, output, contract, ctx, *, end=""):
    return output[:n] + end


def _helped(suffix):
    def helper(text):
        return text + suffix

    def run(output, contract, ctx):
        return helper(output)
    return run


def test_a_steps_default_argument_is_in_the_fingerprint():
    assert _verdict(_fingerprint(**_step(_cut_by_default(3))), _fingerprint(**_step(_cut_by_default(4)))) == FAILED


def test_a_steps_keyword_default_is_in_the_fingerprint():
    assert _verdict(_fingerprint(**_step(_cut_by_keyword(3))), _fingerprint(**_step(_cut_by_keyword(4)))) == FAILED


def test_a_partials_arguments_and_keywords_are_in_the_fingerprint():
    three = _fingerprint(**_step(functools.partial(_cut, 3)))
    assert _verdict(three, _fingerprint(**_step(functools.partial(_cut, 4)))) == FAILED
    assert _verdict(three, _fingerprint(**_step(functools.partial(_cut, 3, end="!")))) == FAILED
    assert _verdict(three, _fingerprint(**_step(functools.partial(_cut, 3)))) == OPEN


def test_what_a_function_the_step_closes_over_captures_is_in_the_fingerprint():
    assert _verdict(_fingerprint(**_step(_helped("!"))), _fingerprint(**_step(_helped("?")))) == FAILED


def test_a_steps_declared_arguments_are_in_the_fingerprint():
    run = _cut_by_default(3)
    assert _verdict(_fingerprint(**_step(run, args={"rounds": 1})), _fingerprint(**_step(run, args={"rounds": 2}))) == FAILED


def test_a_built_in_steps_settings_are_in_the_fingerprint():
    assert _verdict(_fingerprint(**{"pipeline_steps": {"repair": repair_with_tests(2)}}),
                    _fingerprint(**{"pipeline_steps": {"repair": repair_with_tests(3)}})) == FAILED


def _at_most(limit):
    return Check("short enough", lambda out: len(out) <= limit, check_id="short", known_bad=("x" * 99,), known_good="ok")


def test_what_an_optional_check_captures_is_in_the_fingerprint():
    assert _verdict(_fingerprint(optional_checks={"short": _at_most(10)}),
                    _fingerprint(optional_checks={"short": _at_most(20)})) == FAILED


# --- the same declaration is the same domain --------------------------------------------------------------------------

def test_the_same_steps_built_twice_are_the_same_domain():
    def declared():
        return {"pipeline_steps": {"look": second_look("A", ("A", "B"), question="Q"), "repair": repair_with_tests(2),
                                   "cut": PipelineStep("cut", "reasoning", "revise", functools.partial(_cut, 3))},
                "optional_checks": {"short": _at_most(10)}}

    assert _fingerprint(**declared()) == _fingerprint(**declared())
    assert _verdict(_fingerprint(**declared()), _fingerprint(**declared())) == OPEN
    assert _shows_a_change(**declared())


def test_values_in_another_order_or_with_their_keys_in_another_order_are_the_same_domain():
    first = EditSpace({"routing": [{"answer": "local", "review": "local"}, {}],
                       "budgets": [{"max_model_calls": 2, "max_wall_clock_s": 30}]})
    second = EditSpace({"budgets": [{"max_wall_clock_s": 30, "max_model_calls": 2}],
                        "routing": [{}, {"review": "local", "answer": "local"}]})
    assert _verdict(_fingerprint(space=first), _fingerprint(space=second)) == OPEN
    assert _shows_a_change(space=first)


def test_a_check_that_is_an_object_is_not_another_domain_in_every_process():
    """The old fingerprint read such a check by its source and ``repr``, and an object's ``repr`` holds its address: two equal
    checks were two domains, so enrolling again applied the remedy. An object is not rendered, so the domain is unknown."""
    class Short:
        def __call__(self, out):
            return len(out) < 10

    def declared():
        return {"optional_checks": {"short": Check("short", Short(), check_id="short")}}

    assert _verdict(_fingerprint(**declared()), _fingerprint(**declared())) == OPEN
    assert not _shows_a_change(**declared())


# --- what the fingerprint cannot render makes the domain unknown, which scores open ----------------------------------

class _Trims:
    def __init__(self, n):
        self.n = n

    def __call__(self, output, contract, ctx):
        return output[:self.n]


def _no_source(n):
    namespace = {}
    exec(f"def run(output, contract, ctx):\n    return output[:{n}]\n", namespace)      # no file to read its source from
    return namespace["run"]


def _closes_over(value):
    def run(output, contract, ctx):
        return output if value else ""
    return run


def test_a_step_that_is_an_object_makes_the_domain_unknown():
    assert not _shows_a_change(**_step(_Trims(3)))
    assert _verdict(_fingerprint(**_step(_Trims(3))), _fingerprint(**_step(_Trims(4)))) == OPEN


def test_a_step_with_no_source_makes_the_domain_unknown():
    assert not _shows_a_change(**_step(_no_source(3)))
    assert not _shows_a_change(**_step(len))


def test_a_step_that_closes_over_what_json_cannot_hold_makes_the_domain_unknown():
    for value in ({"a", "b"}, threading.Lock(), math, float("nan"), {1: "a"}, b"bytes", _Trims(3)):
        assert not _shows_a_change(**_step(_closes_over(value))), value


def test_a_method_bound_to_an_object_makes_the_domain_unknown():
    assert not _shows_a_change(**_step(_Trims(3).__call__))


def test_an_edit_value_json_cannot_hold_makes_the_domain_unknown():
    for value in ({"a", "b"}, float("nan"), float("inf"), {1: "a"}, object(), Profile()):
        assert not _shows_a_change(space=EditSpace({"routing": [value]})), value


def test_a_known_domain_and_an_unknown_one_show_no_change_either_way():
    known, unknown = _fingerprint(**_step(_cut_by_default(3))), _fingerprint(**_step(_Trims(3)))
    assert _verdict(known, unknown) == OPEN and _verdict(unknown, known) == OPEN


def test_an_unknown_domain_still_moves_where_it_could_be_rendered():
    """What is frozen at enrolment compares the fingerprint whole, so a change in a part that renders is still seen there, though
    ``tick_rca`` reads no change from it."""
    narrow = _fingerprint(space=EditSpace({"retries": [0, 1]}), **_step(_Trims(3)))
    wider = _fingerprint(space=EditSpace({"retries": [0, 1, 2]}), **_step(_Trims(3)))
    assert narrow != wider and _verdict(narrow, wider) == OPEN
    assert narrow == _fingerprint(space=EditSpace({"retries": [1, 0]}), **_step(_Trims(4)))


# --- unreadable or malformed state ------------------------------------------------------------------------------------

def test_a_domain_part_that_was_never_written_shows_no_change():
    now = _fingerprint(**_step(_cut_by_default(3)))
    assert _verdict(None, now) == OPEN
    assert dict(scored(tick_rca(TickRecord(outcome="refused", candidates=({"fields": ["prompt"]},))), _record(now))
                )["edit_space_short"] == OPEN
    legacy = TickRecord(outcome="refused", candidates=({"fields": ["prompt"]},), enrolment="one digest for every part")
    assert dict(scored(tick_rca(legacy), _record(now)))["edit_space_short"] == OPEN


def test_a_domain_part_written_by_an_older_fingerprint_shows_no_change():
    older = "5" * 64                     # how the fingerprint before this one wrote a domain: a bare digest
    now = _fingerprint(**_step(_cut_by_default(3)))
    assert _verdict(older, now) == OPEN and _verdict(now, older) == OPEN


def test_a_domain_part_that_is_not_text_shows_no_change():
    now = _fingerprint(**_step(_cut_by_default(3)))
    row = tick_rca(_record(now)).to_dict()
    for junk in (5, None, ["a"], {"x": 1}, ""):
        kept = TickDiagnosis.from_dict({**row, "enrolment": {"domain": junk}})
        assert dict(scored(kept, _record(now)))["edit_space_short"] == OPEN, junk


def test_a_cycle_in_what_a_step_captures_is_rendered_and_the_same_each_time():
    def looping():
        items = []
        items.append(items)

        def run(output, contract, ctx):
            return run(output[1:], contract, ctx) if output.startswith(" ") and items else output
        return run

    assert _fingerprint(**_step(looping())) == _fingerprint(**_step(looping()))
    assert _shows_a_change(**_step(looping()))


def test_an_empty_cell_makes_the_domain_unknown_and_raises_nothing():
    def unfinished(bind):
        def run(output, contract, ctx):
            return later(output)
        if bind:
            def later(text):
                return text
        return run

    assert not _shows_a_change(**_step(unfinished(False)))     # the cell ``later`` was never bound
    assert _shows_a_change(**_step(unfinished(True)))


# --- a stop or a restart between what is written ----------------------------------------------------------------------

_IN_A_FRESH_PROCESS = """
from tests.metaharness.test_domain_fingerprint import _fingerprint, declared_for_a_restart
print(_fingerprint(**declared_for_a_restart()))
"""


def declared_for_a_restart() -> dict:
    return {"space": EditSpace({"budgets": [{"max_model_calls": 2, "max_wall_clock_s": 30}], "hints": [["a", "b"], []],
                                "routing": [{"answer": "local", "review": "local"}]}),
            "optional_checks": {"short": _at_most(10)},
            "pipeline_steps": {"look": second_look("A", ("A", "B"), question="Q"), "repair": repair_with_tests(2),
                               "cut": PipelineStep("cut", "reasoning", "revise", functools.partial(_cut, 3), args={"n": 3})}}


def test_a_restarted_process_gives_the_same_fingerprint():
    from tests.metaharness.test_domain_fingerprint import _fingerprint as fingerprint, declared_for_a_restart as declared

    here = fingerprint(**declared())                 # imported by the name a fresh process gives the module
    root = Path(__file__).resolve().parents[2]
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed,
               "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root), os.environ.get("PYTHONPATH", "")])}
        ran = subprocess.run([sys.executable, "-c", _IN_A_FRESH_PROCESS], env=env, cwd=root, capture_output=True, text=True,
                             timeout=120, check=False)
        assert ran.returncode == 0, ran.stderr[-2000:]
        assert ran.stdout.strip() == here
    assert _shows_a_change(**declared_for_a_restart())


def test_a_diagnosis_read_back_from_its_row_scores_as_it_did():
    before, same = _fingerprint(**_step(_cut_by_default(3))), _fingerprint(**_step(_cut_by_default(3)))
    after = _fingerprint(**_step(_cut_by_default(4)))
    kept = TickDiagnosis.from_dict(json.loads(json.dumps(tick_rca(_record(before)).to_dict())))
    assert dict(scored(kept, _record(after)))["edit_space_short"] == FAILED
    assert dict(scored(kept, _record(same)))["edit_space_short"] == OPEN


# --- in the tick ------------------------------------------------------------------------------------------------------

def _looked(question):
    return {"look": second_look("Answer: 1", ("Answer: 1", "Answer: 2"), question=question, name="look")}


def test_a_built_in_step_given_another_question_and_enrolled_again_applies_the_domains_remedy(tmp_path, monkeypatch):
    from tests.metaharness.test_tick_rca import _loop, _named, _reenrolled, _wording

    loop = _loop(tmp_path, FakeLLM(_wording))
    loop.tick()
    run = loop.domain.execution()
    _reenrolled(loop, monkeypatch, replace(run, pipeline_steps=_looked("Check it once more.")))
    assert "edit_space_short" in _named(loop.log.cycles()[-1])
    assert _reenrolled(loop, monkeypatch, replace(run, pipeline_steps=_looked("Check it against the input."))) == FAILED


def test_a_fresh_handle_on_the_same_domain_finds_nothing_changed_and_applies_no_remedy(tmp_path):
    from datetime import timedelta

    from g6.metaharness.loop import FROZEN_CHANGED, LoopSettings
    from g6.metaharness.method import Method
    from g6.self_maintenance.pacing import ProviderWindow
    from tests.metaharness.test_tick_rca import MONDAY, _named, _wording, domains

    class Looked(domains.WordProblems):
        def execution(self):
            return replace(super().execution(), pipeline_steps=_looked("Check it once more."))

    method = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, use_recovery=False, use_reflection=False)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, MONDAY),), method=method, audit_per_tick=60)

    def opened():                                    # a new process builds the same domain anew, closures and all
        return ShadowLoop(Looked(tmp_path), tmp_path / "loop", settings, proposer=FakeLLM(_wording),
                          now=lambda: MONDAY + timedelta(days=2), clock=domains.Clock())

    first = opened()
    assert first.enrol("Ada")["completion_state"] == "qualified-draft"
    first.tick()
    assert "edit_space_short" in _named(first.log.cycles()[-1])
    again = opened()
    row = again.tick()
    assert row.get("code") != FROZEN_CHANGED, row
    assert dict(again.log.cycles()[-1]["rca_scored"])["edit_space_short"] == OPEN


# --- the domains this repository defines ------------------------------------------------------------------------------

def test_every_domain_this_repository_defines_renders_whole(tmp_path):
    from tests.metaharness.test_coding_replay import _domain
    from tests.metaharness.test_tick_rca import domains

    for domain in (_domain(tmp_path / "r"), domains.WordProblems(tmp_path / "w"), domains.BugFixes(tmp_path / "b")):
        run = domain.execution()
        assert _shows_a_change(space=run.space, optional_checks=run.optional_checks, pipeline_steps=run.pipeline_steps), domain
    for spec in domains.v6b.SPECS.values():
        assert _shows_a_change(optional_checks=spec.optional_checks, pipeline_steps=spec.pipeline_steps), spec.key
