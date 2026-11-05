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

