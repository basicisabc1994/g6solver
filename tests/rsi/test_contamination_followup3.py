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
def test_followup3_former_container_bounds_do_not_limit_views(kind):
    text = ('[' * 34 + '"leaf"' + ']' * 34 if kind == "depth" else
            json.dumps([None] * 16384) if kind == "nodes" else
            json.dumps(["x" * 262145]))
    result = clean_screen(SuiteCase("held", "private", "secret"), outputs=(text,))
    assert not result.voided and not result.serialization_limited
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


WRAPPERS = {
    "plain": lambda s: s,
    "prefix": lambda s: "Result: " + s,
    "diagnostics": lambda s: s + "\nFinished successfully",
    "fence": lambda s: "```json\n" + s + "\n```",
}


@pytest.mark.parametrize("literal", ["[]", "{}", "()", "'code()'"])
@pytest.mark.parametrize("serialise", [json.dumps, repr])
@pytest.mark.parametrize("wrap", WRAPPERS.values(), ids=WRAPPERS)
@pytest.mark.parametrize("source", ["training", "output", "held_out"])
def test_structure_free_passage_matrix(literal, serialise, wrap, source):
    parts = ["alpha", "beta", "gamma", literal, "delta", "epsilon", "zeta", "eta",
             "theta", "iota", "kappa", "lambda", "mu", "nu"]
    encoded, passage = wrap(serialise(parts)), " ".join(parts)
    held = SuiteCase("held", "private", encoded if source == "held_out" else passage)
    training = ((SuiteCase("train", "other", encoded if source == "training" else passage),)
                if source != "output" else ())
    assert clean_screen(held, training, (encoded,) if source == "output" else ()).ngram_excluded == 1


@pytest.mark.parametrize("source", ["training", "output", "held_out"])
@pytest.mark.parametrize("serialise", [json.dumps, repr])
def test_review_r4_literal_empty_list_leaf(source, serialise):
    parts = ["alpha", "beta", "gamma", "[]", "delta", "epsilon", "zeta", "eta",
             "theta", "iota", "kappa", "lambda"]
    encoded, passage = serialise(parts), " ".join(parts)
    held = SuiteCase("held", "private", encoded if source == "held_out" else passage)
    training = ((SuiteCase("train", "other", encoded if source == "training" else passage),)
                if source != "output" else ())
    assert clean_screen(held, training, (encoded,) if source == "output" else ()).ngram_excluded == 1


def test_word_view_uses_unicode_letters_and_numbers_only():
    assert gate.comparison_views("α_β,中:Ⅻ ² 'code()'")[3:] == (
        "α β 中 xii 2 code", "α β 中 xii 2 code", "αβ中xii2code")


def test_punctuation_only_differences_add_one_exclusion():
    words = [f"word{i}" for i in range(13)]
    held = SuiteCase("held", "private", ":".join(words))
    assert clean_screen(held, (SuiteCase("train", "other", " ".join(words)),)).ngram_excluded == 1


def test_small_gap_canary_split_across_dict_values_with_keys():
    encoded = json.dumps({"0": "a" * 16, "1": "a" * 16})
    result = clean_screen(SuiteCase("held", "private", "secret"), outputs=(encoded,))
    assert result.voided and result.canary_hits == 1


@pytest.mark.parametrize("side", ["training", "output", "held_out"])
def test_small_gap_passage_one_word_per_dict_value(side):
    words = [f"term{i}" for i in range(13)]
    encoded = json.dumps({str(i): word for i, word in enumerate(words)})
    held = SuiteCase("held", "private", encoded if side == "held_out" else " ".join(words))
    training = () if side == "output" else (SuiteCase("train", "other", encoded if side == "training"
                                                  else " ".join(words)),)
    assert clean_screen(held, training, (encoded,) if side == "output" else ()).ngram_excluded == 1


