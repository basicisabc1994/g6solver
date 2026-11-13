def test_textarea_literal_tags_do_not_create_a_code_exemption(honesty):
    # Textarea content is RCDATA: these tag bytes are shown literally, rather than making a code span.
    text = 'G6 produces <code>verified</code> answers.'
    assert honesty.claims(text), 'Control: the literal displayed string is a claim.'
    assert honesty.claims(honesty._page_copy('<textarea readonly>' + text + '</textarea>')), (
        'Literal textarea text was incorrectly given the inline-code exemption.'
    )
