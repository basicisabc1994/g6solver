import pytest


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    'Superseded by the block rule (02e25ab): the essay rule\'s unit is the block and no sentence end is looked for, '
    'so two independent sentences in one block that names G6 are refused together, whatever mark parts them.'))
def test_closing_quote_keeps_independent_sentences_separate(honesty):
    source = '"G6 checks the patch." Other tools verify programs.'
    assert honesty.g6_claims('<p>G6 checks the patch. Other tools verify programs.</p>') == []
    assert honesty.g6_claims('<p>' + source + '</p>') == [], 'Closing quotation mark hid a sentence boundary.'
