import pytest


@pytest.mark.parametrize('title', ['Prof.', 'Revd.', 'Messrs.'])
def test_long_title_abbreviation_does_not_lose_the_g6_subject(honesty, title):
    sentence = f'G6 uses {title} Smith to verify every patch.'
    page = honesty.views.markdown_html(sentence)
    assert honesty.claims(honesty._page_copy(page)), 'Control: the word is a claim.'
    assert honesty.g6_claims(page), f'The claim after {title} lost its G6 subject.'


def test_vs_before_a_proper_name_is_not_a_sentence_boundary(honesty):
    sentence = 'G6 compares Alpha vs. Beta results to verify every patch.'
    assert honesty.claims(sentence)
    assert honesty.g6_claims(honesty.views.markdown_html(sentence)), 'The claim after vs. lost its G6 subject.'
