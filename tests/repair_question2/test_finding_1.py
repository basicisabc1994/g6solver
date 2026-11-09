"""A dialog must preserve spaces inside the executable input and expected output."""
from g6.harness import behaviour_question as bq
from g6.harness.confirmed_expectation import confirm
from .helpers import TASK, candidate, namespace


def test_form_distinguishes_json_values_that_the_compiled_check_distinguishes(tmp_path):
    messages = []
    for value in ("a  b", "a b"):
        one = bq.caller_candidates(TASK, [candidate(args=[value],
                   outcomes={"A": {"returns": value}, "B": {"returns": "other"}})])[0]
        question = one.question("b" * 64)
        messages.append(bq.form(question, one)[0])
        check = namespace(tmp_path, confirm(question, "A", answered_by="person:Maintainer").instance)
        seen = []
        check["_load"] = lambda spec: (lambda arg: seen.append(arg) or arg)
        check["test_example"]()
        assert seen == [value]
    assert messages[0] != messages[1], "different executed strings have identical behaviour forms"
