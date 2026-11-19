# SPDX-License-Identifier: Apache-2.0
"""Synthetic boundary cases for the held-out contamination rules."""
import importlib
import json

import pytest

from g6.metaharness.schema import SuiteCase


@pytest.fixture
def api():
    class API:
        def __getattr__(self, key):
            name = "g6.recursion.contamination_gate"
            assert importlib.util.find_spec(name) is not None, "S3 contamination screen is missing"
            return getattr(importlib.import_module(name), key)

    return API()


def probe(api, drop=0, controls=(0, 0, 0)):
    return api.MembershipProbe(10, 10 - drop, (10,) * len(controls), tuple(10 - x for x in controls))


def run(api, held, training=(), outputs=(), probes=None):
    return api.screen(tuple(training), tuple(held), canaries=("a" * 32,), model_outputs=tuple(outputs),
                      membership=probes if probes is not None else {c.case_id: probe(api) for c in held})


@pytest.mark.parametrize("length,excluded", [(12, 0), (13, 1), (14, 1)])
def test_zero_shared_13_grams_with_exact_boundary(api, length, excluded):
    words = " ".join(f"word{i}" for i in range(length))
    result = run(api, [SuiteCase("held", words + " held suffix", "secret")],
                 [SuiteCase("train", words + " other suffix", "answer")])
    assert result.ngram_excluded == excluded
    assert result.excluded_count == excluded
    assert result.excluded_indices == ((0,) if excluded else ())


def test_screen_normalizes_case_and_unicode_and_checks_labels_and_metadata(api):
    text = " ".join(f"TOKEN{i}" for i in range(13))
    held = [SuiteCase("held", "unrelated", text)]
    result = run(api, held, [SuiteCase("train", "different", "answer", {"trace": text.lower()})])
    assert result.ngram_excluded == result.excluded_count == 1


@pytest.mark.parametrize("kind", ["id", "hash"])
def test_suite_identity_overlap_excludes_even_short_text(api, kind):
    held = SuiteCase("held", "short hidden question", "hidden answer")
    train = SuiteCase("held" if kind == "id" else "train",
                      held.input if kind == "hash" else "something else", "other answer")
    result = run(api, [held], [train])
    assert result.identity_excluded == result.excluded_count == 1


@pytest.mark.parametrize("drop,excluded", [(2.999, 0), (3, 0), (3.001, 1)])
def test_membership_strictly_above_control_mean_plus_two_sample_sd(api, drop, excluded):
    # Control drops 0, 1, 2 have mean 1 and sample standard deviation 1.
    result = run(api, [SuiteCase("held", "private words", "answer")],
                 probes={"held": probe(api, drop, (0, 1, 2))})
    assert result.membership_excluded == result.excluded_count == excluded
    assert result.voided is bool(excluded)


@pytest.mark.parametrize("where", ["input", "label", "metadata", "output"])
def test_canary_in_any_training_field_or_model_output_voids_and_is_counted(api, where):
    marker = "a" * 32
    train = SuiteCase("train", marker if where == "input" else "text",
                      marker if where == "label" else "label", {"x": marker if where == "metadata" else "meta"})
    result = run(api, [SuiteCase("held", "private words", "answer")], [train],
                 [marker] if where == "output" else [])
    assert result.voided and result.canary_hits == 1
    assert marker not in json.dumps(result.to_record())


def test_multiple_reasons_count_an_item_once_and_public_report_has_no_content(api):
    secret = " ".join(f"private{i}" for i in range(13))
    result = run(api, [SuiteCase(secret, secret, secret)], [SuiteCase(secret, secret, secret)],
                 probes={secret: probe(api, 4)})
    assert result.identity_excluded == result.ngram_excluded == result.membership_excluded == 1
    assert result.excluded_count == 1
    assert secret not in json.dumps(result.to_record()) + repr(result)


def test_missing_or_extra_membership_observation_fails_closed(api):
    for observations in ({}, {"other": probe(api)}):
        with pytest.raises(api.ContaminationError, match="MEMBERSHIP_REQUIRED"):
            run(api, [SuiteCase("held", "private", "answer")], probes=observations)


@pytest.mark.parametrize("values", [
    (1, float("nan"), (1, 1), (1, 1)), (1, -1, (1, 1), (1, 1)),
    (1, 1, (1,), (1,)), (1, 1, (1, 1), (1,)), (True, 1, (1, 1), (1, 1)),
])
def test_bad_loss_observations_are_refused(api, values):
    with pytest.raises(api.ContaminationError, match="MEMBERSHIP_INVALID"):
        api.MembershipProbe(*values)


