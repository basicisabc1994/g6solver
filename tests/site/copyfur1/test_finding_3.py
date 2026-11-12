def test_closing_quote_keeps_independent_sentences_separate(honesty):
    source = '"G6 checks the patch." Other tools verify programs.'
    assert honesty.g6_claims('<p>G6 checks the patch. Other tools verify programs.</p>') == []
    assert honesty.g6_claims('<p>' + source + '</p>') == [], 'Closing quotation mark hid a sentence boundary.'
