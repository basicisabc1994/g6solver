"""Finding 5: the closed abbreviation list is matched in any case, and it holds ordinary English words and common
acronyms (no, etc, max, min, sun, sat, ed, co, ca, sec, est ...). A sentence that really ends in one of them never
ends, so the independent sentence after it is refused as a G6 claim."""
import pytest

# Each first sentence ends in a word that is in ABBREVIATIONS; the second is about something else.
ENDS = {
    'etc': 'G6 runs the tests, the linter, the type checker, etc.',
    'no': 'When the evidence does not support the claim, G6 says no.',
    'SAT': 'G6 reduces the constraint to SAT.',
    'CA': 'G6 never trusts an unknown CA.',
    'max': 'G6 repairs a patch three times at the max.',
    'min': 'G6 keeps the waiting time to a min.',
    'Inc': 'G6 is not a product of Example Inc.',
    'Co': 'G6 was tried at Example and Co.',
    'ed': 'G6 follows the handbook, second ed.',
    'Sun': 'G6 ran the nightly check on Sat and Sun.',
    'Dec': 'G6 0.3.0 is planned for Dec.',
    'EST': 'G6 runs its nightly check at 02:00 EST.',
    'SEC': 'G6 does not file anything with the SEC.',
    'al': 'G6 follows the design of Dijkstra et al.',
}


def test_every_case_ends_in_a_listed_word(honesty):
    for word, first in ENDS.items():
        assert first.endswith(f' {word}.') and word.casefold() in honesty.ABBREVIATIONS, word


@pytest.mark.parametrize('word', sorted(ENDS))
def test_a_sentence_that_ends_in_an_ordinary_word_of_the_list_ends(honesty, word):
    first, second = ENDS[word], 'Formal methods verify programs against a specification.'
    # controls: a paragraph break parts them, and a first sentence that ends in another word is parted as well
    assert honesty.g6_claims(f'<p>{first}</p><p>{second}</p>') == []
    assert honesty.g6_claims(f'<p>{first[:-len(word) - 1]}today. {second}</p>') == []
    assert honesty.g6_claims(f'<p>{first} {second}</p>') == [], (
        f'An independent sentence was refused as a G6 claim because the sentence before it ends in "{word}."')
