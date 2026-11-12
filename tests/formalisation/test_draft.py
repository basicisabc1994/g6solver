# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The bounded draft and repair loop, with fakes: no model is called. Caps, consent, and what a repair is given."""
from __future__ import annotations

import json

import pytest

z3 = pytest.importorskip("z3")

from g6.core import cloud_consent as cloud  # noqa: E402
from g6.core.llm import FakeLLM  # noqa: E402
from g6.harness.formalisation import Unsupported  # noqa: E402
from g6.harness.formalisation.draft import Caps, Intent, draft_pair  # noqa: E402
from g6.harness.formalisation.library import LocalLibrary  # noqa: E402
from g6.harness.formalisation.statement import Observation  # noqa: E402
from tests.formalisation._cases import DOMAIN, example  # noqa: E402

SEEN = Observation("returns", "shop.f", (("x", "Int"),), "Int")
CASE = example([3], {"returns": 4}, {"returns": 5})
SECRET = "PLANTED-TASK-TEXT-7f3a"
FRAGMENT = "smt.relation.int_bool.v1"


def reply(relation, **more):
    return json.dumps({"sorts": {"x": "Int", "result": "Int"}, "relation": relation, **more})


def intent(task="f returns x plus one", cases=(CASE,)) -> Intent:
    source = cloud.input_source(task)
    return Intent(requirement_id="R1", task=task, task_sources=(cloud.Source.text(source, "task", task),),
                  cases=tuple(cases), observation=SEEN, domain=DOMAIN)


def local(*replies):
    """A model that runs on this machine: it answers its replies in order, then repeats the last."""
    left = list(replies)
    return FakeLLM(lambda system, user: left.pop(0) if len(left) > 1 else left[0])


class Cloud:
    """A caller G6 does not know: the consent check treats it as a cloud recipient."""

    model = "cloud-double"

    def __init__(self, *replies):
        self.replies, self.received = list(replies), []

    def complete(self, system, user, *, max_tokens=0):
        self.received.append((system, user, max_tokens))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def pair(a, b, **kwargs):
    return draft_pair(intent(), language="smt", fragment=FRAGMENT, callers={"local:a": a, "local:b": b}, **kwargs)


def test_two_right_drafts_are_drafted_with_their_provenance():
    one, two = pair(local(reply("(= result (+ x 1))")), local(reply("(= result (+ 1 x))")))
    assert (one.status, two.status) == ("drafted", "drafted") and (one.family, two.family) == ("local:a", "local:b")
    assert one.statement.text == "(= result (+ x 1))" and one.statement.domain == DOMAIN
    assert (one.attempts, one.repairs, one.calls, one.saw_other) == (1, 0, 1, False) and len(one.prompt_sha256s) == 1
    assert one.common_sources == two.common_sources and len(one.common_sources) == 2   # the task and the examples
    assert one.output_chars > 0 and one.provenance()["family"] == "local:a"


def test_one_candidate_per_family_two_repairs_total():
    wrong = reply("(= result (+ x 2))")
    a, b = local(wrong), local(wrong)
    one, two = pair(a, b)
    assert len(a.calls) + len(b.calls) == 4          # two first drafts and two repairs, never more
    assert (one.repairs, two.repairs) == (2, 0) and (one.attempts, two.attempts) == (3, 1)
    assert one.status == two.status == "drafted" and one.diagnostics and two.diagnostics
    none = pair(local(wrong), local(wrong), caps=Caps(max_repairs_total=0))
    assert [d.attempts for d in none] == [1, 1]


def test_no_regeneration_until_agreement():
    a, b = local(reply("(= result (+ x 1))")), local(reply("(and (> result x) (< result (+ x 2)))"))
    one, two = pair(a, b)
    assert len(a.calls) == len(b.calls) == 1 and one.statement.text != two.statement.text


def test_caps_stop_the_loop(monkeypatch):
    from g6.harness.formalisation import draft

    wrong = reply("(= result (+ x 2))")
    one, two = pair(local(wrong), local(wrong), caps=Caps(max_output_chars=10))
    assert one.status == "drafted" and one.repairs == 0 and "output limit" in one.reason
    assert two.status == "caps_spent" and two.statement is None and two.calls == 0
    import itertools

    clock = itertools.count(0, 50)
    monkeypatch.setattr(draft, "_now", lambda: float(next(clock)))
    late = pair(local(wrong), local(wrong), caps=Caps(max_wall_clock_s=60.0))
    assert [d.status for d in late] == ["caps_spent", "caps_spent"] and all(d.statement is None for d in late)


