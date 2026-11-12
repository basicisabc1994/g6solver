# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/freeze-holds: its sixteen findings as the reviewer reproduced them, with the reviewer's support code
(the review's own temporary folder aside: pytest's serves here). FakeLLM is the only model anywhere here."""
from __future__ import annotations

import importlib
import importlib.util
import os
import struct
import subprocess
import sys
from collections import OrderedDict, namedtuple
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM, LLMError
from g6.core.vault import AccessDenied
from g6.harness import Check
from g6.metaharness import shadow
from g6.metaharness.domain import PAID_MODEL, Cut, Judge, answer_identity
from g6.metaharness.loop import LoopSettings, ShadowLoop, _Halted, _judge_digest
from g6.metaharness.method import Method
from g6.metaharness.schema import SuiteCase
from g6.metaharness.shadow import FROZEN_CHANGED
from g6.self_maintenance.pacing import ProviderWindow
from g6.self_maintenance.usage import MODEL_REFUSED


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


domains = load(Path(__file__).with_name("_loop_domains.py"), "frz2_domains")
START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)
SETTINGS = LoopSettings(
    windows=(ProviderWindow("claude", 1_000_000, START),), audit_per_tick=0,
    method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12,
                  reflect_calls=1, use_recovery=False, use_reflection=False),
)


def enrolled(folder, *, domain=None, proposer=None, domain_file=None):
    domain = domain or domains.WordProblems(folder)
    loop = ShadowLoop(domain, folder / "loop", SETTINGS,
                      proposer=proposer or domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock(), domain_file=domain_file)
    reply = loop.enrol("Review owner")
    assert reply["completion_state"] == "qualified-draft", reply
    return loop, domain


def recommendation(loop):
    loop.requests.open(10_000)
    try:
        profile = loop.mh._head()[1].model_dump(mode="json")
        profile["prompt"] = domains.WORKING
        proposal = loop.mh.candidate(profile, proposer="auto:review")["proposal"]
        report = loop.mh.evaluate(proposal)["report"]
        decision = loop.mh.decide(report, proposal)["decision"]
    finally:
        loop.requests.close()
    assert decision.verdict == "recommend"
    return decision


# Findings 1-3: the judge ------------------------------------------------------------------------------------------------

def test_imported_scalar_judging_rule_is_frozen(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    rules = tmp_path / "scalar_rules.py"
    rules.write_text('ACCEPTED = "yes"\n', encoding="utf-8")
    judge_file = tmp_path / "judge.py"
    judge_file.write_text(
        "from scalar_rules import ACCEPTED\n"
        "def verdict(output, expected):\n"
        "    return output == ACCEPTED\n", encoding="utf-8")
    module = load(judge_file, "frz2_scalar_judge")
    before = _judge_digest(Judge(module.verdict, "independent"))
    assert module.verdict("yes", None)
    rules.write_text('ACCEPTED = "different answer"\n', encoding="utf-8")
    import scalar_rules
    importlib.reload(scalar_rules)
    fresh = load(judge_file, "frz2_scalar_judge")
    assert not fresh.verdict("yes", None)
    assert _judge_digest(Judge(fresh.verdict, "independent")) != before, "changed imported scalar rule kept the judge digest"


def test_helper_imported_inside_verdict_is_frozen(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    rules = tmp_path / "local_rules.py"
    rules.write_text('def accepts(output):\n    return output == "yes"\n', encoding="utf-8")
    judge_file = tmp_path / "judge.py"
    judge_file.write_text(
        "def verdict(output, expected):\n"
        "    from local_rules import accepts\n"
        "    return accepts(output)\n", encoding="utf-8")
    module = load(judge_file, "frz2_local_judge")
    before = _judge_digest(Judge(module.verdict, "independent"))
    assert module.verdict("yes", None)  # helper is loaded after the first reading
    rules.write_text('def accepts(output):\n    return output == "different answer"\n', encoding="utf-8")
    import local_rules
    importlib.reload(local_rules)
    assert not module.verdict("yes", None)
    assert _judge_digest(Judge(module.verdict, "independent")) != before, "changed local-import helper kept the judge digest"


def test_namespace_package_does_not_cut_off_judge_graph(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    package = tmp_path / "frz2_namespace"
    package.mkdir()
    rules = package / "rules.py"
    rules.write_text('def accepts(output):\n    return output == "yes"\n', encoding="utf-8")
    judge_file = tmp_path / "judge.py"
    judge_file.write_text(
        "import frz2_namespace.rules\n"
        "def verdict(output, expected):\n"
        "    return frz2_namespace.rules.accepts(output)\n", encoding="utf-8")
    module = load(judge_file, "frz2_namespace_judge")
    before = _judge_digest(Judge(module.verdict, "independent"))
    assert module.verdict("yes", None)
    rules.write_text('def accepts(output):\n    return output == "different answer"\n', encoding="utf-8")
    import frz2_namespace.rules
    importlib.reload(frz2_namespace.rules)
    assert not module.verdict("yes", None)
    assert _judge_digest(Judge(module.verdict, "independent")) != before, "namespace package hid the changed judging helper"


# Findings 4-7, 11-14: the answers ---------------------------------------------------------------------------------------

class SubclassAnswer(str):
    def __new__(cls, text, valid=True):
        obj = super().__new__(cls, text)
        obj.valid = valid
        return obj


def subclass_verdict(output, expected):
    return expected.valid and domains.GSM.oracle(output, str(expected))


def test_builtin_subclass_answer_state_cannot_change_before_adoption(tmp_path, monkeypatch):
    """Changed by repair 3 on purpose: a subclass is outside the admitted answers, so its state never reaches a judge. The
    reviewer's form enrolled one and changed its state before the adoption; enrolment now refuses it, by case id."""
    domain = domains.WordProblems(tmp_path)
    domain.pool = [replace(case, expected=SubclassAnswer(case.expected)) for case in domain.pool]
    domain.judge = lambda: Judge(subclass_verdict, "independent")
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    reply = loop.enrol("Review owner")
    assert reply["warning_card"]["code"] == "G6_E_LOOP_ANSWER_UNIDENTIFIABLE" and loop.log.enrolled() is None
    assert loop.tick()["completion_state"] == "blocked-escalated" and loop.mh is None


