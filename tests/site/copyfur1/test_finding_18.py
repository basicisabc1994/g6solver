def test_the_sites_own_product_name_identifies_a_g6_claim(honesty):
    template = (honesty.OSS / 'web/templates/blog/base_post.html').read_text(encoding='utf-8')
    assert '"name": "G6Solver"' in template
    assert honesty.g6_claims('<p>G6 verifies every answer.</p>')
    assert honesty.g6_claims('<p>G6Solver verifies every answer.</p>'), 'The site\'s G6Solver product name lost the claim subject.'
