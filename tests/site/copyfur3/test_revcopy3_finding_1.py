"""Finding 1: a phrase the owner left (LEFT) is put together from two separate blocks, and the claim word in it is
not read. ``_page_copy`` keeps each block as a paragraph of its own; ``claims`` folds every paragraph break into a
space before it takes the LEFT phrases out."""
import pytest


def halves(honesty):
    """Every way to cut a LEFT phrase in two at a space: derived from the table the check reads."""
    for phrase in honesty.LEFT:
        words = phrase.split(' ')
        for cut in range(1, len(words)):
            yield ' '.join(words[:cut]), ' '.join(words[cut:])


SHAPES = {
    'a heading and its paragraph': '<h3>{head}</h3><p>{tail}</p>',
    'two cells of a table row': '<table><tr><td>{head}</td><td>{tail}</td></tr></table>',
    'two list items': '<ul><li>{head}</li><li>{tail}</li></ul>',
    'an image label and a paragraph': '<img src="x.png" alt="{head}"><p>{tail}</p>',
}


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_a_left_phrase_is_not_put_together_from_two_blocks(honesty, shape):
    cuts = list(halves(honesty))
    assert len(cuts) > 10
    missed = []
    for head, tail in cuts:
        # controls: whole, in one block, the phrase is left; cut in two, one of the halves is a claim on its own
        assert honesty.claims(honesty._page_copy(f'<p>{head} {tail}</p>')) == [], (head, tail)
        assert honesty.claims(head) or honesty.claims(tail), (head, tail)
        if not honesty.claims(honesty._page_copy(SHAPES[shape].format(head=head, tail=tail))):
            missed.append((head, tail))
    assert not missed, f'A claim word passed because the next block completed a LEFT phrase ({shape}): {missed}'


def test_a_heading_that_ends_in_the_word_is_read_whatever_the_next_paragraph_starts_with(honesty):
    assert honesty.claims(honesty._markdown_copy('## Built-in verification\n\nRun it after every change.\n'))   # control
    copy = honesty._markdown_copy('## Built-in verification\n\nPlan a change, then run it.\n')
    assert honesty.claims(copy), 'The heading "Built-in verification" passed because the paragraph after it starts with "Plan".'


def test_a_table_header_is_read_whatever_the_next_cell_holds(honesty):
    table = '| Kind | What G6 does |\n|---|---|\n| Formal | Verification of every patch |\n'
    assert honesty.claims(honesty._markdown_copy(table.replace('Formal', 'Fast'))), 'Control: the cell is a claim.'
    assert honesty.claims(honesty._markdown_copy(table)), 'The cell "Verification of every patch" passed after a cell that says "Formal".'