def test_nan_payloads_do_not_share_an_answer_identity():
    left = struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[0]
    right = struct.unpack(">d", bytes.fromhex("7ff8000000000002"))[0]
    # A judge can distinguish these without rendering either value.
    assert struct.pack(">d", left) != struct.pack(">d", right)
    assert answer_identity(left) != answer_identity(right), "different NaN payloads share an answer identity"


def test_pickle_fallback_is_stable_across_hash_seeds():
    code = (
        "from types import SimpleNamespace\n"
        "from g6.metaharness.domain import answer_identity\n"
        'value = SimpleNamespace(labels={"alpha", "beta", "gamma", "delta", "epsilon"})\n'
        "print(answer_identity(value))\n"
    )
    results = [subprocess.run(
        [sys.executable, "-B", "-c", code],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip() for seed in ("1", "2")]
    assert results[0] == results[1], "the same pickled answer changed identity across hash seeds"


class UnpicklableAnswer:
    def __init__(self, value):
        self.value = value

    def __reduce_ex__(self, protocol):
        raise TypeError("not serializable")

    def __eq__(self, other):
        return type(other) is type(self) and self.value == other.value


def test_repr_fallback_does_not_pin_object_addresses():
    left, right = UnpicklableAnswer(42), UnpicklableAnswer(42)
    assert left == right
    assert answer_identity(left) == answer_identity(right), "equal unpicklable answers have address-dependent identities"


def test_distinct_namedtuple_types_do_not_share_a_cut_identity():
    first_type = namedtuple("Answer", "accepted")
    second_type = namedtuple("Answer", "rejected")
    first, second = first_type(1), second_type(1)
    assert hasattr(first, "accepted") and not hasattr(second, "accepted")
    left = Cut((), (), (SuiteCase("case", "input", first),), ())
    right = Cut((), (), (SuiteCase("case", "input", second),), ())
    assert left.digest() != right.digest(), "different answer types with the same qualified name share a cut digest"


def test_ordered_mapping_answers_keep_their_semantic_order():
    first = OrderedDict([("accept", 1), ("reject", 2)])
    second = OrderedDict(reversed(list(first.items())))
    assert first != second
    assert next(iter(first)) != next(iter(second))
    left = Cut((), (), (SuiteCase("case", "input", first),), ())
    right = Cut((), (), (SuiteCase("case", "input", second),), ())
    assert left.digest() != right.digest(), "unequal ordered answers share a cut digest"


@dataclass
class DataAnswer:
    labels: set


def test_cut_can_digest_answers_supported_by_answer_identity():
    answers = [{1, "one"}, {1: "one", "1": "string"}, DataAnswer({"one"}), complex(1, 2)]
    failures = []
    for answer in answers:
        assert len(answer_identity(answer)) == 64
        try:
            Cut((), (), (SuiteCase("case", "input", answer),), ()).digest()
        except (TypeError, ValueError) as exc:
            failures.append(f"{type(answer).__name__}: {exc}")
    assert not failures, f"freeze still requires JSON answers: {failures}"


def test_repr_fallback_detects_an_existing_answers_changed_value():
    answer = UnpicklableAnswer(42)
    before = answer_identity(answer)
    answer.value = 43
    assert answer.value != 42
    assert answer_identity(answer) != before, "mutating an unpicklable answer kept its address-based identity"


# Findings 8, 15: one rule codes a cycle -----------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["budget", "sealed_breach", "unmetered", "classifier", "barred"])
def test_budget_cutoff_does_not_hide_a_frozen_change(tmp_path, status):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled\n", encoding="utf-8")
    calls = []

    def propose(system, user):
        calls.append(True)
        declaration.write_text("# changed during development\n", encoding="utf-8")
        if status == "budget":
            # Exhaust the active reservation at the next request boundary.
            loop.requests.limit = loop.requests.sent
        elif status == "sealed_breach":
            try:
                domain.sealed_file.read_text(encoding="utf-8")
            except AccessDenied:
                pass
        elif status == "unmetered":
            # Inject the receipt a caller without a request boundary leaves.
            loop.requests.unseen = True
        elif status == "classifier":
            raise LLMError(f"{MODEL_REFUSED}: permission denied")
        elif status == "barred":
            raise _Halted(PAID_MODEL, "a fake caller switched to a paid backend")
        return '{"prompt": "Work through the problem step by step."}'

    loop, domain = enrolled(tmp_path, proposer=FakeLLM(propose), domain_file=declaration)
    loop.tick()
    assert calls and "domain_file" in loop._changed()
    assert not loop.mh.adoption_rows()
    assert loop.log.cycles()[-1]["code"] == FROZEN_CHANGED, f"{status} status hid the cycle frozen change"


def test_harness_standard_refusal_does_not_skip_cycle_freeze_rule(tmp_path, monkeypatch):
    judge_dir = tmp_path / "judges"
    judge_dir.mkdir()
    source = judge_dir / "judge.py"
    source.write_text(
        "from frz2_domains import GSM\n"
        "def verdict(output, expected):\n"
        "    return GSM.oracle(output, expected)\n", encoding="utf-8")
    module = load(source, "frz2_early_refusal_judge")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, "independent")
    loop, _ = enrolled(tmp_path, domain=domain)
    original = shadow.ShadowHarness.adopt
    changed = []

    def editor_then_adopt(self, *args, **kwargs):
        # A save after the real gate recommended, before adopt reads the workflow.
        source.write_text("def verdict(output, expected):\n    return False\n", encoding="utf-8")
        changed.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(shadow.ShadowHarness, "adopt", editor_then_adopt)
    reply = loop.tick()
    assert changed and "evaluator" in loop._changed()
    assert not loop.mh.adoption_rows()
    assert reply["warning_card"]["code"] == shadow.FROZEN_CHANGED, "early standard refusal bypassed the cycle frozen rule"


