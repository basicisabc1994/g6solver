"""A stated rule must be shown in full before a person confirms its scope."""
from g6.harness import behaviour_question as bq
from g6.mcp import behaviour
from tests.repair_question2.helpers import Dialog, TASK, ask, candidate, repo


def test_quoted_rule_whitespace_is_not_changed_before_confirmation(tmp_path):
    root = repo(tmp_path)
    rule = 'For every input, prepend the literal prefix `a  b`.'
    other_rule = 'For every input, prepend the literal prefix `a b`.'
    one = bq.caller_candidates(TASK, [candidate(rule=rule)])[0]
    other = bq.caller_candidates(TASK, [candidate(rule=other_rule)])[0]
    case, scope = one.questions("b" * 64)
    other_case, other_scope = other.questions("b" * 64)
    assert scope.sha256 != other_scope.sha256
    dialog = Dialog({"choice": "A", "binds": "rule", "name": "Maintainer"})
    assert ask(root, data=[candidate(rule=rule)], dialog=dialog)["status"] == "confirmed"
    held = behaviour.enforced(str(root), TASK)
    assert held.expectations[0].scope == bq.RULE_SCOPE.format(rule=rule)
    message, _schema = bq.form(case, one, scope)
    other_message, _other_schema = bq.form(other_case, other, other_scope)
    assert message != other_message, "two rules with different quoted literals are identical in the behaviour form"
