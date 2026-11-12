"""Finding 10: in an essay a list item is read with its lead-in only when the lead-in is one sentence that ends in a
bare colon straight before a list. The members below are not among those the author records as open (two lists, no
colon, a table row): a lead-in of two sentences, a colon followed by the essays' own citation marker, a lead-in
before a table or a quotation, and a question in a summary answered in its details."""
import pytest

CITE = '<sup><a href="#ref-2" class="text-g6-green hover:text-white transition-colors no-underline">[2]</a></sup>'
ITEMS = '<ul><li>checks a patch.</li><li>verifies every patch.</li></ul>'
PAGES = {
    'a lead-in of two sentences': f'<p>G6 takes a different approach. It does two things:</p>{ITEMS}',
    'a citation after the colon': f'<p>G6 does two things:{CITE}</p>{ITEMS}',
    'a note in brackets after the colon': f'<p>G6 does two things: <em>(in this order)</em></p>{ITEMS}',
    'a lead-in before a table': '<p>G6 does two things:</p><table><tr><td>Checks a patch.</td></tr>'
                                '<tr><td>Verifies every patch.</td></tr></table>',
    'a lead-in before a quotation': '<p>G6 says this of every patch:</p><blockquote><p>Verified.</p></blockquote>',
    'a question in a summary': '<details><summary>What does G6 do with a patch?</summary>'
                               '<p>Verifies it before release.</p></details>',
}


@pytest.mark.parametrize('case', sorted(PAGES))
def test_a_claim_under_a_g6_lead_in_is_found(honesty, case):
    # control: the plain lead-in the check knows is found, and the claim is a claim for ordinary copy
    assert honesty.g6_claims(f'<p>G6 does two things:</p>{ITEMS}')
    assert honesty.claims(honesty._page_copy(PAGES[case]))
    assert honesty.g6_claims(PAGES[case]), f'The claim lost its G6 subject ({case}).'
