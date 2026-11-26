# SPDX-License-Identifier: Apache-2.0
"""Decision-relevant version-space information gain per cost; planning only.

Forecasts are hypotheses under a uniform candidate prior, never measured labels.
The controller remains the authority for scheduling eligibility and budget. The
metaharness selection race remains available for measured candidate comparisons;
this seam spends no frozen held-out cases and executes no candidate.
"""
from collections import defaultdict
from dataclasses import dataclass

from g6.cognitive_bridge.engine import Probe, expected_information_gain
from g6.distill.records import Model

from g6.harness.verification_controller import Experiment, ExperimentState, next_experiment


@dataclass(frozen=True)
class ExperimentForecast:
    experiment: Experiment
    outcomes: tuple[str, ...]
    decisions: tuple[str, ...]

    def __post_init__(self):
        if len(self.outcomes) != len(self.decisions) or len(self.outcomes) < 2:
            raise ValueError('forecast needs aligned outcomes and decisions for at least two candidates')
        if len(self.outcomes) > 32 or len(set(self.outcomes)) > 2:
            raise ValueError('reuse of binary EIG requires two outcome classes and at most 32 candidates')
        if not all(isinstance(x, str) and x for x in (*self.outcomes, *self.decisions)):
            raise ValueError('forecast labels must be non-empty strings')


class ExperimentChoice(Model):
    experiment: Experiment
    information_gain: float
    gain_per_cost: float
    regret: float
    note: str = 'uniform candidate prior; predicted decision information, not gate evidence'


def _candidate_information(outcomes):
    if len(outcomes) < 2:
        return 0.
    ids = tuple(str(i) for i in range(len(outcomes)))
    probe = Probe(id='candidate_partition', question='which outcome separates these candidates?', tool='planning',
                  positive_if={key: value == outcomes[0] for key, value in zip(ids, outcomes)},
                  assumed_match_probability=1., cost=0.)
    return expected_information_gain({key: 1 / len(ids) for key in ids}, probe)


def _gain(forecast):
    groups = defaultdict(list)
    for outcome, decision in zip(forecast.outcomes, forecast.decisions):
        groups[decision].append(outcome)
    # I(decision; outcome) = I(candidate; outcome) - I(candidate; outcome | decision).
    # Reuse the existing binary likelihood model for both terms. Information that
    # only distinguishes candidates WITHIN one decision receives no value.
    gain = _candidate_information(forecast.outcomes) - sum(
        len(group) / len(forecast.outcomes) * _candidate_information(group) for group in groups.values())
    return gain if gain > 1e-12 else 0.


def choose_experiment(state: ExperimentState, forecasts: tuple[ExperimentForecast, ...]) -> ExperimentChoice | None:
    if len({f.experiment.id for f in forecasts}) != len(forecasts):
        raise ValueError('duplicate experiment identifiers')
    eligible = []
    for forecast in forecasts:
        experiment = forecast.experiment
        # Reuse the existing controller's allowlist, open hypotheses, attempts and budget rules.
        if next_experiment(state, (experiment,)) is None:
            continue
        gain = _gain(forecast)
        if gain > 0:
            eligible.append((gain / experiment.cost_units, gain, experiment))
    if not eligible:
        return None
    ratio, gain, experiment = min(eligible, key=lambda row: (-row[0], row[2].id))
    return ExperimentChoice(experiment=experiment, information_gain=gain, gain_per_cost=ratio,
                            regret=max(r[0] for r in eligible) - ratio)
