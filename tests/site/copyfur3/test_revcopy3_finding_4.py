"""Finding 4: the essays cite a source with a marker after the full stop ("... its context window.[2] The next
sentence"). The table of sentence ends takes only quotation marks and brackets after a mark, so a sentence that ends
with a citation never ends, and the sentence after it is read with its subject: independent copy is refused."""
import re

CITED_END = re.compile(r'[.!?]["\'”’)]*(?:\[\d+\])+(?=\s+["“(\[]?[A-Z])')


def sentence_ends(honesty, text):
    ends, position = set(), 0
    for sentence in honesty.sentences(text):
        position = text.index(sentence, position) + len(sentence)
        ends.add(position)
    return ends


def test_the_sentence_reader_ends_a_sentence_at_a_citation_in_the_real_essays(honesty, site):
    cited = joined = 0
    example = ''
    for page in honesty.PAGES:
        if page.namespace != 'blog':
            continue
        for unit in honesty._units(site('/' + page.path)):
            text = honesty._normalise(honesty._resolve(unit))
            ends = sentence_ends(honesty, text)
            for found in CITED_END.finditer(text):
                cited += 1
                if found.end() not in ends:
                    joined += 1
                    example = example or text[max(0, found.start() - 60):found.end() + 40]
    assert cited > 100, 'Control: the essays cite sources this way.'
    assert joined == 0, (f'{joined} of the {cited} sentences that end with a citation in the real essays are joined '
                         f'to the sentence after them, e.g. "...{example}..."')


def test_a_sentence_after_a_cited_sentence_keeps_its_own_subject(honesty):
    marker = '<sup><a href="#ref-2" class="text-g6-green hover:text-white transition-colors no-underline">[2]</a></sup>'
    # controls: without the citation the two sentences are apart, and a claim in the cited sentence itself is found
    assert honesty.g6_claims('<p>G6 checks every patch. Formal methods verify programs.</p>') == []
    assert honesty.g6_claims(f'<p>G6 verifies every patch.{marker} Formal methods check programs.</p>')
    page = f'<p>G6 checks every patch.{marker} Formal methods verify programs.</p>'
    assert honesty.g6_claims(page) == [], 'A sentence about formal methods was refused as a G6 claim after a citation.'
