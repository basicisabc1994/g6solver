"""A dialog must preserve spaces inside the executable input and expected output."""
import json

import pytest

from g6.harness import behaviour_question as bq
from g6.harness.confirmed_expectation import confirm
from .helpers import TASK, candidate
from tests.harness.test_outcomes_compete import namespace


def test_form_distinguishes_json_values_that_the_compiled_check_distinguishes(tmp_path):
    messages = []
    for value in ("a  b", "a b"):
        one = bq.caller_candidates(TASK, [candidate(args=[value],
                   outcomes={"A": {"returns": value}, "B": {"returns": "other"}})])[0]
        question = one.question("b" * 64)
        messages.append(bq.form(question, one)[0])
        program = ("def add(arg):\n    import json\n    from pathlib import Path\n"
                   "    path = Path('seen.json')\n"
                   "    seen = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []\n"
                   "    seen.append(arg)\n    path.write_text(json.dumps(seen), encoding='utf-8')\n    return arg\n")
        check = namespace(tmp_path, confirm(question, "A", answered_by="person:Maintainer").instance, program)
        (tmp_path / 'seen.json').unlink(missing_ok=True)
        with pytest.MonkeyPatch.context() as local:
            local.chdir(tmp_path)
            check["test_example"]()
        seen = json.loads((tmp_path / 'seen.json').read_text(encoding='utf-8'))
        assert seen == [value]
    assert messages[0] != messages[1], "different executed strings have identical behaviour forms"
