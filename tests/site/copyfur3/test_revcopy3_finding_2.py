"""Finding 2: two inline elements with nothing between them are read as one word, so a claim word at the start of
the second is glued to the end of the first and is not read. The site's own pages are built this way (a card's title
and its description, a row of links, a row of tabs), and a raised note marker after a word is the same case."""
import re

import pytest

# Where the real pages put one inline element straight after another: (page, the join, the word to put there).
REAL = {
    'a card on the docs hub: its title, then its description': (
        '/docs/', r'(<span class="h3">[^<]*\w</span><span>)(\w+)', 'Verified'),
    "the home page's links under a limit": (
        '/', r'(class="limit__parts"><a [^>]*>[^<]*\w</a><a [^>]*>)(\w+)', 'Verifier'),
    "the get-started page's tabs": (
        '/get-started/', r'(<button [^>]*role="tab"[^>]*>[^<]*\w</button><button [^>]*role="tab"[^>]*>)(\w+)', 'Verify'),
}


@pytest.mark.parametrize('place', sorted(REAL))
def test_a_word_that_starts_an_inline_element_on_a_real_page_is_read(honesty, site, place):
    path, join, word = REAL[place]
    page = site(path)
    found = re.search(join, page)
    assert found, f'Control: {path} no longer has this markup.'
    assert honesty.claims(honesty._page_copy(page)) == [], 'Control: the real page passes.'
    changed = page[:found.start(2)] + word + page[found.end(2):]
    # control: with white space between the elements, the same word on the same page is read
    spaced = honesty.claims(honesty._page_copy(changed.replace('><', '> <')))
    assert any(word.lower() in claim.lower() for claim in spaced), 'Control: the word is a claim when a space parts the elements.'
    assert honesty.claims(honesty._page_copy(changed)), (
        f'"{word}" at the start of an element on {path} was read as the tail of the word before it and passed.')


@pytest.mark.parametrize('page', [
    '<p>G6 answers are verified<sup>1</sup>.</p>',                               # a note marker
    '<p>G6 answers are verified<sup><a href="#fn1">1</a></sup>.</p>',
    '<p>G6 answers are verified<sub>a</sub>.</p>',
    '<svg><title>Checked</title><desc>Verified by G6</desc></svg>',
    '<p><span class="chip">Checked</span><span class="chip">Verified</span></p>',
    '<nav><a href="/">Home</a><a href="/v/">Verify</a></nav>',
])
def test_a_claim_word_next_to_an_inline_element_is_read(honesty, page):
    assert honesty.claims(honesty._page_copy(page.replace('><', '> <').replace('d<', 'd <'))), 'Control: parted, it is read.'
    assert honesty.claims(honesty._page_copy(page)), 'A claim word was glued to the text of the element beside it.'
