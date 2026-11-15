import pytest


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    'Superseded by the block rule (02e25ab): the essay rule\'s unit is the block and no sentence end is looked for, '
    'so two independent sentences in one block that names G6 are refused together. 07c4a96 had made these nine pass '
    'with the table of sentence ends that 02e25ab deleted.'))
@pytest.mark.parametrize('first,second', [
    ('G6 is OK!', 'Other tools verify programs.'),
    ('Does G6 check C?', 'Other tools verify programs.'),
    ('G6 checks version 1.2.', 'Other tools verify programs.'),
    ('G6 checks it.', '3 other tools verify programs.'),
    ('G6 checks it.', '(Other tools verify programs.)'),
    ('G6 checks it.', '[Other tools verify programs.]'),
    ('G6 checks it.', 'eBPF tools verify programs.'),
    ("G6 checks what other systems don't.", 'Other tools verify programs.'),
    ('G6 checks what other systems don\u2019t.', 'Other tools verify programs.'),
])
def test_certain_independent_sentences_are_not_combined(honesty, first, second):
    assert not honesty.claims(first)
    assert honesty.claims(second)
    assert not honesty.g6_claims(f'<p>{first}</p><p>{second}</p>'), 'Control: separate subjects.'
    assert honesty.g6_claims(f'<p>{first} {second}</p>') == [], 'Independent subjects were combined.'
