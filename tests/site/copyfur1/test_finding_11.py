import json


def test_main_entity_url_is_machine_identity_not_prose(honesty):
    url = 'https://g6solver.com/essays/formal-verification-safety-net/'
    nested = {'@context': 'https://schema.org', '@type': 'BlogPosting', 'mainEntityOfPage': {'@type': 'WebPage', '@id': url}}
    scalar = {'@context': 'https://schema.org', '@type': 'BlogPosting', 'mainEntityOfPage': url}
    def page(value):
        return '<script type="application/ld+json">' + json.dumps(value) + '</script>'
    honesty.structured_inventory(page(scalar))
    assert honesty.claims(honesty._page_copy(page(nested))) == []
    assert honesty.claims(honesty._page_copy(page(scalar))) == [], 'A main-entity URL was read as assurance prose.'

