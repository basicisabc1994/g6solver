def test_soft_hyphenated_word_at_line_boundary_is_still_a_claim(honesty):
    # A discretionary hyphen plus a source line break, as in wrapped copy.
    source = 'G6 produces veri\u00ad\nfied answers.'
    assert honesty.claims(honesty._markdown_copy('G6 produces veri\u00adfied answers.'))
    assert honesty.claims(honesty._markdown_copy(source)), 'Line wrapping hid the verification-family word.'
