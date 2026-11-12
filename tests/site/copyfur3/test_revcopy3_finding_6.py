"""Finding 6: a listed abbreviation or an initial keeps a sentence whole only when it is the whole word between two
spaces. Joined to another word by a dash, a hyphen or a slash (a parenthesis in dashes, "non-U.S.", "Lt.-Col.",
"Mr./Mrs.") it ends the sentence, and the claim after it loses its G6 subject."""
import pytest

JOINS = {
    'a dash before a title': 'G6 asks its reviewer—Dr. Smith—to verify every patch.',
    'a dash before a listed word': 'G6 runs the old gate—ver. 2 of it—to verify every patch.',
    'a hyphen before initials': 'G6 follows non-U.S. English rules to verify every patch.',
    'a hyphen between two titles': 'G6 asks Lt.-Col. Smith to verify every patch.',
    'a slash between two titles': 'G6 asks Mr./Mrs. Smith to verify every patch.',
    'a hyphen before a month': 'G6 uses post-Jan. 2026 data to verify every patch.',
}


@pytest.mark.parametrize('case', sorted(JOINS))
def test_a_listed_abbreviation_joined_to_another_word_keeps_the_sentence_whole(honesty, case):
    text = JOINS[case]
    assert honesty.claims(text), 'Control: ordinary copy reads the claim.'
    # control: the same abbreviation standing alone keeps the sentence whole
    assert honesty.g6_claims('<p>G6 asks Dr. Smith to verify every patch.</p>')
    assert honesty.g6_claims(f'<p>{text}</p>'), f'The claim lost its G6 subject at a false sentence end ({case}).'
