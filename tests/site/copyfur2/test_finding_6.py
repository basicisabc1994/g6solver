import pytest


def test_unterminated_json_ld_does_not_pass_as_an_empty_inventory(honesty):
    payload = '{"articleBody": broken}'
    prefix = '<script type="application/ld+json">'
    with pytest.raises(AssertionError):
        honesty.structured_inventory(prefix + payload + '</script>')
    with pytest.raises(AssertionError):
        honesty.structured_inventory(prefix + payload)
