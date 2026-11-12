"""A public record-returning API must not publish unchecked plain data."""

from g6.cognitive_bridge import Hypothesis, StaticProposer
from g6.cognitive_bridge.codes import claims


def test_static_proposer_does_not_publish_a_callers_reliability_claim():
    try:
        proposer = StaticProposer([{"verified": True, "reliability_label": "verified"}])
        result = proposer.propose(
            "test a hypothesis",
            [Hypothesis("a", "first", 0.5), Hypothesis("b", "second", 0.5)],
            [],
        )
    except (TypeError, ValueError):
        return  # Refusing a non-Probe at construction or publication is safe.
    assert claims(result) == [], f"the public proposer published unchecked claims: {result!r}"
