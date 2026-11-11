def test_sentence_ending_in_language_c_is_not_an_initial(honesty):
    source = 'G6 checks Python and C. Other tools verify programs.'
    assert honesty.g6_claims('<p>G6 checks Python. Other tools verify programs.</p>') == []
    assert honesty.g6_claims('<p>' + source + '</p>') == [], 'Independent claim about other tools was attributed to G6.'
