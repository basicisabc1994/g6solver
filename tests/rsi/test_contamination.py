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
