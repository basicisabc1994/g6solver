# SPDX-License-Identifier: Apache-2.0
from g6.harness.completion_loop import asks


def test_question_mark_in_tilde_code_is_not_a_question():
    assert not asks('~~~python\nprint("choose?")\n~~~\n')
