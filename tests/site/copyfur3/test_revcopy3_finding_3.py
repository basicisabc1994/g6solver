"""Finding 3: a raised or lowered mark typed after a claim word (a note number, the trade mark sign) is folded by
``_normalise`` into letters or digits, which become part of the word, and the word is no longer a claim."""
import sys
import unicodedata


def raised_marks():
    """Every character Unicode defines as a raised or lowered form of other characters: from the Unicode data."""
    return [chr(point) for point in range(0x80, sys.maxunicode + 1)
            if unicodedata.decomposition(chr(point)).startswith(('<super>', '<sub>'))]


def test_a_raised_mark_after_a_claim_word_does_not_hide_it(honesty):
    marks = raised_marks()
    assert {'¹', '²', '™', '℠', '₁'} <= set(marks)     # the note numbers, TM and SM among them
    for plain in ('®', '*', '†'):                                    # controls: marks that are not folded
        assert honesty.claims(honesty._page_copy(f'<p>G6 answers are verified{plain}.</p>')), plain
    missed = [mark for mark in marks if not honesty.claims(honesty._page_copy(f'<p>G6 answers are verified{mark}.</p>'))]
    assert not missed, ('"verified" followed by a raised or lowered mark passed: '
                        + ' '.join(f'U+{ord(mark):04X}' for mark in missed))


def test_the_trade_mark_sign_and_a_note_number_in_every_reader(honesty):
    for mark in ('™', '¹'):
        text = f'G6 Verified{mark} answers for every patch.'
        assert honesty.claims(honesty._markdown_copy(text)), text
        assert honesty.g6_claims(f'<p>{text}</p>'), text
        assert honesty.claims(honesty._page_copy(f'<meta name="description" content="{text}">')), text
