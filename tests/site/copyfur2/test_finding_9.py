import html


def test_inline_frame_document_is_reader_facing_copy(honesty):
    inner = '<p>G6 verifies every patch.</p>'
    assert honesty.claims(honesty._page_copy(inner)), 'Control: the inner document contains a claim.'
    page = '<iframe title="Example output" srcdoc="' + html.escape(inner, quote=True) + '"></iframe>'
    assert honesty.claims(honesty._page_copy(page)), 'The visible srcdoc document was omitted.'
