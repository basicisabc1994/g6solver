"""Only observations with separately published G6 custody may become questions."""
import json

import pytest


def runner():
    from g6.harness import behaviour_runner
    return behaviour_runner


@pytest.mark.parametrize("damage", ["none", "missing", "torn", "foreign", "activity"])
def test_custody_keeps_reply_fields_and_refuses_partial_publication(tmp_path, damage):
    mod = runner()
    one = mod.Activity(check="x", template="differential.v1", result="behaviour_changed")
    one.owned_runs = [True, True, True]
    reply = {"assessment": {"completion": {"activities": [one]}}}
    data = json.dumps(reply).encode()
    assert json.loads(data)["assessment"]["completion"]["activities"] == [dict(one)]
    path = tmp_path / "review.json"
    path.write_bytes(data)
    mod.write_probes(path, reply, data)
    side = path.with_suffix(".observations.json")
    if damage == "missing":
        side.unlink()
    elif damage == "torn":
        side.write_text("{")
    elif damage == "foreign":
        data += b" "
    elif damage == "activity":
        one["result"] = "replaced"
        data = json.dumps(reply).encode()
    probes = mod.read_probes(path, data)
    assert bool(probes) is (damage == "none")


def test_an_unowned_stream_never_acquires_custody(tmp_path):
    mod = runner()
    one = mod.Activity(check="x", template="differential.v1", result="behaviour_changed")
    one.owned_runs = [True, False]
    reply = {"assessment": {"completion": {"activities": [one]}}}
    path = tmp_path / "review.json"
    data = json.dumps(reply).encode()
    mod.write_probes(path, reply, data)
    assert mod.read_probes(path, data) == {}
