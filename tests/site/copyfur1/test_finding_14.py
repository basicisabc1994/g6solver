import json


def test_context_term_definitions_are_machine_data_at_every_depth(honesty):
    data = {'@context': {'name': 'https://example.org/verification/name'}, 'name': 'G6'}
    page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
    honesty.structured_inventory(page)
    assert honesty.claims(honesty._page_copy(page)) == [], 'A context IRI definition was read as public prose.'
