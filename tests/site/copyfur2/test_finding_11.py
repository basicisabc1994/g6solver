def test_an_exempt_code_name_does_not_hide_a_sentence_boundary(honesty):
    page = '<p>G6 calls <code>check</code>. Other tools verify programs.</p>'
    assert len(honesty.sentences(honesty._page_copy(page))) == 2, 'Control: ordinary copy sees two sentences.'
    assert honesty.g6_claims('<p>G6 calls check. Other tools verify programs.</p>') == []
    assert honesty.g6_claims(page) == [], 'The essay-only code marker hid a certain sentence boundary.'
