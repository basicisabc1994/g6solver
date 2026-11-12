import json
import pytest


def test_non_json_numeric_constants_are_refused_by_inventory(honesty):
    def strict_constant(value):
        raise ValueError(value)

    accepted = []
    for constant in ('NaN', 'Infinity', '-Infinity'):
        body = '{"@type": "Article", "articleBody": "Checked.", "width": ' + constant + '}'
        with pytest.raises(ValueError, match=constant):
            json.loads(body, parse_constant=strict_constant)
        try:
            honesty.structured_inventory('<script type="application/ld+json">' + body + '</script>')
        except AssertionError:
            continue
        accepted.append(constant)
    assert accepted == [], f'Malformed JSON constants accepted: {accepted}'
