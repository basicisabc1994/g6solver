# SPDX-License-Identifier: Apache-2.0
from g6.harness.completion_loop import asks


def test_question_mark_in_tilde_code_is_not_a_question():
    assert not asks('~~~python\nprint("choose?")\n~~~\n')


def test_a_question_in_an_html_block_is_read_and_script_text_is_not():
    assert asks("<div>\nShall I add the dependency?\n</div>\n")
    assert not asks("<script>\nvar a = b ? c : d;\n</script>\n")