def test_canary_completion_above_chance_voids(api):
    assert api.CanaryProbe(0.2, 0.1).flagged
    assert not api.CanaryProbe(0.1, 0.1).flagged
    result = api.screen((), (SuiteCase("held", "private", "answer"),), canaries=("a" * 32,),
                        membership={"held": probe(api)}, canary_probes=(api.CanaryProbe(0.2, 0.1),))
    assert result.voided and result.canary_probe_flags == 1


def test_canary_is_counted_even_when_membership_readings_are_missing(api):
    result = api.screen((), (SuiteCase("held", "private", "answer"),), canaries=("a" * 32,),
                        model_outputs=("a" * 32,), membership={})
    assert result.voided and result.canary_hits == 1


@pytest.mark.parametrize("canaries", [(None,), (1,), ({},), ("short",), ()])
def test_invalid_canary_configuration_has_a_content_free_refusal(api, canaries):
    with pytest.raises(api.ContaminationError, match="^G6_E_RSI_SCREEN_INVALID$"):
        api.screen((), (SuiteCase("held", "private", "answer"),), canaries=canaries,
                   membership={"held": probe(api)})


def test_unicode_normalization_precedes_13_token_overlap(api):
    words = " ".join("ｔｏｋｅｎ" + str(i) for i in range(13))
    plain = " ".join("token" + str(i) for i in range(13))
    assert run(api, [SuiteCase("held", words, "answer")], [SuiteCase("train", plain, "other")]).excluded_count == 1


def test_short_canonical_input_duplicates_are_excluded(api):
    result = run(api, [SuiteCase("held", "two words", "secret")],
                 [SuiteCase("train", " TWO\tWORDS ", "other")])
    assert result.identity_excluded == result.excluded_count == 1


@pytest.mark.parametrize("invisible", ["\u200b", "\u00ad", "\u034f", "\U000e0100"])
def test_fixed_removal_view_catches_listed_default_ignorables(api, invisible):
    passage = " ".join(f"word{i}" for i in range(13))
    result = run(api, [SuiteCase("held", passage + " held", "secret")],
                 [SuiteCase("train", passage.replace(" ", invisible + " ") + " train", "other")])
    assert result.ngram_excluded == result.excluded_count == 1


@pytest.mark.parametrize("nested", [False, True])
def test_13_token_overlap_across_case_fields(api, nested):
    words = [f"word{i}" for i in range(13)]
    expected = (" ".join(words[7:10]), [" ".join(words[10:])]) if nested else " ".join(words[7:])
    result = run(api, [SuiteCase("held", " ".join(words) + " held", "secret")],
                 [SuiteCase("train", " ".join(words[:7]), expected)])
    assert result.ngram_excluded == result.excluded_count == 1


def marker_variant(marker, mode):
    if mode == "uppercase":
        return marker.upper()
    if mode == "nfkc":
        return "".join(chr(ord(c) + 0xFEE0) for c in marker)
    if mode == "unicode_escape":
        return "".join(f"\\u{ord(c):04x}" for c in marker)
    if mode == "percent":
        return "".join(f"%{ord(c):02X}" for c in marker)
    if mode == "surrogate_ignorable":
        return r"\udb40\udd00".join(marker)
    return "\u200b".join(marker)


@pytest.mark.parametrize("mode", ["uppercase", "nfkc", "unicode_escape", "percent", "invisible", "surrogate_ignorable"])
@pytest.mark.parametrize("source", ["training", "output"])
def test_fixed_canary_views_catch_listed_encodings(api, mode, source):
    encoded = marker_variant("a" * 32, mode)
    result = run(api, [SuiteCase("held", "private", "secret")],
                 [SuiteCase("train", encoded, "other")] if source == "training" else (),
                 [encoded] if source == "output" else ())
    assert result.voided and result.canary_hits == 1


def test_canary_split_across_adjacent_nested_fields_voids(api):
    marker = "a" * 32
    result = run(api, [SuiteCase("held", "private", "secret")],
                 [SuiteCase("train", "different", (marker[:16], [marker[16:]]))])
    assert result.voided and result.canary_hits == 1


def test_fixed_views_recompose_after_ignorable_removal_and_are_idempotent(api):
    held, train = "caf\u00e9", "cafe\u034f\u0301"
    result = run(api, [SuiteCase("held", held, "secret")], [SuiteCase("train", train, "other")])
    assert result.identity_excluded == result.excluded_count == 1
    assert api.canonical_text(api.canonical_text(train)) == api.canonical_text(train)
    for view in api.canonical_views(train):
        assert view in api.canonical_views(view)


def test_fixed_space_view_catches_replaced_invisible_word_boundaries(api):
    words = [f"word{i}" for i in range(13)]
    result = run(api, [SuiteCase("held", " ".join(words) + " held", "secret")],
                 [SuiteCase("train", "\u200b".join(words) + " train", "other")])
    assert result.ngram_excluded == result.excluded_count == 1


