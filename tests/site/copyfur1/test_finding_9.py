def test_list_continuation_keeps_the_subject_of_a_g6_claim(honesty):
    source = 'G6 can:\n\n- verify every patch.\n- challenge an answer.'
    page = honesty.views.markdown_html(source)
    assert '<li>verify every patch.</li>' in page
    assert honesty.claims(honesty._page_copy(page))
    assert honesty.g6_claims(page), 'The list continuation lost its G6 subject.'
