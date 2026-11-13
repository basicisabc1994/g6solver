def test_prefixed_words_can_be_mentioned_without_making_a_claim(honesty):
    assert honesty.claims(honesty._markdown_copy('Avoid the word "verified" in public copy.')) == []
    copy = honesty._markdown_copy('Avoid the word "unverified" in public copy.')
    assert honesty.claims(copy) == [], 'The mention rule does not share the matcher\'s prefixed vocabulary.'
