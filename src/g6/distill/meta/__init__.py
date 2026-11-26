# SPDX-License-Identifier: Apache-2.0
"""M: advisory records and planning; fixed gates alone judge measured outcomes."""
from importlib import import_module

_MODULES = {
    'controller': ('MetaController',),
    'experiments': ('ExperimentChoice', 'ExperimentForecast', 'choose_experiment'),
    'policy_gate': ('FrozenMetaEvaluation', 'SynthesisReplay', 'evaluate_policy', 'judge_policy', 'policy_proposal', 'rank_policies'),
    'prediction_ledger': ('MetaLedger',),
    'records': ('ConceptOutcome', 'MetaPrediction', 'MethodOutcome', 'ReplayMeasurement', 'ReplaySequence',
                'SynthesisPolicy', 'WorthEstimate', 'concept_worth'),
    'selection': ('SelectorFeed', 'selector_feed'),
}


def __getattr__(name):
    # S may read the data policy without importing any metaharness judging code.
    for module, names in _MODULES.items():
        if name in names:
            return getattr(import_module(f'{__name__}.{module}'), name)
    raise AttributeError(name)

__all__ = ['ConceptOutcome', 'ExperimentChoice', 'ExperimentForecast', 'FrozenMetaEvaluation', 'MetaController', 'MetaLedger',
           'MetaPrediction', 'MethodOutcome', 'ReplayMeasurement', 'ReplaySequence', 'SelectorFeed', 'SynthesisPolicy',
           'SynthesisReplay', 'WorthEstimate', 'choose_experiment',
           'concept_worth', 'evaluate_policy', 'judge_policy', 'policy_proposal', 'rank_policies', 'selector_feed']
