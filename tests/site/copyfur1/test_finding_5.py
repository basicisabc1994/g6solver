def test_unrelated_brand_is_not_a_verification_family_word(honesty):
    copy = honesty._markdown_copy('G6 can inspect Verifone logs.')
    assert honesty.claims(honesty._markdown_copy('G6 can inspect payment-terminal logs.')) == []
    assert honesty.claims(copy) == [], 'The unrelated name Verifone was refused as an assurance claim.'
