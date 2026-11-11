"""Exception outcomes must not both accept the same actual exception."""
import pytest
from g6.harness import behaviour_question as bq
from g6.harness.confirmed_expectation import confirm
from .helpers import TASK, candidate, namespace


def test_exception_multiple_inheritance_cannot_satisfy_both_competing_outcomes(tmp_path):
    class Both(ValueError, TypeError):
        pass

    try:
        one = bq.caller_candidates(TASK, [candidate(outcomes={
            "A": {"raises": "ValueError"}, "B": {"raises": "TypeError"}})])[0]
    except bq.CandidateRefused:
        return
    question = one.question("b" * 64)
    accepted = []
    for option in ("A", "B"):
        check = namespace(tmp_path, confirm(question, option, answered_by="person:Maintainer").instance)
        load = check["_load"]

        def raises_both(*args):
            raise Both("same observed exception")

        check["_load"] = lambda spec, load=load: load(spec) if spec[0] == "builtin" else raises_both
        try:
            check["test_example"]()
        except pytest.fail.Exception:
            continue
        accepted.append(option)
    assert len(accepted) <= 1, f"one implementation satisfies both confirmed outcomes: {accepted}"
