# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""WS-C.3: hypothesis registry + reflection controls (G6-INV-04/09, §11; AT-08, AT-12).

EMPIRICAL + ABSTRACT legs. A raw model re-proposes a disconfirmed strategy with full
confidence; these controls make "guarding one's competence" (Dörner) structurally impossible.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from g6.action_logic import (
    Hypothesis,
    HypothesisStatus,
    Outcome,
    apply_disconfirming_evidence,
    disconfirming_evidence_severity,
    reauthorisation_severity,
    reauthorisation_severity_from_registry,
    update_on_outcome,
)
from g6.action_logic.hypotheses import E_DISCONFIRMING_PENDING, E_FALSIFIED_REUSED

_PURE = settings(max_examples=200, deadline=None)


def _hyp(**kw) -> Hypothesis:
    base = dict(
        hypothesis_id="h1",
        statement="The latency regression is caused by the new cache layer.",
        confidence=0.6,
        falsification_condition="latency stays high after the cache is disabled",
    )
    base.update(kw)
    return Hypothesis(**base)


# =========================================================================== #
# §11 reflection — update_on_outcome
# =========================================================================== #

class TestReflectionUpdate:
    def test_disconfirmed_outcome_falsifies(self) -> None:
        h = update_on_outcome(_hyp(), Outcome.DISCONFIRMED, evidence="latency stayed high")
        assert h.status is HypothesisStatus.FALSIFIED
        assert h.confidence <= 0.1
        assert "latency stayed high" in h.disconfirming_evidence

    def test_harmful_outcome_falsifies(self) -> None:
        assert update_on_outcome(_hyp(), Outcome.HARMFUL).status is HypothesisStatus.FALSIFIED

    def test_confirmed_outcome_supports_but_does_not_prove(self) -> None:
        # §11: a successful outcome MUST NOT automatically validate the causal model.
        h = update_on_outcome(_hyp(confidence=0.6), Outcome.CONFIRMED, evidence="cache off fixed it")
        assert h.status is HypothesisStatus.SUPPORTED  # not a "proven" absorbing state
        assert h.confidence < 1.0 and h.confidence <= 0.9

    def test_partial_confirmation_is_capped_lower(self) -> None:
        h = update_on_outcome(_hyp(confidence=0.7), Outcome.PARTIALLY_CONFIRMED)
        assert h.status is HypothesisStatus.SUPPORTED and h.confidence <= 0.75

    def test_confounded_weakens(self) -> None:
        h = update_on_outcome(_hyp(confidence=0.8), Outcome.CONFOUNDED)
        assert h.status is HypothesisStatus.WEAKENED and h.confidence < 0.8

    def test_inconclusive_is_no_change(self) -> None:
        h0 = _hyp(confidence=0.6)
        h1 = update_on_outcome(h0, Outcome.INCONCLUSIVE)
        assert h1.status is h0.status and h1.confidence == h0.confidence

    def test_falsified_is_sticky_under_later_confirmation(self) -> None:
        # A model already shown wrong cannot be silently resurrected by a later success.
        falsified = update_on_outcome(_hyp(), Outcome.DISCONFIRMED)
        revived = update_on_outcome(falsified, Outcome.CONFIRMED, evidence="it worked this time")
        assert revived.status is HypothesisStatus.FALSIFIED

    @_PURE
    @given(outcome=st.sampled_from(list(Outcome)), conf=st.floats(0.0, 1.0))
    def test_property_confirmation_never_reaches_certainty(self, outcome: Outcome, conf: float) -> None:
        h = update_on_outcome(_hyp(confidence=conf), outcome)
        assert h.confidence < 1.0  # no outcome ever asserts certainty

    @_PURE
    @given(outcome=st.sampled_from(list(Outcome)))
    def test_property_falsified_stays_falsified(self, outcome: Outcome) -> None:
        falsified = _hyp(status=HypothesisStatus.FALSIFIED, confidence=0.1)
        assert update_on_outcome(falsified, outcome).status is HypothesisStatus.FALSIFIED


# =========================================================================== #
# AT-08 — disconfirming evidence pauses dependent actions
# =========================================================================== #

class TestDisconfirmingEvidence:
    def test_apply_reduces_confidence_and_weakens(self) -> None:
        h = apply_disconfirming_evidence(_hyp(confidence=0.8), "a counterexample appeared")
        assert h.status is HypothesisStatus.WEAKENED
        assert h.confidence < 0.8
        assert "a counterexample appeared" in h.disconfirming_evidence

    def test_at08_disconfirming_evidence_pauses(self) -> None:
        h = apply_disconfirming_evidence(_hyp(), "contradicts the plan")
        sev, reasons, item = disconfirming_evidence_severity(h)
        assert sev == "review" and E_DISCONFIRMING_PENDING in reasons
        assert item["hypothesis_id"] == "h1"

    def test_clean_hypothesis_does_not_pause(self) -> None:
        sev, reasons, item = disconfirming_evidence_severity(_hyp())
        assert sev is None and reasons == [] and item is None


# =========================================================================== #
# AT-12 — strategy repetition on a falsified hypothesis is blocked
# =========================================================================== #

class TestReauthorisation:
    def test_at12_relying_on_falsified_hypothesis_blocks(self) -> None:
        falsified = _hyp(status=HypothesisStatus.FALSIFIED, confidence=0.1)
        sev, reasons, item = reauthorisation_severity(relied_hypothesis=falsified)
        assert sev == "block" and E_FALSIFIED_REUSED in reasons
        assert item["replacement_accepted"] is False

    def test_at12_new_evidence_backed_hypothesis_unblocks(self) -> None:
        falsified = _hyp(status=HypothesisStatus.FALSIFIED)
        replacement = Hypothesis(
            hypothesis_id="h2",
            statement="The regression is caused by GC pauses, not the cache.",
            confidence=0.5,
            supporting_evidence=("gc log shows long pauses",),
            falsification_condition="latency stays high after GC tuning",
        )
        sev, reasons, item = reauthorisation_severity(
            relied_hypothesis=falsified, new_hypothesis=replacement
        )
        assert sev is None and item["replacement_accepted"] is True

    def test_at12_replacement_without_evidence_still_blocks(self) -> None:
        falsified = _hyp(status=HypothesisStatus.FALSIFIED)
        bad = Hypothesis(hypothesis_id="h2", statement="Try again, same idea.",
                         falsification_condition="x")  # no supporting evidence
        sev, _r, _i = reauthorisation_severity(relied_hypothesis=falsified, new_hypothesis=bad)
        assert sev == "block"

    def test_at12_reusing_the_same_falsified_id_blocks(self) -> None:
        falsified = _hyp(status=HypothesisStatus.FALSIFIED)
        same = _hyp(status=HypothesisStatus.ACTIVE, supporting_evidence=("hand-wave",))  # same id h1
        sev, _r, _i = reauthorisation_severity(relied_hypothesis=falsified, new_hypothesis=same)
        assert sev == "block"

    def test_active_hypothesis_does_not_block_reauthorisation(self) -> None:
        sev, reasons, item = reauthorisation_severity(relied_hypothesis=_hyp())
        assert sev is None and reasons == [] and item is None

    def test_registry_lookup_variant(self) -> None:
        registry = {"h1": _hyp(status=HypothesisStatus.FALSIFIED)}
        sev, _r, _i = reauthorisation_severity_from_registry("h1", registry)
        assert sev == "block"
        # unknown id -> no recorded falsification -> no block here
        assert reauthorisation_severity_from_registry("ghost", registry)[0] is None