def test_each_call_asks_for_at_most_the_token_cap():
    from g6.core.consent_callers import CallerWrapper

    asked = []

    class Counting(CallerWrapper):
        def complete(self, system, user, *, max_tokens=2048):
            asked.append(max_tokens)
            return self.inner.complete(system, user, max_tokens=max_tokens)

    pair(Counting(local(reply("(= result (+ x 1))"))), local(reply("(= result (+ x 1))")),
         caps=Caps(max_output_tokens=700))
    assert asked == [700]


def test_parse_success_mints_nothing():
    one, _ = pair(local(reply("(= result (+ x 1))")), local(reply("(= result (+ x 1))")))
    assert not hasattr(one, "outcome") and not hasattr(one, "acceptance") and not hasattr(one, "receipt")
    assert set(one.provenance()) == {"family", "model", "status", "attempts", "repairs", "prompt_sha256s",
                                     "common_sources", "retrieved", "saw_other", "calls", "output_chars", "seconds"}


def test_withheld_without_grant():
    planted = intent(task=f"f adds one. {SECRET}")
    remote = Cloud(reply("(= result (+ x 1))"))
    one, two = draft_pair(planted, language="smt", fragment=FRAGMENT,
                          callers={"cloud": remote, "local:b": local(reply("(= result (+ x 1))"))})
    assert one.status == "withheld" and one.statement is None and remote.received == []
    assert cloud.REQUIRED in one.reason and SECRET not in one.reason
    assert two.status == "drafted"                                   # the model on this machine still drafts


def test_repair_prompt_has_no_other_draft():
    a = local(reply("(= result (+ x 7))"), reply("(= result (+ x 1))"))
    b = local(reply("(= result (- x 99))"), reply("(= result (+ x 1))"))
    one, two = pair(a, b)
    assert (one.repairs, two.repairs) == (1, 1) and one.status == two.status == "drafted"
    repair_a, repair_b = a.calls[1][1], b.calls[1][1]
    assert "(+ x 7)" in repair_a and "does not permit the confirmed outcome" in repair_a
    assert "(- x 99)" not in repair_a and "(+ x 7)" not in repair_b and "(+ x 7)" not in b.calls[0][1]
    assert one.saw_other is False and two.saw_other is False


def test_retrieved_statements_carry_sources_and_are_untrusted(tmp_path):
    library = LocalLibrary(tmp_path / "library.jsonl")
    kept = library.add(described="a function that returns x plus one", text="(= result (+ x 1))", language="smt")
    library.add(described="colour of the sky", text="(= result 0)", language="smt")
    library.add(described="returns x plus one", text="theorem t : True := trivial", language="lean4")
    a = local(reply("(= result (+ x 1))"))
    one, two = pair(a, local(reply("(= result (+ x 1))")), library=library)
    assert one.retrieved == two.retrieved == (kept.content_sha256,) and kept.receipt == ""
    assert "untrusted context" in a.calls[0][1] and kept.source_id in a.calls[0][1] and "(= result 0)" not in a.calls[0][1]
    assert len(one.common_sources) == 3                    # the task, the examples and the retrieved statement
    assert not hasattr(kept, "trust") and not hasattr(kept, "verified")


@pytest.mark.parametrize("text", [
    "(declare-fun f (Int) Int)", "(forall ((z Int)) (= result z))", "result == x + 1", "(= result (g x))",
    "(= result (+ x 1.5))",
])
def test_outside_fragment_reply_refused(text):
    one, _ = pair(local(reply(text)), local(reply("(= result (+ x 1))")))
    assert one.status == "outside_fragment" and one.statement is None and "outside the supported fragment" in one.reason
    other, _ = pair(local(json.dumps({"sorts": {"x": "Real", "result": "Int"}, "relation": "(= result x)"})),
                    local(reply("(= result (+ x 1))")))
    assert other.status == "outside_fragment"


def test_a_reply_that_is_not_the_typed_structure_is_a_model_failure():
    one, _ = pair(local("def f(x): return x + 1"), local(reply("(= result (+ x 1))")))
    assert one.status == "model_failed" and one.statement is None


def test_one_caller_leaves_the_second_draft_unavailable():
    one, two = draft_pair(intent(), language="smt", fragment=FRAGMENT,
                          callers={"local:a": local(reply("(= result (+ x 1))"))})
    assert one.status == "drafted" and (two.status, two.statement, two.family) == ("unavailable", None, "")
    none = draft_pair(intent(), language="smt", fragment=FRAGMENT, callers={})
    assert [d.status for d in none] == ["unavailable", "unavailable"]


def test_unsupported_language_or_fragment_drafts_nothing():
    for language, fragment in (("lean4", FRAGMENT), ("smt", "smt.other.v1"), ("z3_python", FRAGMENT),
                               ("curry_howard", "curry_howard.v1")):
        with pytest.raises(Unsupported, match="G6_E_FORMALISATION_UNSUPPORTED"):
            draft_pair(intent(), language=language, fragment=fragment, callers={})