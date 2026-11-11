def test_every_description_of_a_colon_led_term_keeps_its_subject(honesty):
    page = '<dl><dt>G6 can:</dt><dd>check a patch.</dd><dd>verify every patch.</dd></dl>'
    assert honesty.g6_claims('<dl><dt>G6 can:</dt><dd>verify every patch.</dd></dl>')
    assert honesty.claims(honesty._page_copy(page)), 'Control: the claim text is read.'
    assert honesty.g6_claims(page), 'The second description lost the G6 subject.'
