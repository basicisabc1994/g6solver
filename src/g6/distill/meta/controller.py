# SPDX-License-Identifier: Apache-2.0
"""Record each M choice through the self-model before returning it to a runner.

The lower-level worth, ranking and EIG helpers are pure planning calculations.
This is their decision boundary. The runner must honor each simulation's risk
decision; no method here executes an experiment, calls a model or activates S.
"""
from .experiments import choose_experiment
from .selection import selector_feed


class MetaController:
    def __init__(self, ledger):
        self.ledger = ledger

    def _record(self, kind, subject_id, *, action, **inputs):
        if not self._bound(action, kind, subject_id):
            raise ValueError('self-model action must bind this choice and prediction type')
        return self.ledger.predict(kind=kind, subject_id=subject_id, action=action, **inputs)

    @staticmethod
    def _bound(action, kind, subject_id):
        # A subject can be chosen again on another tick/case. The suffix names
        # that action instance without changing the subject whose forecast is scored.
        return action.kind == kind and (action.action_id == subject_id or action.action_id.startswith(subject_id + ':'))

    def concept(self, estimates, *, action, **inputs):
        eligible = [(ident, estimate) for ident, estimate in estimates.items() if estimate.value_per_cost > 0]
        if not eligible:
            return None
        ident, estimate = min(eligible, key=lambda row: (-row[1].value_per_cost, row[0]))
        row = self._record('concept_compile', ident, action=action, **inputs)
        self.ledger.record_data(estimate)
        return ident, estimate, row

    def reuse(self, entries, outcomes, context, *, actions, admit_trial=None, **inputs):
        from g6.core.selector import rank_library_methods
        feed = selector_feed(entries, outcomes)
        ranking = rank_library_methods(feed.variants, feed.trials, context, admit_trial=admit_trial)
        if ranking is None:
            return None
        chosen = tuple(item['variant_id'] for item in ranking.ordered_variants)
        # Validate every action before writing the first row: no partial ranking
        # caused by missing/mismatched action bindings.
        for ident in chosen:
            if ident not in actions or not self._bound(actions[ident], 'method_catch', ident):
                raise ValueError('each ranked method needs its own bound method_catch action')
        rows = tuple(self._record('method_catch', ident, action=actions[ident], **inputs) for ident in chosen)
        return ranking, rows

    def experiment(self, state, forecasts, *, action, **inputs):
        choice = choose_experiment(state, forecasts)
        if choice is None:
            return None
        row = self._record('experiment_decide', choice.experiment.id, action=action, **inputs)
        self.ledger.record_data(choice)
        return choice, row

    def policy(self, base, policy, *, proposer, action, **inputs):
        from .policy_gate import policy_proposal
        proposal = policy_proposal(base, policy, proposer=proposer)
        row = self._record('synthesis_policy', proposal.candidate_digest, action=action, **inputs)
        self.ledger.record_data(policy)
        return proposal, row
