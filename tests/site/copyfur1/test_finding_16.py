def test_application_name_metadata_is_reader_facing_copy(honesty):
    text = 'G6 verifies every patch.'
    assert honesty.claims(honesty._page_copy('<meta name="description" content="' + text + '">'))
    page = '<meta name="application-name" content="' + text + '">'
    assert honesty.claims(honesty._page_copy(page)), 'The reader-facing application name was skipped.'
