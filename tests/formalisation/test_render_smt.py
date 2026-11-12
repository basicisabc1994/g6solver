# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Check 4 for the SMT fragment: an exhaustive, deterministic renderer of the checked artefact itself."""
from __future__ import annotations

import dataclasses
import inspect
import itertools

import pytest

from g6.harness.formalisation import FRAGMENTS, render_smt, smt_ir
from g6.harness.formalisation.checks import check_rendering
from g6.harness.formalisation.render import MAX_FORMAL_CHARS, MAX_WORDS_CHARS, RENDERERS, Rendering, Unrenderable, render
from g6.harness.formalisation.statement import Observation
from tests.formalisation._cases import relation
from tests.formalisation.test_smt_ir import SAMPLES, SORTS

INPUTS = (("x", "Int"), ("y", "Int"), ("p", "Bool"))


def test_every_constructor_rendered():
    """The inventory: a constructor added to the grammar without a render case fails here."""
    assert set(render_smt.CASES) == set(smt_ir.CONSTRUCTORS)
    for name, text in SAMPLES.items():
        shown = render(relation(text, inputs=INPUTS, domain=()))
        assert shown.renderer == "smt.words.v1" and shown.words.isascii(), name


def test_a_term_outside_the_inventory_is_unrenderable():
    @dataclasses.dataclass(frozen=True)
    class Power:
        base: object
        exponent: object

    with pytest.raises(Unrenderable, match="Power"):
        render_smt.words_of(Power(smt_ir.Var("x", "Int"), smt_ir.IntLit(2)))


@pytest.mark.parametrize("a, b", [
    ("(>= result x)", "(> result x)"), ("(= result (+ x 1))", "(= result (- x 1))"), ("(= result x)", "(= x result)"),
    ("(= result (+ x (+ y 1)))", "(= result (+ (+ x y) 1))"), ("(= result (+ x y 1))", "(= result (+ x (+ y 1)))"),
    ("(= result (- x y))", "(= result (- y x))"), ("(=> p (= result 0))", "(=> (not p) (= result 0))"),
    ("(= result (div x 2))", "(= result (mod x 2))"), ("(or p (and (> x 0) (= result 0)))", "(and (or p (> x 0)) (= result 0))"),
    ("(= result (ite p x y))", "(= result (ite p y x))"), ("(=> p (= result 0))", "(or (not p) (= result 0))"),
    ("(= result (- x))", "(= result (- 0 x))"), ("(distinct result x)", "(not (= result x))"),
])
def test_distinct_statements_render_distinctly(a, b):
    one, two = (render(relation(text, inputs=INPUTS, domain=())) for text in (a, b))
    assert one.words != two.words and one.sha256 != two.sha256


def test_a_changed_domain_bound_renders_distinctly():
    assert render(relation("(= result x)", domain=("(>= x 0)",))).words != render(
        relation("(= result x)", domain=("(> x 0)",))).words
    assert render(relation("(= result x)", domain=("(>= x 0)",))).words != render(
        relation("(= result x)", domain=(), assumptions=("(>= x 0)",))).words


def test_no_two_sample_terms_or_their_parts_share_words():
    """Over every sub-term of the samples: the words are the same only when the canonical text is."""
    terms = set()
    for text in SAMPLES.values():
        todo = [smt_ir.read(text, SORTS)]
        while todo:
            term = todo.pop()
            terms.add(term)
            todo.extend(smt_ir.children(term))
    for a, b in itertools.combinations(sorted(terms, key=smt_ir.write), 2):
        assert (render_smt.words_of(a) == render_smt.words_of(b)) == (smt_ir.write(a) == smt_ir.write(b))


def test_words_from_statement_only():
    """The renderer is given the statement and nothing else: no task text, no question, no model paraphrase."""
    assert list(inspect.signature(render).parameters) == ["statement"]
    for made in RENDERERS.values():
        assert list(inspect.signature(made).parameters) == ["statement"]
    one = relation("(= result (+ x 1))")
    assert render(one) == render(dataclasses.replace(one)) and render(one).words == render(one).words
    assert "the task" not in render(one).words