# Findings 9, 10: adoption's last check is the harness's own -------------------------------------------------------------

def test_public_adopt_does_not_trust_a_callers_false_freeze_claim(tmp_path):
    declaration = tmp_path / "domain.py"
    declaration.write_text("# enrolled\n", encoding="utf-8")
    loop, _ = enrolled(tmp_path, domain_file=declaration)
    reading = loop._frozen_now()[0]
    decision = recommendation(loop)
    declaration.write_text("# changed after measurement\n", encoding="utf-8")
    assert "domain_file" in loop._changed(against=reading)
    reply = loop.mh.adopt(decision, frozen=reading, last_check=lambda: None)
    assert not loop.mh.adoption_rows(), "public adopt trusted a caller claiming changed files were unchanged"
    assert reply["warning_card"]["code"] == FROZEN_CHANGED


def test_unknown_empty_last_check_status_is_not_success(tmp_path):
    loop, _ = enrolled(tmp_path)
    decision = recommendation(loop)
    result = loop.mh.adopt(decision, frozen=loop._frozen_now()[0], last_check=lambda: {})
    assert not loop.mh.adoption_rows(), "empty unrecognized last-check result was treated as success"
    assert result["completion_state"] == "blocked-escalated"


# Finding 16: a check is pinned by its module's file -----------------------------------------------------------------------

def test_check_module_globals_are_part_of_the_frozen_evaluator(tmp_path, monkeypatch):
    check_dir = tmp_path / "checks"
    check_dir.mkdir()
    source = check_dir / "frz2_criterion.py"
    function = "def accepts(output):\n    return ENABLED and bool(output.strip())\n"
    source.write_text("ENABLED = True\n" + function, encoding="utf-8")
    monkeypatch.syspath_prepend(str(check_dir))
    module = load(source, "frz2_criterion")
    extra = Check("the answer passes the external criterion", module.accepts, check_id="external-criterion")
    domain = domains.WordProblems(tmp_path)
    execution = domain.execution
    domain.execution = lambda: replace(execution(), checks=(*execution().checks, extra))
    loop, _ = enrolled(tmp_path, domain=domain)
    before = extra.code_sha()
    original = shadow._measured
    changed = []

    def editor(report):
        source.write_text("ENABLED = False\n" + function, encoding="utf-8")
        importlib.reload(module)
        changed.append(True)
        return original(report)

    monkeypatch.setattr(shadow, "_measured", editor)
    reply = loop.tick()
    assert changed and not extra.run("Answer: 42")
    assert extra.code_sha() == before
    assert not loop.mh.adoption_rows(), "adopted after an external acceptance check changed behavior"
    assert reply["warning_card"]["code"] == shadow.FROZEN_CHANGED
