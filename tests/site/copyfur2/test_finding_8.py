def test_microdata_meta_description_is_reader_facing_copy(honesty):
    text = 'G6 verifies every patch.'
    assert honesty.claims(honesty._page_copy(f'<meta name="description" content="{text}">'))
    page = ('<div itemscope itemtype="https://schema.org/SoftwareApplication">'
            f'<meta itemprop="description" content="{text}"></div>')
    assert honesty._reader(page).json_errors == [], 'Control: no malformed structured-data refusal.'
    assert honesty.claims(honesty._page_copy(page)), 'Microdata description copy was omitted.'
