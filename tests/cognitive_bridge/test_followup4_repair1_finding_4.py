"""The selector's candidate cap must precede per-candidate historical scoring."""
import sys

import pytest

from g6.cognitive_bridge import (
    MetacognitiveMethodSelector, MethodVariant, ProblemSignature,
)


def test_candidate_cap_precedes_scoring():
    selector = MetacognitiveMethodSelector()
    context = ProblemSignature("d", "u", "k", 1, 10)
    variants = [MethodVariant(f"v{i}", "formal", "SMT", "formal", "check_validity", 1)
                for i in range(101)]
    scored = []
    score_code = MetacognitiveMethodSelector._score.__code__

    def profile(frame, event, answer):
        if event == "call" and frame.f_code is score_code:
            scored.append(frame.f_locals["variant"].id)

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        with pytest.raises(ValueError, match="at most 100"):
            selector.select(context, variants, required_methods=["formal"])
    finally:
        sys.setprofile(previous)

    assert scored == [], f"scored {len(scored)} candidates before checking the 100-candidate cap"
