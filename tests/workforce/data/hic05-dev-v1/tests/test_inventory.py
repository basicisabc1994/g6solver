"""Original fixed checks for this new authored development population."""
from inventory import apply_event


def test_new_event():
    assert apply_event({"quantity": 10, "seen_ids": []}, {"id": "evt-a", "delta": 4}) == {
        "quantity": 14, "seen_ids": ["evt-a"]}


def test_adjacent_duplicate():
    state = {"quantity": 14, "seen_ids": ["evt-a"]}
    assert apply_event(state, {"id": "evt-a", "delta": 4}) == state


def test_inputs_unchanged():
    state, event = {"quantity": 10, "seen_ids": []}, {"id": "evt-a", "delta": 4}
    apply_event(state, event)
    assert state == {"quantity": 10, "seen_ids": []}
    assert event == {"id": "evt-a", "delta": 4}
