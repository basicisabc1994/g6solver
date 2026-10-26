"""Public authored development candidate: adjacent-only replay protection."""


def apply_event(state, event):
    result = {"quantity": state["quantity"], "seen_ids": list(state["seen_ids"])}
    if not result["seen_ids"] or event["id"] != result["seen_ids"][-1]:
        result["quantity"] += event["delta"]
        result["seen_ids"].append(event["id"])
    return result
