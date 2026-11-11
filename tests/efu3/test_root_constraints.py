"""Wider class members which constrain the last repair's offered roots."""
from types import SimpleNamespace

from g6.harness import verification_search
from g6.ml import _fit
from tests.harness.efu2_cases import NODE, PASSED, report


def test_caller_fields_do_not_replace_the_training_seed_in_a_child_record():
    prepared = SimpleNamespace(spec=SimpleNamespace(task="regression", seed=7),
                               split=SimpleNamespace(train=[], held_out=[]))
    encoded = SimpleNamespace(numeric=[], categorical=[], X=[], y=[], groups=[])
    assert _fit.job("fit", prepared, encoded, seed=99)["seed"] == 7


def test_a_search_outcome_does_not_change_when_only_console_text_changes():
    clean = verification_search.reading(report(PASSED), NODE)
    printed = verification_search.reading(report(PASSED, output="G6-OBSERVATION broken\n"), NODE)
    assert printed["result"] == clean["result"]
