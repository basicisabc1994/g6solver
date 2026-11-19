# SPDX-License-Identifier: Apache-2.0
"""Synthetic regressions for total decoding, container fragments and path folding."""
import json
import random
from urllib.parse import quote

import pytest

from g6.metaharness.schema import SuiteCase
from g6.recursion import contamination_gate as gate


def clean_screen(held, training=(), outputs=()):
    return gate.screen(training, (held,), canaries=("a" * 32,), model_outputs=outputs,
                       membership={held.case_id: gate.MembershipProbe(1, 1, (1, 1), (1, 1))})


def malformed_corpus():
    seeds = ["&#" + "9" * 4301 + ";", "&#x" + "f" * 4301 + ";",
             r"\U" + "9" * 4301, r"\Uffffffff", "\ud800", "\udfff",
             "[" * 2000 + '"text"' + "]" * 2000, r"\N{bad}", "%FF%C3%A9", "&amp;" * 8]
    rng = random.Random(301)
    return seeds + [" | ".join(rng.choices(seeds, k=3)) for _ in range(30)]


@pytest.mark.parametrize("raw", malformed_corpus(), ids=lambda s: ascii(s[:24]))
def test_followup3_canonical_views_are_total_on_malformed_corpus(raw):
    views = gate.canonical_views(raw)
    assert len(views) == 3 and all(isinstance(v, str) for v in views)
    assert gate.canonical_views(views[0]) == views


def test_followup3_entity_failure_retains_region_and_decodes_sibling(monkeypatch):
    original = gate.html.unescape

    def decode(region):
        if "999" in region:
            raise ValueError("bad entity")
        return original(region)

    monkeypatch.setattr(gate.html, "unescape", decode)
    assert gate.canonical_views("&#999; &eacute;")[0] == "&#999; é"


CONTAINERS = {
    "json_list": lambda fragments: json.dumps(fragments),
    "json_dict": lambda fragments: json.dumps({str(i): s for i, s in enumerate(fragments)}),
    "python_list": repr,
    "python_tuple": lambda fragments: repr(tuple(fragments)),
    "python_dict": lambda fragments: repr({str(i): s for i, s in enumerate(fragments)}),
    "nested_json": lambda fragments: json.dumps([json.dumps(fragments)]),
    "nested_python": lambda fragments: repr([repr(fragments)]),
    "nested_json_leaves": lambda fragments: json.dumps([json.dumps([s]) for s in fragments]),
    "nested_python_leaves": lambda fragments: repr([repr([s]) for s in fragments]),
}


@pytest.mark.parametrize("serialise", CONTAINERS.values(), ids=CONTAINERS)
@pytest.mark.parametrize("source", ["training", "output", "held_out"])
def test_followup3_serialised_fragment_passages_are_excluded(serialise, source):
    words = [f"word{i}" for i in range(13)]
    encoded, passage = serialise(words), " ".join(words)
    held = SuiteCase("held", "private", encoded if source == "held_out" else passage)
    training = ((SuiteCase("train", "other", encoded if source == "training" else passage),)
                if source != "output" else ())
    result = clean_screen(held, training, (encoded,) if source == "output" else ())
    assert result.ngram_excluded == result.excluded_count == 1


@pytest.mark.parametrize("serialise", CONTAINERS.values(), ids=CONTAINERS)
@pytest.mark.parametrize("source", ["training", "output"])
def test_followup3_serialised_fragment_canaries_void(serialise, source):
    encoded = serialise(["a" * 16, "a" * 16])
    result = clean_screen(SuiteCase("held", "private", "secret"),
                          (SuiteCase("train", encoded, "other"),) if source == "training" else (),
                          (encoded,) if source == "output" else ())
    assert result.voided and result.canary_hits == 1


@pytest.mark.parametrize("serialise", [str, json.dumps, repr, quote], ids=["plain", "json", "repr", "url"])
@pytest.mark.parametrize("side", ["training", "held_out"])
@pytest.mark.parametrize("identity", [False, True], ids=["thirteen_tokens", "identity"])
def test_followup3_windows_path_casefold_applies_after_backslash(serialise, side, identity):
    passage = r"C:\Work alpha beta gamma delta epsilon zeta eta theta iota"
    plain = SuiteCase("plain", passage.lower() if identity else "other", passage.lower())
    encoded = SuiteCase("encoded", serialise(passage) if identity else "private", serialise(passage))
    held, train = (plain, encoded) if side == "training" else (encoded, plain)
    result = clean_screen(held, (train,))
    assert (result.identity_excluded if identity else result.ngram_excluded) == 1


@pytest.mark.parametrize("kind", ["depth", "nodes", "size"])
def test_followup3_container_limit_keeps_plain_text_without_refusal(kind):
    text = ('[' * 34 + '"leaf"' + ']' * 34 if kind == "depth" else
            json.dumps([None] * 16384) if kind == "nodes" else
            json.dumps(["x" * 262145]))
    result = clean_screen(SuiteCase("held", "private", "secret"), outputs=(text,))
    assert not result.voided and result.serialization_limited
    assert result.to_record() == {} and "serialization" not in repr(result)


def test_followup3_input_identity_does_not_match_one_container_leaf():
    held = SuiteCase("held", json.dumps(["shared", "held"]), "secret")
    train = SuiteCase("train", json.dumps(["shared", "train"]), "other")
    assert clean_screen(held, (train,)).identity_excluded == 0


def test_followup3_casefold_and_nfkc_settle_for_composed_letters(monkeypatch):
    normalize = gate.unicodedata.normalize
    calls = 0

    def bounded_normalize(form, text):
        nonlocal calls
        calls += 1
        if calls > 100:
            pytest.fail("canonical views did not settle")
        return normalize(form, text)

    monkeypatch.setattr(gate.unicodedata, "normalize", bounded_normalize)
    assert gate.canonical_views("\u01f0") == ("\u01f0",) * 3