@pytest.mark.parametrize("side", ["training", "output", "held_out"])
def test_small_gap_r4_literal_list_all_three_sides(side):
    parts = ["alpha", "beta", "gamma", "[]", "delta", "epsilon", "zeta", "eta",
             "theta", "iota", "kappa", "lambda"]
    encoded = json.dumps(parts)
    held = SuiteCase("held", "private", encoded if side == "held_out" else " ".join(parts))
    training = () if side == "output" else (SuiteCase("train", "other", encoded if side == "training"
                                                  else " ".join(parts)),)
    assert clean_screen(held, training, (encoded,) if side == "output" else ()).ngram_excluded == 1


@pytest.mark.parametrize("gap, expected", [(0, 1), (2, 1), (3, 0)])
def test_small_gap_passage_three_extra_tokens_do_not_match(gap, expected):
    words = [f"term{i}" for i in range(13)]
    candidate = (" " + "noise " * gap).join(words)
    held = SuiteCase("held", "private", " ".join(words))
    assert clean_screen(held, outputs=(candidate,)).ngram_excluded == expected


@pytest.mark.parametrize("size, gap, expected", [(7, 1, ()), (8, 24, (0,)), (8, 25, ())])
def test_small_gap_marker_piece_of_seven_characters_does_not_match(size, gap, expected):
    marker = "0123456789abcdef0123456789abcdef"
    candidate = marker[:size] + "z" * gap + marker[size:]
    assert gate.canary_matches((marker,), (candidate,)) == expected


def test_small_gap_passage_tokens_are_maximal_runs():
    assert gate._passage_tokens('[] ["alpha" _,:beta`gamma\'\u00b2\u4e2d') == (
        "[]", "[", "alpha", "_", "beta", "gamma", "\u00b2\u4e2d")


@pytest.mark.parametrize("kind", ["marker", "passage"])
def test_small_gap_matching_bound_refuses_incomplete_scan(monkeypatch, kind):
    monkeypatch.setattr(gate, "_MAX_MATCH_STEPS", 10, raising=False)
    with pytest.raises(gate.ContaminationError, match="SCREEN_LIMIT"):
        if kind == "marker":
            gate.canary_matches(("a" * 32,), ("unrelated content",))
        else:
            clean_screen(SuiteCase("held", "private", " ".join(f"term{i}" for i in range(13))),
                         outputs=("unrelated content",))


