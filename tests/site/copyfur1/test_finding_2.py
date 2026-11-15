import pytest


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    'Kept failing by ruling, then superseded. 9e2bca1 ruled that a single capital before a full stop stays a false '
    'refusal (release ledger review-copyfur1#2); 02e25ab made the block the essay rule\'s unit, so two independent '
    'sentences in one block that names G6 are refused together and no sentence end is looked for.'))
def test_sentence_ending_in_language_c_is_not_an_initial(honesty):
    source = 'G6 checks Python and C. Other tools verify programs.'
    assert honesty.g6_claims('<p>G6 checks Python. Other tools verify programs.</p>') == []
    assert honesty.g6_claims('<p>' + source + '</p>') == [], 'Independent claim about other tools was attributed to G6.'
