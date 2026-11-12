import json
from urllib.parse import urljoin

import pytest


@pytest.mark.parametrize('address', ['verification.html', 'verification', 'essays/verification/'])
def test_relative_main_entity_addresses_are_machine_values(honesty, address):
    absolute = urljoin('https://g6solver.com/', address)

    def page(value):
        return '<script type="application/ld+json">' + json.dumps({'mainEntityOfPage': value}) + '</script>'

    honesty.structured_inventory(page(address))
    assert not honesty.claims(honesty._page_copy(page(absolute))), 'Control: absolute address is exempt.'
    assert honesty.claims(honesty._page_copy(page(address))) == [], 'A relative entity URL was read as prose.'
