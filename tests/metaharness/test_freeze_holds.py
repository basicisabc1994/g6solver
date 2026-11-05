# SPDX-License-Identifier: Apache-2.0
"""A frozen file that changes is never adopted past: an adoption is written only if the frozen digests the tick read before
it developed anything are the digests on disk as the adoption is written, checked last with the record already made, and
the record carries them. Each proposer reads a view of its own. FakeLLM is the only model anywhere here."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM
from g6.core.vault import Vault
from g6.metaharness import shadow
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.schema import Profile
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("freeze_holds_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)
LEAN = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), audit_per_tick=0,
                    method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1,
                                  use_recovery=False, use_reflection=False))
FROZEN_CHANGED = "G6_E_LOOP_FROZEN_CHANGED"


def _enrolled(tmp_path, *, settings=SETTINGS, who="Ada", **kwargs):
    domain = kwargs.pop("domain", None) or domains.WordProblems(tmp_path)
    proposer = kwargs.pop("proposer", None) or domains.proposes({"prompt": domains.WORKING})
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=lambda: NOW, clock=domains.Clock(),
                      **kwargs)
    assert loop.enrol(who)["completion_state"] == "qualified-draft"
    return loop, domain


# The reviewers' findings, as they reproduced them ---------------------------------------------------------------------

def test_changed_enrolled_file_is_checked_before_adopting(tmp_path):
    """review-c4, finding 3."""
    domain_file = tmp_path / "domain.py"
    domain_file.write_text("# the declaration the person enrolled\n", encoding="utf-8")
    now = datetime(2026, 11, 1, tzinfo=UTC)

    def propose(system, user):
        # Simulate an external editor saving the enrolled declaration during the
        # lengthy development phase. No G6 enforcement code is replaced.
        domain_file.write_text("# changed after the initial frozen check\n", encoding="utf-8")
        return json.dumps({"prompt": domains.WORKING})

    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, now),),
                            method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1))
    loop = ShadowLoop(domains.WordProblems(tmp_path), tmp_path / "loop", settings,
                      proposer=FakeLLM(propose), now=lambda: now, domain_file=domain_file)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    reply = loop.tick()
    assert "domain_file" in loop._changed()
    assert not loop.mh.adoption_rows(), f"adopted generation {reply.get('head_version')} after the enrolled domain file changed"


def test_frozen_change_while_assembling_adoption_record_must_refuse_adoption(tmp_path, monkeypatch):
    """review-l4, finding 5."""
    declaration = tmp_path / "domain.py"
    declaration.write_text("# version enrolled by a person\n", encoding="utf-8")
    loop, _ = _enrolled(tmp_path)
    loop.domain_file = declaration
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    original = shadow._measured
    changed = []

    def prepare_measurements(report):
        # This is after last_check(), but before the adoption row is appended.
        # Inject an editor save at this deterministic interleaving point.
        declaration.write_text("# edited while the adoption record was assembled\n", encoding="utf-8")
        changed.append(True)
        return original(report)

    monkeypatch.setattr(shadow, "_measured", prepare_measurements)
    reply = loop.tick()
    assert changed and "domain_file" in loop._changed()
    assert loop.mh.adoption_rows() == [], (
        f"adopted despite a frozen change before the adoption write; outcome={reply.get('outcome')!r}; "
        f"generation={loop.mh._head()[0]}"
    )


def test_edit_during_the_final_frozen_check_is_refused(tmp_path, monkeypatch):
    """review-l4b, finding 2."""
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled declaration\n", encoding="utf-8")
    loop, domain = _enrolled(tmp_path, settings=LEAN, who="Review owner", domain_file=declaration)
    start_head = loop.mh._head()
    checks = []
    read_declaration, saved = threading.Event(), threading.Event()
    real_verify = Vault.verify_unchanged

    def slow_verify(self, **kwargs):
        result = real_verify(self, **kwargs)
        checks.append(1)
        if len(checks) == 2:  # pre-tick check, then the final check before adoption
            read_declaration.set()
            assert saved.wait(5), "the test editor did not finish"
        return result

    monkeypatch.setattr(Vault, "verify_unchanged", slow_verify)

    def editor():
        if read_declaration.wait(15):
            declaration.write_text("# saved during final verification\n", encoding="utf-8")
            saved.set()

    writer = threading.Thread(target=editor)
    writer.start()
    try:
        reply = loop.tick()
    finally:
        read_declaration.set()
        writer.join(5)
    assert not writer.is_alive()
    assert saved.is_set() and len(checks) == 2
    assert reply["warning_card"]["code"] == FROZEN_CHANGED, reply
    assert loop.mh._head() == start_head and loop.mh.adoption_rows() == []


def test_one_proposer_cannot_change_the_next_proposers_head_view(tmp_path):
    """review-l4b, finding 4."""
    loop, domain = _enrolled(tmp_path, settings=LEAN, who="Review owner")
    head = loop.mh._head()[1]
    shown = []

    class EditsItsInput:
        name = "edits-its-input"

        def propose(self, view, runner):
            # A normal edit of a Python dict, not an edit to a store or a private cache.
            view.base["prompt"] = "A profile that has never been the head"
            return []

    class ReadsItsInput:
        name = "reads-its-input"

        def propose(self, view, runner):
            shown.append(Profile.model_validate(view.base).digest())
            return []

    loop.mh.propose(EditsItsInput(), budget=120)
    before = len(domain.llm.calls)
    loop.mh.propose(ReadsItsInput(), budget=120)
    assert loop.mh._head()[1] == head
    assert len(domain.llm.calls) == before  # this is the cache hit, not another measurement
    assert shown == [head.digest()], "the next proposer saw a head that never existed"


# The class: the frozen state read before development is what the adoption is written against ---------------------------

def _declared(tmp_path):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# the declaration a person enrolled\n", encoding="utf-8")
    loop, domain = _enrolled(tmp_path, settings=LEAN, domain_file=declaration)
    return loop, domain, declaration


def _edit(declaration) -> None:
    declaration.write_text("# saved by an editor while the cycle ran\n", encoding="utf-8")


def _during(phase: str, declaration, monkeypatch) -> list[str]:
    """Arrange for the declaration to be saved during ``phase`` of the next tick, once."""
    done: list[str] = []

    def once() -> None:
        if not done:
            _edit(declaration)
            done.append(phase)

    if phase == "development":
        real = shadow.ShadowHarness.propose

        def proposing(self, *args, **kwargs):
            once()
            return real(self, *args, **kwargs)

        monkeypatch.setattr(shadow.ShadowHarness, "propose", proposing)
    elif phase == "the held-out evaluation":
        real_evaluate = shadow.ShadowHarness.evaluate

        def evaluating(self, *args, **kwargs):
            once()
            return real_evaluate(self, *args, **kwargs)

        monkeypatch.setattr(shadow.ShadowHarness, "evaluate", evaluating)
    elif phase == "the making of the adoption's row":
        real_measured = shadow._measured
        monkeypatch.setattr(shadow, "_measured", lambda report: (once(), real_measured(report))[1])
    else:                                        # the final check itself, as it reads the sealed bytes
        real_verify, calls = Vault.verify_unchanged, []

        def verifying(self, **kwargs):
            calls.append(1)
            found = real_verify(self, **kwargs)
            if len(calls) == 2:
                once()
            return found

        monkeypatch.setattr(Vault, "verify_unchanged", verifying)
    return done


PHASES = ["development", "the held-out evaluation", "the making of the adoption's row", "the final check"]


@pytest.mark.parametrize("phase", PHASES)
def test_a_frozen_change_at_any_moment_of_a_cycle_is_refused_recorded_and_refused_again(tmp_path, monkeypatch, phase):
    loop, _, declaration = _declared(tmp_path)
    done = _during(phase, declaration, monkeypatch)
    got = loop.tick()
    monkeypatch.undo()
    assert done == [phase] and loop.mh.adoption_rows() == []
    assert got["warning_card"]["code"] == FROZEN_CHANGED and "during this cycle" in got["message"]
    row = loop.log.cycles()[-1]
    assert row["outcome"] == "blocked" and row["code"] == FROZEN_CHANGED and "domain_file" in row["reason"]
    again = loop.tick()
    assert again["warning_card"]["code"] == FROZEN_CHANGED and loop.mh.adoption_rows() == []
    restarted = ShadowLoop(loop.domain, loop.root, LEAN, proposer=domains.proposes({"prompt": domains.WORKING}),
                           now=lambda: NOW, clock=domains.Clock(), domain_file=declaration)
    assert restarted.tick()["warning_card"]["code"] == FROZEN_CHANGED and restarted.mh.adoption_rows() == []


def test_a_case_pool_changed_during_development_is_refused_too(tmp_path, monkeypatch):
    loop, domain, _ = _declared(tmp_path)
    pool, real = domain.development(), shadow.ShadowHarness.propose

    def proposing(self, *args, **kwargs):
        domain.development = lambda: pool[1:]          # a case taken out of what judges, mid-cycle
        return real(self, *args, **kwargs)

    monkeypatch.setattr(shadow.ShadowHarness, "propose", proposing)
    got = loop.tick()
    assert got["warning_card"]["code"] == FROZEN_CHANGED and "cases" in got["message"] and loop.mh.adoption_rows() == []


def test_an_adoption_carries_the_frozen_digests_it_was_written_against(tmp_path):
    loop, _, _ = _declared(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    [row] = loop.mh.adoption_rows()
    assert row["frozen"] == loop.log.enrolled()["frozen"] and row["frozen"]["domain_file"]


def test_no_adoption_stands_on_a_reading_an_earlier_tick_made(tmp_path, monkeypatch):
    loop, _, _ = _declared(tmp_path)
    loop._frozen_seen = loop._frozen_now()[0]            # what a tick before this one read
    monkeypatch.setattr(ShadowLoop, "_refused", lambda self: None)   # as if this tick read nothing of its own
    got = loop.tick()
    assert got["warning_card"]["code"] == FROZEN_CHANGED and loop.mh.adoption_rows() == []


def test_the_limit_of_this_a_change_put_back_before_the_last_read_is_not_seen(tmp_path, monkeypatch):
    """What is left: the check compares digests at two moments, so bytes changed and put back between them read as
    unchanged, and so does an edit after the last read, in the moment before the row is on disk."""
    loop, _, declaration = _declared(tmp_path)
    enrolled = declaration.read_bytes()
    real = shadow.ShadowHarness.propose

    def changed_and_put_back(self, *args, **kwargs):
        _edit(declaration)
        declaration.write_bytes(enrolled)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(shadow.ShadowHarness, "propose", changed_and_put_back)
    assert loop.tick()["outcome"] == "adopted"


# Each proposer reads a view of its own ------------------------------------------------------------------------------------

def test_what_a_domain_does_to_the_measured_view_no_proposer_sees(tmp_path):
    loop, domain = _enrolled(tmp_path, settings=LEAN)
    head, shown = loop.mh._head()[1], []
    measured = loop.mh.measure(budget=120)["view"]
    measured.base["prompt"] = "a profile that was never the head"

    class Reads:
        name = "reads"

        def propose(self, view, runner):
            shown.append(Profile.model_validate(view.base).digest())
            return []

    loop.mh.propose(Reads(), budget=120)
    assert shown == [head.digest()]


# Review 1's classes: what judges is pinned by what decides a verdict ----------------------------------------------------

def test_a_judge_is_pinned_by_its_own_files_and_not_by_what_other_digests_pin(tmp_path):
    from g6.metaharness import loop as loop_module
    from g6.metaharness.domain import Judge

    helper = tmp_path / "freeze_rules.py"
    helper.write_text("def verdict(output, expected):\n    return output == expected\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        rules = importlib.import_module("freeze_rules")
        judge_file = tmp_path / "freeze_judge.py"
        judge_file.write_text("import json\nfrom freeze_rules import verdict\n\ndef judge(output, expected):\n"
                              "    return verdict(output, expected)\n\ndef other(output, expected):\n    return True\n",
                              encoding="utf-8")
        judging = importlib.import_module("freeze_judge")
        files = loop_module._imported_files(judging.judge)
        assert {judge_file.resolve(), helper.resolve()} <= files
        assert not any(loop_module._installed(path) for path in files)          # G6 and the installation: pinned elsewhere
        first = loop_module._judge_digest(Judge(judging.judge, answers_by="independent"))
        assert loop_module._judge_digest(Judge(judging.other, answers_by="independent")) != first
        helper.write_text("def verdict(output, expected):\n    return False\n", encoding="utf-8")
        assert loop_module._judge_digest(Judge(judging.judge, answers_by="independent")) != first
        assert rules.verdict("a", "a")                                          # what runs is unchanged; what is pinned is not
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("freeze_rules", None)
        sys.modules.pop("freeze_judge", None)


@pytest.mark.parametrize("one, other", [(1, "1"), (True, 1), (1, 1.0), ({1: "a"}, {"1": "a"}), ([1], ["1"]),
                                        ((1,), [1]), (b"1", "1"), (None, "None")])
def test_answers_a_judge_can_tell_apart_never_share_an_identity(one, other):
    from g6.metaharness.domain import answer_identity

    assert answer_identity(one) != answer_identity(other)


def test_the_same_answer_has_the_same_identity_whatever_order_it_was_built_in():
    from g6.metaharness.domain import answer_identity

    assert answer_identity({"a": 1, "b": {2, 3}}) == answer_identity({"b": {3, 2}, "a": 1})


# Review 2's classes: files listed whole, answers by an exact encoding, the harness's own check, one rule per cycle ---------

def _a_status(status, loop, domain):
    """Make ``status`` hold in the tick under way, as review 2 made each."""
    from g6.core.llm import LLMError
    from g6.core.vault import AccessDenied
    from g6.metaharness.domain import PAID_MODEL
    from g6.metaharness.loop import _Halted
    from g6.self_maintenance.usage import MODEL_REFUSED

    if status == "budget":
        loop.requests.limit = loop.requests.sent
    elif status == "sealed_breach":
        try:
            domain.sealed_file.read_text(encoding="utf-8")
        except AccessDenied:
            pass
    elif status == "unmetered":
        loop.requests.unseen = True
    elif status == "classifier":
        raise LLMError(f"{MODEL_REFUSED}: permission denied")
    elif status == "barred":
        raise _Halted(PAID_MODEL, "a fake caller switched to a paid backend")


#: Each status a cycle can end with, and its code when no frozen change holds with it.
STATUSES = {"budget": "G6_E_LOOP_TICK_BUDGET_SPENT", "sealed_breach": "G6_E_LOOP_SEALED_BREACH",
            "unmetered": "G6_E_LOOP_UNMETERED_CALLER", "classifier": "G6_E_LOOP_CLASSIFIER_STOP",
            "barred": "G6_E_LOOP_PAID_MODEL", "none": None}


def _statused(tmp_path, status, frozen):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled\n", encoding="utf-8")
    holder = {}

    def propose(system, user):
        if frozen:
            declaration.write_text("# changed during development\n", encoding="utf-8")
        _a_status(status, holder["loop"], holder["domain"])
        return '{"prompt": "No useful improvement"}'

    loop, domain = _enrolled(tmp_path, settings=LEAN, domain_file=declaration, proposer=FakeLLM(propose))
    holder.update(loop=loop, domain=domain)
    return loop, domain


@pytest.mark.parametrize("frozen", [True, False])
@pytest.mark.parametrize("status", list(STATUSES))
def test_one_rule_codes_every_status_crossed_with_a_frozen_change_and_keeps_them_all(tmp_path, status, frozen):
    loop, _ = _statused(tmp_path, status, frozen)
    reply = loop.tick()
    row = loop.log.cycles()[-1]
    codes = [held["code"] for held in row["statuses"]]
    assert reply["warning_card"]["code"] == row["code"] and loop.mh.adoption_rows() == []
    if frozen:
        assert row["code"] == FROZEN_CHANGED and FROZEN_CHANGED in codes
    else:
        assert row["code"] != FROZEN_CHANGED and FROZEN_CHANGED not in codes
    if STATUSES[status] is not None:
        assert STATUSES[status] in codes and (frozen or row["code"] == STATUSES[status])


@pytest.mark.parametrize("status", ["sealed_breach", "unmetered", "classifier", "barred"])
def test_a_safety_stop_ranked_under_a_frozen_change_still_pauses_the_loop_after_a_new_enrolment(tmp_path, status):
    loop, _ = _statused(tmp_path, status, True)
    loop.tick()
    assert loop.log.cycles()[-1]["code"] == FROZEN_CHANGED and loop._must_stop()
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"     # a person enrols the changed file again
    assert loop.tick()["warning_card"]["code"] == "G6_E_LOOP_PAUSED"


def _recommendation(loop):
    loop.requests.open(10_000)
    try:
        profile = loop.mh._head()[1].model_dump(mode="json")
        profile["prompt"] = domains.WORKING
        proposal = loop.mh.candidate(profile, proposer="auto:test")["proposal"]
        decision = loop.mh.decide(loop.mh.evaluate(proposal)["report"], proposal)["decision"]
    finally:
        loop.requests.close()
    assert decision.verdict == "recommend"
    return decision


def test_adopt_runs_the_harness_own_check_and_trusts_no_caller(tmp_path):
    loop, _, declaration = _declared(tmp_path)
    decision = _recommendation(loop)
    loop._frozen_seen = loop._frozen_now()[0]
    _edit(declaration)
    got = loop.mh.adopt(decision, frozen=loop._frozen_seen, last_check=lambda: None)
    assert got["warning_card"]["code"] == FROZEN_CHANGED and loop.mh.adoption_rows() == []


@pytest.mark.parametrize("said", [{}, [], "", 0, False, {"completion_state": "qualified-draft"},
                                  shadow.Held({"envelope": "x"})])
def test_a_callers_check_may_only_refuse_and_nothing_but_none_lets_an_adoption_through(tmp_path, said):
    loop, _, _ = _declared(tmp_path)
    decision = _recommendation(loop)
    loop._frozen_seen = loop._frozen_now()[0]
    got = loop.mh.adopt(decision, frozen=loop._frozen_seen, last_check=lambda: said)
    assert got["completion_state"] == "blocked-escalated" and loop.mh.adoption_rows() == []
    assert loop.mh.adopt(decision, frozen=loop._frozen_seen, last_check=lambda: None)["head_version"] == 1


class _Slotted:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


def _deep(levels):
    value = []
    for _ in range(levels):
        value = [value]
    return value


# set, complex and bytes encode exactly but do not render in the split's reference; NaN neither renders nor is JSON; a
# mapping with keys of two types cannot be put in order; the last two have no exact encoding at all
@pytest.mark.parametrize("make", [lambda: {1, 2}, lambda: complex(1, 2), lambda: b"7", lambda: float("nan"),
                                  lambda: {1: "a", "1": "b"}, lambda: _Slotted(1), lambda: _deep(80)])
def test_a_case_whose_answer_the_loop_cannot_hold_exactly_is_refused_at_enrolment(tmp_path, make):
    from dataclasses import replace

    domain = domains.WordProblems(tmp_path)
    domain.pool = [replace(case, expected=make()) for case in domain.pool]
    loop = ShadowLoop(domain, tmp_path / "loop", LEAN, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    got = loop.enrol("Ada")
    assert got["warning_card"]["code"] == "G6_E_LOOP_ANSWER_UNIDENTIFIABLE" and loop.log.enrolled() is None
    assert "word-" in got["message"]                              # cases are named by id; no answer is shown


@pytest.mark.parametrize("make", [lambda: _Slotted(1), lambda: (lambda: 1), lambda: int, lambda: iter([1]),
                                  lambda: _deep(80), lambda: {"a": [len]}])
def test_an_answer_whose_state_no_encoding_holds_is_refused_not_guessed(make):
    from g6.metaharness.domain import AnswerUnidentifiable, answer_identity

    with pytest.raises(AnswerUnidentifiable):
        answer_identity(make())


def test_an_answer_is_encoded_without_pickle_or_repr_and_a_subclass_by_its_value_and_state(monkeypatch):
    import pickle

    from g6.metaharness.domain import answer_identity

    class Plain:
        def __init__(self):
            self.labels = {"b", "a"}

        def __repr__(self):
            raise AssertionError("repr was read")

    class Text(str):
        def __repr__(self):
            raise AssertionError("repr was read")

    monkeypatch.setattr(pickle, "dumps", lambda *a, **k: (_ for _ in ()).throw(AssertionError("pickle was used")))
    assert len(answer_identity(Plain())) == 64 and answer_identity(-0.0) != answer_identity(0.0)
    one, other = Text("42"), Text("42")
    assert answer_identity(one) == answer_identity(other) != answer_identity("42")
    other.valid = False
    assert answer_identity(one) != answer_identity(other)


def test_a_module_loaded_after_enrolment_from_outside_every_pinned_root_is_a_change(tmp_path, monkeypatch):
    loop, _, _ = _declared(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "frz2_later_helper.py").write_text("FLAG = True\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(elsewhere))
    importlib.import_module("frz2_later_helper")
    try:
        assert "evaluator" in loop._changed()
    finally:
        sys.modules.pop("frz2_later_helper", None)
    assert "evaluator" not in loop._changed()


def test_every_file_under_a_judges_root_is_pinned_whether_or_not_it_was_ever_imported(tmp_path):
    from g6.metaharness import loop as loop_module
    from g6.metaharness.domain import Judge

    root = tmp_path / "judging"
    (root / "rules" / "deeper").mkdir(parents=True)                     # a namespace package: no __init__.py
    (root / "judge.py").write_text("def verdict(output, expected):\n    return output == expected\n", encoding="utf-8")
    never = root / "rules" / "deeper" / "never_imported.py"
    never.write_text("ACCEPTED = 'yes'\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("frz2_listed_judge", root / "judge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = loop_module._judge_digest(Judge(module.verdict, answers_by="independent"))
    never.write_text("ACCEPTED = 'no'\n", encoding="utf-8")
    assert loop_module._judge_digest(Judge(module.verdict, answers_by="independent")) != first


def test_a_declared_root_that_does_not_hold_the_judge_refuses_enrolment(tmp_path):
    domain = domains.WordProblems(tmp_path)
    domain.roots = (tmp_path / "nowhere",)
    loop = ShadowLoop(domain, tmp_path / "loop", LEAN, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    got = loop.enrol("Ada")
    assert got["warning_card"]["code"] == "G6_E_LOOP_JUDGE_UNPINNED" and "the verdict" in got["message"]


def test_a_check_whose_code_is_in_no_file_refuses_enrolment(tmp_path):
    from dataclasses import replace

    from g6.harness import Check

    namespace: dict = {}
    exec("def accepts(output):\n    return True\n", namespace)            # Python code that is in no file
    extra = Check("anything passes", namespace["accepts"], check_id="fileless")
    domain = domains.WordProblems(tmp_path)
    execution = domain.execution
    domain.execution = lambda: replace(execution(), checks=(*execution().checks, extra))
    loop = ShadowLoop(domain, tmp_path / "loop", LEAN, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    got = loop.enrol("Ada")
    assert got["warning_card"]["code"] == "G6_E_LOOP_JUDGE_UNPINNED" and "check fileless" in got["message"]


def test_a_built_in_or_installed_check_needs_no_root(tmp_path):
    from g6.metaharness import loop as loop_module
    from g6.harness import Check

    for run in (bool, len, str.isdigit, json.dumps):
        assert loop_module._held_by(loop_module._code_file(Check("p", run).run), ()), run


def test_two_lambdas_of_one_function_are_two_names_so_a_swap_between_them_is_a_change():
    from g6.metaharness import loop as loop_module

    accept = lambda output: True  # noqa: E731 - two lambdas of one scope share a qualified name
    refuse = lambda output: False  # noqa: E731
    assert loop_module._named(accept)[:2] == loop_module._named(refuse)[:2]
    assert loop_module._named(accept) != loop_module._named(refuse)


# Review 3's classes: the judge is given exactly what was identified, roots are pinned whole with no links, every cycle
# that adopts nothing ends on one rule, and a safety refusal outlives a cycle the log would not take ----------------------

ADMITTED_ANSWERS = [None, True, False, 0, -1, 2 ** 80, -0.0, 0.1, float("nan"), "", "x\ud800y", [], {},
                    {"b": 1, "a": [2, {"c": None}]}, [[], [[]]], {"tests": ["t"], "fixed": "f"}]


@pytest.mark.parametrize("answer", ADMITTED_ANSWERS, ids=range(len(ADMITTED_ANSWERS)))
def test_an_admitted_answer_decodes_to_exactly_what_was_identified_and_shares_nothing(answer):
    import struct

    from g6.metaharness.domain import answer_encoding, answer_from

    def shape(value):
        if isinstance(value, float):
            return ("float", struct.pack(">d", value))
        if isinstance(value, dict):
            return ("dict", [(key, shape(item)) for key, item in value.items()])
        if isinstance(value, list):
            return ("list", [shape(item) for item in value])
        return (type(value).__name__, value)

    decoded = answer_from(answer_encoding(answer))
    assert shape(decoded) == shape(answer)                    # each type exact, dict order and float bytes kept
    assert answer_encoding(decoded) == answer_encoding(answer)
    if isinstance(answer, (list, dict)):
        assert decoded is not answer


def _aliased():
    shared = []
    return [shared, shared]


def _cyclic():
    found: list = []
    found.append(found)
    return found


REFUSED_ANSWERS = {"a str subclass": lambda: type("Text", (str,), {})("x"), "a tuple": lambda: (1,),
                   "bytes": lambda: b"x", "a set": lambda: {1}, "an int key": lambda: {1: "a"},
                   "a list reached twice": _aliased, "a cycle": _cyclic, "too deep": lambda: _deep(80),
                   "a proxy": lambda: __import__("weakref").proxy(_Target([1])), "a bool subclass of int": lambda: _Number(1)}


class _Target(list):
    pass


class _Number(int):
    pass


@pytest.mark.parametrize("name", list(REFUSED_ANSWERS))
def test_an_answer_outside_the_admitted_set_has_no_encoding_and_enrolment_names_its_case_only(name):
    from g6.metaharness.domain import AnswerUnidentifiable, Cut, answer_encoding
    from g6.metaharness.loop import _answer_problems
    from g6.metaharness.schema import SuiteCase

    with pytest.raises(AnswerUnidentifiable):
        answer_encoding(REFUSED_ANSWERS[name]())
    [(code, said)] = _answer_problems(Cut((), (), (SuiteCase("the-case", "input", REFUSED_ANSWERS[name]()),), ()))
    assert code == "G6_E_LOOP_ANSWER_UNIDENTIFIABLE" and "the-case" in said


def test_an_admitted_answer_and_a_refused_one_never_share_an_identity():
    from g6.metaharness.domain import answer_identity

    assert answer_identity([1]) != answer_identity((1,)) and answer_identity("x") != answer_identity(b"x")
    assert answer_identity([[], []]) != answer_identity(_aliased())
    assert answer_identity({"a": 1, "b": 2}) != answer_identity({"b": 2, "a": 1})         # order is part of it


@pytest.mark.parametrize("make", [lambda f: domains.WordProblems(f), lambda f: domains.BugFixes(f)])
def test_the_judge_is_given_fresh_copies_of_what_was_identified_and_the_first_domains_fit(tmp_path, make):
    from g6.metaharness.domain import answer_encoding

    domain = make(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain, settings=LEAN)
    held = {case.case_id: case.expected for case in domain.development()}
    for name in ("tune", "selection", "held_out", "monitor"):
        for case in getattr(loop.cut, name):
            assert answer_encoding(case.expected) == answer_encoding(held[case.case_id])
            assert not isinstance(case.expected, (list, dict)) or case.expected is not held[case.case_id]
    suite = loop.workflow.suite
    assert all(case.expected is not held[case.case_id] or isinstance(case.expected, str)
               for case in (*suite.dev, *suite.held_out, *suite.selection))


def test_coding_replay_answers_are_labels_the_admitted_set_holds():
    from g6.metaharness.coding_replay import RIGHT, WRONG
    from g6.metaharness.domain import answer_encoding, answer_from

    assert all(answer_from(answer_encoding(label)) == label for label in (RIGHT, WRONG))


def test_every_regular_file_under_a_root_is_pinned_whatever_its_suffix(tmp_path):
    from g6.metaharness.loop import _pinned

    files = [tmp_path / "rules.json", tmp_path / "helpers.zip", tmp_path / "deep" / "table.csv", tmp_path / "README"]
    for path in files:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"original")
    for path in files:
        before = _pinned([tmp_path])
        path.write_bytes(b"edited")
        assert _pinned([tmp_path]) != before, path.name
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    before = _pinned([tmp_path])
    (cache / "helpers.cpython-312.pyc").write_bytes(b"what an import writes")
    assert _pinned([tmp_path]) == before                      # an interpreter's own cache stays out


def _junction(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.skipif(os.name != "nt", reason="a junction is Windows's")
def test_a_junction_under_a_root_refuses_enrolment_and_one_made_later_is_a_change(tmp_path):
    from g6.metaharness.domain import Judge
    from g6.metaharness.loop import _pinned

    judge_dir, elsewhere = tmp_path / "judge", tmp_path / "elsewhere"
    judge_dir.mkdir()
    elsewhere.mkdir()
    source = judge_dir / "frz3_linked_judge.py"
    source.write_text('def verdict(output, expected):\n    return output == "Answer: " + str(expected)\n', encoding="utf-8")
    spec = importlib.util.spec_from_file_location("frz3_linked_judge", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = _pinned([judge_dir])
    _junction(judge_dir / "alias", elsewhere)
    assert _pinned([judge_dir]) != before
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, answers_by="independent")
    loop = ShadowLoop(domain, tmp_path / "loop", LEAN, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    got = loop.enrol("Ada")
    assert got["warning_card"]["code"] == "G6_E_LOOP_JUDGE_UNPINNED" and "junction" in got["message"]


@pytest.mark.skipif(os.name != "nt", reason="a junction is Windows's")
def test_a_root_that_is_another_name_for_the_loops_folder_is_the_same_place(tmp_path):
    from g6.metaharness.loop import _same_place

    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    _junction(tmp_path / "alias", loop_dir)
    assert _same_place(loop_dir, tmp_path / "alias") and _same_place(loop_dir / "inner", tmp_path / "alias")
    assert not _same_place(loop_dir, tmp_path / "other")


def test_a_failed_cycle_with_nothing_changed_keeps_its_own_code(tmp_path):
    loop, _, _ = _declared(tmp_path)
    got = loop.fault(RuntimeError("a pass broke outside any tick"))
    row = loop.log.cycles()[-1]
    assert got["warning_card"]["code"] == row["code"] == "G6_E_LOOP_CYCLE_FAILED"
    assert [status["code"] for status in row["statuses"]] == ["G6_E_LOOP_CYCLE_FAILED"]


def test_a_failed_cycle_outside_a_tick_is_held_to_what_a_person_enrolled(tmp_path):
    loop, _, declaration = _declared(tmp_path)
    _edit(declaration)
    loop.fault(RuntimeError("a pass broke outside any tick"))
    row = loop.log.cycles()[-1]
    assert row["code"] == FROZEN_CHANGED and "G6_E_LOOP_CYCLE_FAILED" in [status["code"] for status in row["statuses"]]


@pytest.mark.parametrize("status", ["classifier", "unmetered", "sealed_breach", "barred"])
def test_a_safety_stop_the_log_would_not_take_stops_the_loop_at_once(tmp_path, monkeypatch, status):
    from g6.self_maintenance.envelope import stop_in_force

    loop, _ = _statused(tmp_path, status, False)

    def refused(row):
        raise OSError("the disk is full")

    monkeypatch.setattr(loop.log, "cycle", refused)
    assert loop.tick()["warning_card"]["code"] == "G6_E_LOOP_CANNOT_RECORD"
    assert stop_in_force(loop.root).code == "G6_E_LOOP_STOPPED" and loop.lost_cycles == 1


# Review 4's classes: unreadable is never a digest, only interpreter caches are left out, a root is checked as given ----

class _NeverSeen(Exception):
    """A failure no handler names."""


def _locked(path: Path):
    import msvcrt

    stream = path.open("r+b", buffering=0)
    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, max(1, path.stat().st_size))
    return stream


@pytest.mark.skipif(os.name != "nt", reason="a byte-range lock and a denying directory handle are Windows's")
@pytest.mark.parametrize("failure", ["a byte-range lock", "a folder that cannot be listed", "a file gone mid-listing",
                                     "an error never seen"])
def test_any_failure_to_read_under_a_root_makes_the_reading_unreadable_never_a_digest(tmp_path, monkeypatch, failure):
    import msvcrt

    from g6.metaharness import loop as loop_module

    root = tmp_path / "judge"
    (root / "data").mkdir(parents=True)
    rule = root / "data" / "rule.txt"
    rule.write_bytes(b"true")
    closing = []
    if failure == "a byte-range lock":
        stream = _locked(rule)
        closing.append(lambda: (msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 4), stream.close()))
    elif failure == "a folder that cannot be listed":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(root / "data"), 0x80000000, 0, None, 3, 0x02000000, None)   # shares nothing
        assert handle != wintypes.HANDLE(-1).value
        closing.append(lambda: kernel.CloseHandle(handle))
    elif failure == "a file gone mid-listing":
        real = os.walk

        def walked(top, **kwargs):
            for here, dirs, files in real(top, **kwargs):
                yield here, dirs, [*files, "gone.txt"] if Path(here).name == "data" else files

        monkeypatch.setattr(loop_module.os, "walk", walked)
    else:
        real_read = Path.read_bytes
        monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(_NeverSeen("never seen"))
                            if self == rule else real_read(self))
    try:
        with pytest.raises(loop_module.FrozenUnreadable):
            loop_module._pinned([root])
    finally:
        for close in closing:
            close()
    monkeypatch.undo()
    assert loop_module._pinned([root])                        # readable again, it pins as any root does


def test_a_reading_that_cannot_be_made_refuses_enrolment_and_every_tick_while_it_stands(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    loop, _, declaration = _declared(tmp_path)
    real = loop_module._frozen_bytes

    def unreadable(path):
        if Path(str(path)) == declaration:
            raise loop_module.FrozenUnreadable(f"{path} cannot be read (PermissionError)")
        return real(path)

    monkeypatch.setattr(loop_module, "_frozen_bytes", unreadable)
    got = loop.tick()
    assert got["warning_card"]["code"] == FROZEN_CHANGED and "could not be read" in got["message"]
    assert loop.mh.adoption_rows() == [] and loop.enrol("Ada")["warning_card"]["code"] == "G6_E_LOOP_JUDGE_UNPINNED"
    loop._frozen_seen = dict(loop.log.enrolled()["frozen"])
    assert "could not be read" in loop._still_frozen()["message"]     # the adoption's own last check refuses it too
    loop._frozen_seen = None
    monkeypatch.undo()
    assert loop.tick()["outcome"] == "adopted"               # once it reads as enrolled, the loop goes on


def test_the_freeze_takes_no_empty_digest_for_a_file_it_cannot_read(tmp_path):
    from g6.metaharness import loop as loop_module

    with pytest.raises(loop_module.FrozenUnreadable):
        loop_module._frozen_bytes(tmp_path / "missing.py")
    assert "file_digest" not in inspect_source(loop_module)


def inspect_source(module) -> str:
    import inspect

    return inspect.getsource(module)


@pytest.mark.parametrize("name, pinned", [("enabled.json", True), ("helper.py", True), ("helper.pyc", True),
                                          ("helper.cpython-312.pyc", False), ("helper.cpython-312.opt-1.pyc", False),
                                          ("helper.pypy39.pyc", False)])
def test_only_a_bytecode_cache_named_for_an_interpreter_is_left_out(tmp_path, name, pinned):
    from g6.metaharness.loop import _pinned

    cache = tmp_path / "__pycache__"
    cache.mkdir()
    before = _pinned([tmp_path])
    (cache / name).write_bytes(b"written")
    assert (_pinned([tmp_path]) != before) is pinned


@pytest.mark.skipif(os.name != "nt", reason="a junction is Windows's")
def test_a_declared_root_is_checked_as_given_before_it_is_resolved(tmp_path):
    from g6.metaharness.loop import _unpinned

    real = tmp_path / "rules"
    real.mkdir()
    _junction(tmp_path / "alias", real)
    domain = domains.WordProblems(tmp_path)
    domain.roots = (tmp_path / "alias", *__import__("g6.metaharness.loop", fromlist=["_roots"])._roots(
        domains.WordProblems(tmp_path)))
    found = _unpinned(domain)
    assert found and found[-1][0] == "G6_E_LOOP_JUDGE_UNPINNED" and "alias" in found[-1][1]

