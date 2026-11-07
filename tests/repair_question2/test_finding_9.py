"""Every unreadable budget setting must close the question budget."""
from g6.harness import question_store as qs


def test_digit_character_that_int_cannot_read_allows_no_question(monkeypatch):
    monkeypatch.setenv(qs.BUDGET_ENV, "²")
    try:
        budget, note = qs.default_budget()
        error = ""
    except ValueError as exc:
        budget, note, error = None, "", str(exc)
    assert budget == 0 and note, f"unreadable budget crashed instead of allowing none: {error}"
