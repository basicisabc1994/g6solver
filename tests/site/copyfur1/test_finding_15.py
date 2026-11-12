def test_accessible_value_text_is_reader_facing_copy(honesty):
    page = ('<div role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100" '
            'aria-valuetext="G6 verified every patch."></div>')
    assert honesty.claims(honesty._page_copy(page)), 'The assurance announced to an assistive reader was skipped.'
