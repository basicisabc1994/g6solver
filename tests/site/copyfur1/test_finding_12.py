def test_readonly_text_input_value_is_reader_facing_copy(honesty):
    text = 'G6 verifies every patch.'
    assert honesty.claims(honesty._page_copy('<input type="button" value="' + text + '">'))
    page = '<input type="text" readonly aria-label="Assurance" value="' + text + '">'
    assert honesty.claims(honesty._page_copy(page)), 'Visible readonly input text was skipped.'
