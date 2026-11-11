def test_title_abbreviation_does_not_separate_g6_from_its_claim(honesty):
    sentence = 'G6 uses Dr. Smith to verify every patch.'
    assert honesty.claims(honesty._markdown_copy(sentence))
    assert honesty.g6_claims(honesty.views.markdown_html(sentence)), 'A claim after Dr. was accepted.'
