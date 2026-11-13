import json


def test_json_ld_index_is_machine_data_not_a_claim(honesty):
    data = {'@context': 'https://schema.org', '@type': 'Article',
            'description': {'@value': 'G6 checks patches.', '@index': 'verified'}}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    honesty.structured_inventory(page)
    assert not honesty.claims(data['description']['@value']), 'Control: displayed text makes no claim.'
    assert honesty.claims(honesty._page_copy(page)) == [], 'A JSON-LD index was treated as assurance prose.'