def test_formal_text_and_digest_beside_words():
    one = relation("(=> (> x 5) (= result 0))", raises_when="(< x 0)", error="ValueError", domain=("(<= x 10)",),
                   entails={"small": "(< result 5)"})
    shown = render(one)
    assert shown.formal_text == one.text and shown.statement_sha256 == one.sha256
    assert shown.definitions_sha256 == one.definitions_sha256
    lines = dict(line.split(": ", 1) for line in shown.words.splitlines())
    assert list(lines) == ["Function", "Observed", "Setup", "Inputs covered", "Assuming", "Rule", "Raises",
                           "Also required (small)", "Guards", "Not covered"]
    assert lines["Function"] == "shop.f('x': whole number)"
    assert lines["Inputs covered"] == "'x' is at most 10"
    assert lines["Rule"] == ("for every covered input that does not raise, every returned 'result' satisfies: "
                             "if ('x' is greater than 5) then ('result' equals 0)")
    assert lines["Raises"] == "ValueError exactly when 'x' is less than 0"
    assert lines["Guards"] == "'x' is greater than 5" and "effects on arguments" in lines["Not covered"]
    bare = dict(line.split(": ", 1) for line in render(relation("(= result x)", domain=())).words.splitlines())
    assert bare["Inputs covered"] == "every value of the declared inputs" and bare["Assuming"] == "nothing further"
    assert bare["Raises"] == "not covered by this statement" and bare["Guards"] == "none"
    assert "errors raised" in bare["Not covered"]


def test_rendering_digest_covers_all_five_fields():
    shown = render(relation("(= result (+ x 1))"))
    for name in ("words", "formal_text", "statement_sha256", "definitions_sha256", "renderer"):
        assert dataclasses.replace(shown, **{name: getattr(shown, name) + "x"}).sha256 != shown.sha256
    assert {f.name for f in dataclasses.fields(Rendering)} == {"words", "formal_text", "statement_sha256",
                                                               "definitions_sha256", "renderer"}


def test_unsupported_is_unrenderable():
    one = relation("(= result (+ x 1))")
    for change in ({"text": "(forall ((z Int)) (> z 0))"}, {"fragment": "smt.other.v1"}, {"language": "lean4"},
                   {"adapter": ""}, {"text": "(=  result x)"}):
        other = dataclasses.replace(one, **change)
        with pytest.raises(Unrenderable):
            render(other)
        outcome, shown = check_rendering(other)
        assert outcome.check == "rendering" and outcome.status == "unsupported" and shown is None
    outcome, shown = check_rendering(one)
    assert outcome.status == "passed" and shown == render(one)


def test_too_large_to_show_is_unrenderable():
    """A statement too long for the dialog is refused whole; it is never shown in part."""
    def listed(symbol, count):   # nested, because one "or" takes at most 64 terms
        return "(or " + " ".join("(or " + " ".join(f"(= {symbol} {n})" for n in range(k, k + 50)) + ")"
                                 for k in range(0, count, 50)) + ")"

    big = relation("(= result x)", domain=(listed("x", 200),))   # a domain of 200 listed values
    with pytest.raises(Unrenderable, match="too large to show"):
        render(big)
    assert check_rendering(big)[0].status == "unsupported"
    long_rule = listed("result", 200)
    assert len(long_rule) > MAX_FORMAL_CHARS and MAX_WORDS_CHARS >= MAX_FORMAL_CHARS
    with pytest.raises(Unrenderable, match="too large to show"):
        render(relation(long_rule, domain=()))
    fifty = "(or " + " ".join(f"(= x {n})" for n in range(50)) + ")"
    shown = dict(line.split(": ", 1) for line in render(relation("(= result x)", domain=(fifty,))).words.splitlines())
    assert shown["Inputs covered"].count(" or ") == 49


@pytest.mark.parametrize("change", [
    {"entry_point": "shop.f\nStatement SHA-256: 00"}, {"entry_point": "shop f"}, {"entry_point": ""},
    {"error": "Value\x07Error"}, {"setup": "anything else"},
])
def test_labels_cannot_be_forged_through_names(change):
    one = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError")
    seen = dataclasses.replace(one.observation, **change)
    with pytest.raises(Unrenderable):
        render(dataclasses.replace(one, observation=seen))
    forged = dataclasses.replace(one, definitions=(*one.definitions, ("entails_Statement SHA-256:", "(> x 0)")))
    with pytest.raises(Unrenderable):
        render(forged)


def test_the_fragment_names_its_renderer():
    assert FRAGMENTS["smt.relation.int_bool.v1"]["renderer"] == "smt.words.v1"
    assert set(RENDERERS) == {name for name, entry in FRAGMENTS.items() if entry["renderer"]}
    assert Observation("returns", "shop.f", (("x", "Int"),), "Int").setup == ""