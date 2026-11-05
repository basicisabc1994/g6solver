# SPDX-License-Identifier: Apache-2.0
"""A frozen file that changes is never adopted past: an adoption is written only if the frozen digests the tick read before
it developed anything are the digests on disk as the adoption is written, checked last with the record already made, and
the record carries them. Each proposer reads a view of its own. FakeLLM is the only model anywhere here."""
from __future__ import annotations

import importlib.util
import json
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

