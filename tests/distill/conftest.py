"""Keep synthetic in-memory gate fixtures on an in-memory interval journal."""
import pytest


@pytest.fixture(autouse=True)
def synthetic_interval_journal(monkeypatch):
    from g6.distill import acceptance_gate as gate
    interval_journal = gate._interval_journal

    def journal(ident=None):
        decision_journal = gate._journal()
        if getattr(decision_journal, "path", None) is None:
            return decision_journal
        return interval_journal(ident)

    monkeypatch.setattr(gate, "_interval_journal", journal)
