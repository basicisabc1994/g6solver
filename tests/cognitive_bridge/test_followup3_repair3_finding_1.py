"""Size limits must bound input walks as well as the final encoder.

The profiler only stops an otherwise trillion-node walk; it does not change the
input or any bridge function, and the test restores the previous profiler.
"""
import sys

import pytest

from g6.cognitive_bridge import CognitiveBridgeBlock, Probe, StaticProposer
from g6.cognitive_bridge import snapshot


@pytest.mark.parametrize("entry", ["infer", "snapshot", "record"])
def test_input_walk_reaches_its_size_guard(entry):
    # Only 40 distinct lists, depth 40, but more than a trillion expanded nodes.
    value = 0
    for _ in range(40):
        value = [value, value]

    visits = 0
    stopped = False
    code_objects = {snapshot._not_json.__code__, snapshot._has_record.__code__}

    class UnboundedWalk(Exception):
        pass

    def count(frame, event, arg):
        nonlocal visits
        if event == "call" and frame.f_code in code_objects:
            visits += 1
            if visits > 4 * snapshot.MAX_ITEMS:
                raise UnboundedWalk

    old = sys.getprofile()
    try:
        sys.setprofile(count)
        try:
            if entry == "infer":
                response = CognitiveBridgeBlock().infer({"op": "ops", "claims": value})
                assert response.value["completion_state"] == "blocked-escalated"
            elif entry == "snapshot":
                with pytest.raises(snapshot.NotPlainData):
                    StaticProposer(value)
            else:
                with pytest.raises(snapshot.NotPlainData):
                    Probe("p", "question", "tool", {"a": True, "b": False},
                          0.8, arguments={"payload": value})
        except UnboundedWalk:
            stopped = True
    finally:
        sys.setprofile(old)
    assert not stopped, f"input scanned {visits} nodes before the size guard"