def test_fixed_fragment_views_carry_mixed_boundaries(api):
    words = [f"word{i}" for i in range(13)]
    result = run(api, [SuiteCase("held", " ".join(words) + " held", "secret")],
                 [SuiteCase("train", "different", ("word0 wor", "d1 word2", " ".join(words[3:])))])
    assert result.ngram_excluded == result.excluded_count == 1


def test_fixed_whitespace_free_view_catches_short_identity(api):
    result = run(api, [SuiteCase("held", "shortword", "secret")],
                 [SuiteCase("train", "short\n word", "other")])
    assert result.identity_excluded == result.excluded_count == 1


@pytest.mark.parametrize("source", ["training", "output"])
def test_fixed_whitespace_free_view_catches_internal_canary_whitespace(api, source):
    marker = "a" * 16 + " \n " + "a" * 16
    result = run(api, [SuiteCase("held", "private", "secret")],
                 [SuiteCase("train", "different", {"nested": [marker]})] if source == "training" else (),
                 [marker] if source == "output" else ())
    assert result.voided and result.canary_hits == 1


def test_fixed_views_keep_independent_cases_and_outputs_separate(api):
    marker = "a" * 16 + "b" * 16
    result = api.screen((SuiteCase("one", marker[:16], ""), SuiteCase("two", marker[16:], "")),
                        (SuiteCase("held", "private", "secret"),), canaries=(marker,),
                        model_outputs=(marker[:16], marker[16:]), membership={"held": probe(api)})
    assert not result.voided and result.canary_hits == 0


def test_fragment_graph_size_is_quadratic_for_character_fragments(api):
    graph = api._token_graph(["x"] * 80)
    assert sum(len(edges) for edges in graph) == 80 * 81 // 2
    words = " ".join("x" for _ in range(13))
    assert run(api, [SuiteCase("held", words + " held", "secret")],
               [SuiteCase("train", "other", tuple("x" for _ in range(80)))]).ngram_excluded == 1


def test_many_field_cases_are_screened_not_refused(api):
    # A case with many meta fields must be screened linearly, never refused by a join limit.
    meta = {f"k{i}": f"v{i}" for i in range(40)}
    passage = " ".join(f"word{i}" for i in range(13))
    result = run(api, [SuiteCase("held", passage + " held", "secret")],
                 [SuiteCase("train", passage, "answer", meta)])
    assert result.ngram_excluded == 1


def test_compact_fixed_view_repeats_escape_decode_after_whitespace_removal(api):
    assert api.canonical_views("a% 20b")[2] == "ab"
    marker = "a" * 16 + "% 20" + "a" * 16
    assert run(api, [SuiteCase("held", "private", "secret")], outputs=[marker]).canary_hits == 1


@pytest.mark.parametrize("fragments,first", [
    ((r"\u00", "77ord0 word1", " ".join(f"word{i}" for i in range(2, 13))), "word0"),
    (("cafe", "\u0301 word1", " ".join(f"word{i}" for i in range(2, 13))), "caf\u00e9"),
    (("%7", "7ord0 word1", " ".join(f"word{i}" for i in range(2, 13))), "word0"),
])
@pytest.mark.parametrize("side", ["training", "held_out"])
@pytest.mark.xfail(strict=True, reason="STATED LIMIT (module docstring): an escape, percent or combining sequence cut "
                   "by a field boundary is recomposed only when every boundary is of one kind; these mix an empty and "
                   "a space boundary, which an accidental copy does not do. Exhaustive joins refused ordinary cases.")
def test_joined_candidates_normalize_cross_boundary_state(api, fragments, first, side):
    passage = first + " " + " ".join(f"word{i}" for i in range(1, 13))
    joined = SuiteCase("joined", "other", fragments)
    plain = SuiteCase("plain", passage + " suffix", "answer")
    held, training = (plain, joined) if side == "training" else (joined, plain)
    result = run(api, [held], [training])
    assert result.ngram_excluded == result.excluded_count == 1


@pytest.mark.parametrize("kind", ["depth", "nodes", "characters"])
def test_shared_traversal_refuses_past_existing_limits(api, kind):
    if kind == "depth":
        at_limit = "leaf"
        for _ in range(32):
            at_limit = [at_limit]
        over_limit = [at_limit]
    elif kind == "nodes":
        at_limit = [None] * 16383
        over_limit = at_limit + [None]
    else:
        at_limit = "x" * 1048576
        over_limit = at_limit + "x"
    tuple(api._strings(at_limit))
    with pytest.raises(api.ContaminationError, match="^G6_E_RSI_SCREEN_LIMIT$"):
        tuple(api._strings(over_limit))
