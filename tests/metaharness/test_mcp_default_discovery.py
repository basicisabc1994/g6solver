"""Home discovery errors stay in the loop tools' existing envelopes."""
from pathlib import Path

import pytest

from g6.mcp import improve


@pytest.mark.parametrize("operation", ["asked", "status", "enrol", "resume", "demote"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyError, OSError])
def test_default_discovery_typed_errors_are_enveloped(monkeypatch, tmp_path, operation, failure):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("G6_HOME", "")

    def no_home(cls):
        raise failure("home unavailable")

    monkeypatch.setattr(Path, "home", classmethod(no_home))
    reply = (getattr(improve, operation)("") if operation in {"asked", "status"}
             else improve.asked_act(operation, ""))
    assert isinstance(reply, dict)
    if operation == "status":
        assert reply["warning_card"]["code"] == improve.STATUS
        assert reply["running"] is None
        assert reply["pause"]["code"] == improve.RECORDS_UNREADABLE
    else:
        assert reply["warning_card"]["code"] == improve.START_FAILED
        assert reply["running"] is False
