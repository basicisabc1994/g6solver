import json


def test_a_literal_keyword_is_not_a_url_because_it_starts_with_hash(honesty):
    data = {'@context': 'https://schema.org', '@type': 'Article',
            'description': 'G6 checks patches.', 'keywords': ['#verified', '#G6']}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    honesty.structured_inventory(page)
    assert honesty.claims(data['keywords'][0]), 'Control: the literal keyword contains the claim word.'
    assert honesty.claims(honesty._page_copy(page)), 'An SEO keyword was mistaken for a machine URL.'