UNRELATED_ENGLISH_PAIRS = (
    ("The gardener planted beans beside the fence. Rain softened the soil before she returned with a watering can.",
     "A violinist rehearsed quietly upstairs. The conductor had asked everyone to arrive early for the afternoon concert."),
    ("Our train reached the mountain station before dawn. A porter carried two suitcases across the empty platform.",
     "The baker mixed cinnamon into the dough. Warm rolls were ready when the first customers entered his shop."),
    ("Children searched for shells along the beach. Their teacher explained why the tide leaves patterns in the sand.",
     "An accountant checked several receipts after lunch. She found a missing payment and updated the monthly ledger."),
    ("The library opened a room for local history. Visitors could examine old maps beneath the wide reading lamps.",
     "A cyclist repaired his punctured tyre at home. Tomorrow he hoped to ride through the hills before breakfast."),
    ("Sparrows gathered around the fountain at noon. A caretaker swept fallen leaves from the stone path nearby.",
     "The tailor measured a sleeve twice. Blue thread and silver buttons lay beside the unfinished jacket on her table."),
    ("Two hikers followed a narrow track through ferns. They stopped at a creek to refill their bottles.",
     "The cinema replaced its worn velvet curtains. Staff tested the projector before welcoming guests to the evening screening."),
    ("A potter shaped a bowl from wet clay. She smoothed its rim carefully while the wheel turned.",
     "Passengers watched cargo ships from the ferry deck. A cold breeze carried the distant sound of harbour bells."),
    ("The school choir practised a winter song. Parents waited outside while the music teacher arranged another rehearsal.",
     "An orchard worker sorted apples into wooden crates. The smaller fruit would be pressed into juice the following morning."),
    ("Fog covered the valley until midmorning. From the lookout we could barely see the river beneath the bridge.",
     "The mechanic listened to a rattling engine. A loose bracket was making contact with the metal cover."),
    ("My neighbour knitted a green scarf for her nephew. She chose thick wool because his winters were very cold.",
     "Researchers counted beetles beneath fallen logs. Each observation went into a notebook before the specimens were returned."),
    ("The restaurant served soup with fresh bread. Conversation filled the dining room as the evening rain began.",
     "A surveyor placed markers across the field. His assistant recorded distances from the boundary beside the railway."),
    ("We unpacked our tent beside a quiet lake. After sunset the water reflected a clear band of stars.",
     "The museum borrowed three portraits for its exhibition. Staff prepared labels describing the painters and their families."),
    ("An electrician fitted new lights above the counter. The owner chose warm bulbs to brighten the small kitchen.",
     "A farmer watched clouds gather beyond the pasture. Her sheep had already moved toward the shelter near the barn."),
    ("The book club discussed a mystery novel on Tuesday. Nobody had guessed the ending before the final chapter.",
     "A swimming coach set out coloured floats. Beginners practised breathing while older children worked on their turns."),
    ("An astronomer adjusted the telescope after midnight. Saturn appeared sharply once a thin cloud had drifted away.",
     "The florist arranged tulips in a glass vase. Yellow ribbons were tied around the bouquets awaiting collection."),
    ("The council repaired benches around the square. Shoppers rested there while a busker played near the market.",
     "A translator compared two editions of a letter. One unfamiliar expression required a visit to the university archive."),
    ("We roasted vegetables for a family supper. My brother brought pears and a jar of homemade chutney.",
     "The sailing instructor checked every life jacket. Students listened carefully before carrying their small boats toward the jetty."),
    ("A fox crossed the lane behind our cottage. It disappeared into the hedge without disturbing the sleeping dog.",
     "The theatre crew painted scenery late into the evening. A bright doorway was needed for the second act."),
    ("The jeweller cleaned an old silver bracelet. Its clasp needed repair before the customer could wear it again.",
     "Volunteers collected litter beside the canal. By midday their bags were full and the towpath looked much tidier."),
    ("A geologist examined stones from the cliff. Several layers contained tiny fossils left by an ancient sea.",
     "The chess team met at a cafe after work. Their captain described an unusual opening from last weekend's tournament."),
)


@pytest.mark.parametrize("held_text, candidate", UNRELATED_ENGLISH_PAIRS)
def test_small_gap_unrelated_english_does_not_exclude_or_void(held_text, candidate):
    result = clean_screen(SuiteCase("held", "private", held_text), outputs=(candidate,))
    assert result.excluded_count == 0 and not result.voided


@pytest.mark.parametrize("pieces", [(8, 8, 8, 8), (9, 8, 15), (17, 15), (32,)])
def test_small_gap_marker_covers_whole_marker_in_order(pieces):
    marker = "0123456789abcdef0123456789abcdef"
    fragments, offset = [], 0
    for length in pieces:
        fragments.append(marker[offset:offset + length])
        offset += length
    assert gate.canary_matches((marker,), (("z" * 24).join(fragments),)) == (0,)


@pytest.mark.parametrize("phase", ["index", "scan"])
def test_small_gap_passage_index_and_scan_bounds(monkeypatch, phase):
    monkeypatch.setattr(gate, "_MAX_MATCH_STEPS", 10 if phase == "index" else 30)
    words = tuple(f"term{i}" for i in range(13))
    state = gate._Comparison()
    with pytest.raises(gate.ContaminationError, match="SCREEN_LIMIT"):
        positions = gate._passage_index(words, state)
        assert not gate._ordered_13(positions, ("noise",) * 100, state)
    assert state.limited


def test_small_gap_passage_decodes_the_raw_whole_case_join():
    words = [f"word{i}" for i in range(13)]
    held = SuiteCase("held", "private", " ".join(words))
    fragments = (r" \u00", "77ord0 noise noise " + " noise noise ".join(words[1:]))
    result = clean_screen(held, (SuiteCase("train", "other", fragments),))
    assert result.ngram_excluded == 1
