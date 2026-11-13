"""The artifact's size cap must precede a copy of a rejected buffer."""
import sys

import pytest

from g6.cognitive_bridge import EvidenceArtifact, snapshot


def test_oversized_artifact_is_not_copied_before_its_cap():
    content = bytearray(2_000_001)
    copied = []
    converter = snapshot._bytes.__code__

    def profile(frame, event, answer):
        if (event == "return" and frame.f_code is converter
                and frame.f_locals.get("value") is content and type(answer) is bytes):
            copied.append(len(answer))

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        with pytest.raises(ValueError, match="artifact exceeds 2MB"):
            EvidenceArtifact("e", content, "report", "candidate", "store:e", "claim")
    finally:
        sys.setprofile(previous)

    assert copied == [], f"copied {copied} bytes before checking the artifact cap"
