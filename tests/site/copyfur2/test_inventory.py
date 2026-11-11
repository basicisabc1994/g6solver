# SPDX-License-Identifier: Apache-2.0
"""Repair 2's root inventories. Each walks a table the check itself reads (the sentence ends, HTML's list containers
and text elements, the structured-data fields and JSON-LD's keywords, the embedded documents) and plants a case on
every row, so a row added to a table is covered and a reader that stops using its table fails here."""
import ast
import html
import inspect
import json
import string

import pytest

CLAIM = 'G6 verifies every patch.'
OTHERS = 'tools verify programs.'


def ld(data):
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


def copy_claims(honesty, page):
    return honesty.claims(honesty._page_copy(page))


def calls(function):
    return {ast.unparse(node.func) for node in ast.walk(ast.parse(inspect.getsource(function).lstrip()))
            if isinstance(node, ast.Call)}


# Root A: one table of sentence ends, one reading of a unit, a subject that reaches every item under it.

def test_every_listed_abbreviation_keeps_a_subject_with_its_claim(honesty):
    assert {'mr', 'mrs', 'dr', 'prof', 'revd', 'messrs', 'vs', 'etc'} <= honesty.ABBREVIATIONS
    for word in sorted(honesty.ABBREVIATIONS):
        for form in (word, word.capitalize(), word.upper()):
            text = f'G6 uses {form}. Smith to verify every patch.'
            assert honesty.sentences(text) == [text], form
            assert honesty.g6_claims(f'<p>{text}</p>') == [text], form
            assert honesty.g6_claims(f'<p>G6 uses ({form}. Smith) to verify every patch.</p>'), form


@pytest.mark.parametrize('token', [*string.ascii_letters, 'U.S', 'e.g', 'i.e', 'J.R', 'p.m', 'E.G'])
def test_an_initial_keeps_a_subject_with_its_claim(honesty, token):
    text = f'G6 uses {token}. Smith to verify every patch.'
    assert honesty.sentences(text) == [text]
    assert honesty.g6_claims(f'<p>{text}</p>') == [text]


@pytest.mark.parametrize('dots', ['..', '...', '…', '....'])
def test_an_ellipsis_is_not_a_certain_end(honesty, dots):
    assert honesty.g6_claims(f'<p>G6 checks it{dots} Other {OTHERS}</p>')


FIRSTS = ('G6 checks it', 'G6 is OK', 'G6 checks Xy', 'G6 checks Xyz', 'G6 runs version 1.2', 'G6 runs v0.3.0',
          "G6 checks what other systems don't", 'G6 checks what other systems don’t', 'G6 uses example.com')
STARTS = ('Other', '3 other', '(Other', '[Other', '{Other', '"Other', "'Other", '“Other', '‘Other',
          'eBPF', 'iVerify', '(3 other')


def test_a_certain_end_always_ends_a_sentence(honesty):
    closers = ['', *honesty.CLOSERS, honesty.CLOSERS]
    for terminal in [*honesty.TERMINALS, '?!', '!!']:
        for close in closers:
            for first in FIRSTS:
                for start in STARTS:
                    second = f'{start} {OTHERS}'
                    text = f'{first}{terminal}{close} {second}'
                    assert honesty.sentences(text) == [f'{first}{terminal}{close}', second], text
                    assert honesty.g6_claims('<p>' + html.escape(text, quote=False) + '</p>') == [], text
    # an exclamation or a question mark ends a sentence after a single letter or a listed abbreviation too
    for first in ('Does G6 check C', 'G6 checks tests etc', 'G6 asked Dr'):
        for terminal in ('!', '?'):
            assert honesty.g6_claims(f'<p>{first}{terminal} Other {OTHERS}</p>') == [], first


@pytest.mark.parametrize('text', [
    'G6 uses abbr. tools to verify every patch.',                  # an abbreviation outside the list
    'G6 uses Yahoo! to verify every patch.',                       # a name that ends in a mark
    'G6 answers "is it safe?" and verifies every patch.',          # a quoted question inside a sentence
    'G6 answers (is it safe?) and verifies every patch.',
    "G6 says 'done.' and verifies every patch.",
    'G6 checks it. pytest verifies nothing.',                      # the cost: a sentence that starts in lower case
])
def test_a_mark_before_a_lower_case_word_keeps_the_sentence_together(honesty, text):
    assert honesty.sentences(text) == [text]
    assert honesty.g6_claims(f'<p>{text}</p>') == [text]


def test_the_sentence_reader_decides_by_its_table(honesty):
    assert {'TERMINALS', 'CLOSERS', '_ends_sentence'} <= {
        node.id for node in ast.walk(ast.parse(inspect.getsource(honesty.sentences))) if isinstance(node, ast.Name)}
    names = {node.id for node in ast.walk(ast.parse(inspect.getsource(honesty._ends_sentence)))
             if isinstance(node, ast.Name)}
    assert {'ABBREVIATIONS', 'OPENERS'} <= names
    # a word's length or capitals before the mark no longer decide anything
    assert not {'isupper', 'istitle'} & {node.attr for node in ast.walk(ast.parse(inspect.getsource(honesty._ends_sentence)))
                                         if isinstance(node, ast.Attribute)}


CODE_PAGES = (
    '<p>G6 calls <code>check</code>. Other tools verify programs.</p>',
    '<p>G6 calls <code>check</code>! Other tools verify programs.</p>',
    '<p>G6 calls <code>x</code>. Other tools verify programs.</p>',
    '<p>G6 calls <code>etc</code>. Other tools verify programs.</p>',
    '<p>G6 calls "<code>check_patch</code>." Other tools verify programs.</p>',
    '<p>The word CODE and <code>G6</code> verifies it. CODE and <code>x_y</code> stay.</p>',
    '<p><code>G6</code> uses Dr. <code>smith</code> to verify it.</p>',
    '<p>G6 calls <code>verify every patch</code>. Other tools check.</p>',
    '<p>G6 calls veri<code>fies</code>. Other tools check.</p>',
)


@pytest.mark.parametrize('page', CODE_PAGES)
def test_both_checkers_read_one_text_and_the_same_sentences(honesty, monkeypatch, page):
    assert list(inspect.signature(honesty._resolve).parameters) == ['unit', 'exempt']    # no second stand-in
    assert '_resolve' in calls(honesty.g6_claims) and '_resolve' in calls(honesty._page_copy)
    seen = []
    original = honesty.sentences

    def read(text):
        seen.append(text)
        return original(text)

    monkeypatch.setattr(honesty, 'sentences', read)
    monkeypatch.setattr(honesty, 'claims', lambda text: [])
    honesty.g6_claims(page)
    assert seen == [honesty._page_copy(page)]      # one unit: the essay check split exactly the ordinary copy


def test_an_exempt_code_span_is_restored_by_its_place(honesty):
    page = CODE_PAGES[5]
    assert honesty._page_copy(page) == 'The word CODE and CODE verifies it. CODE and CODE stay.'
    assert honesty.g6_claims(page) == ['The word CODE and G6 verifies it.']
    assert honesty.g6_claims('<p>G6 calls <code>check</code>. Other tools verify programs.</p>') == []
    assert honesty.g6_claims(CODE_PAGES[6]) == ['G6 uses Dr. smith to verify it.']


def test_the_list_containers_are_html_s(honesty):
    assert honesty._LISTS == {'ul', 'ol', 'menu', 'dl'}      # HTML Living Standard, 4.4 Grouping content
    assert honesty._LISTS <= honesty._BLOCK and {'li', 'dt', 'dd'} <= honesty._BLOCK


def items(container, texts):
    if container == 'dl':
        return ''.join(f'<dt>{text}</dt><dd>and {text}</dd>' for text in texts)
    return ''.join(f'<li>{text}</li>' for text in texts)


@pytest.mark.parametrize('place', [0, 1, 2])
def test_a_lead_in_reaches_every_item_of_every_list_container(honesty, place):
    for container in sorted(honesty._LISTS):
        texts = ['check a patch.', 'challenge an answer.', 'report it.']
        assert honesty.g6_claims(f'<p>G6 can:</p><{container}>{items(container, texts)}</{container}>') == []
        texts[place] = 'verify every patch.'
        page = f'<p>G6 can:</p><{container}>{items(container, texts)}</{container}>'
        assert honesty.g6_claims(page), (container, place)
        nested = (f'<p>G6 can:</p><{container}>{items(container, ["check a patch."])}'
                  f'<li><ul>{items("ul", texts)}</ul></li></{container}>')
        assert honesty.g6_claims(nested), (container, place)
        after = f'<p>G6 can:</p><{container}>{items(container, ["check."])}</{container}><p>Other {OTHERS}</p>'
        assert honesty.g6_claims(after) == [], container
        inside = f'<blockquote><p>G6 can:</p><{container}>{items(container, texts)}</{container}></blockquote>'
        assert honesty.g6_claims(inside), (container, place)


@pytest.mark.parametrize('place', [0, 1, 2, 3])
@pytest.mark.parametrize('term', ['G6 can:', 'G6'])
@pytest.mark.parametrize('shape', ['plain', 'groups in div', 'end tags left out'])
def test_a_term_reaches_every_description_of_its_group(honesty, place, term, shape):
    texts = ['check a patch.', 'challenge an answer.', 'report it.', 'stop.']
    texts[place] = 'verify every patch.'
    if shape == 'end tags left out':
        page = f'<dl><dt>{term}' + ''.join(f'<dd>{text}' for text in texts) + '</dl>'
    else:
        group = f'<dt>{term}</dt>' + ''.join(f'<dd>{text}</dd>' for text in texts)
        page = f'<dl><div>{group}</div></dl>' if shape == 'groups in div' else f'<dl>{group}</dl>'
    assert honesty.g6_claims(page), page


def test_a_term_stops_at_its_group_and_at_its_list(honesty):
    others = '<dt>Other tools</dt><dd>verify programs.</dd><dd>also verify programs.</dd>'
    assert honesty.g6_claims(f'<dl><dt>G6</dt><dd>checks a patch.</dd>{others}</dl>') == []
    assert honesty.g6_claims(f'<dl><div><dt>G6</dt><dd>checks.</dd></div><div>{others}</div></dl>') == []
    assert honesty.g6_claims(f'<dl><dt>G6<dd>checks.{others}</dl>') == []
    assert honesty.g6_claims(f'<dl><dt>G6</dt><dd>checks a patch.</dd></dl><p>Other {OTHERS}</p>') == []
    # two terms name one group: each description is read with both
    assert honesty.g6_claims('<dl><dt>The gate</dt><dt>G6</dt><dd>checks.</dd><dd>verifies every patch.</dd></dl>')
    assert honesty.g6_claims('<dl><dt>G6</dt><dt>The gate</dt><dd>checks.</dd><dd>verifies every patch.</dd></dl>')
    # a list inside a description is under that description's term
    assert honesty.g6_claims('<dl><dt>G6</dt><dd><ul><li>checks.</li><li>verifies every patch.</li></ul></dd></dl>')


# Root C: structured data is read by what each field holds; HTML's text elements are text; an open block fails.

ADDRESSES = ('/verify/', '../verification/', './verified/', '#verified', 'verification.html', 'verified',
             'essays/verification/', 'https://example.org/verification', '//example.org/verified',
             'mailto:verification@example.org', 'G6:verified')


def test_the_field_tables_partition_what_the_check_knows(honesty):
    words = [honesty._COPY_FIELDS, honesty._OBJECT_FIELDS, honesty._MACHINE_FIELDS]
    for one in words:
        for other in words:
            assert one is other or not (one & other), sorted(one & other)
    assert honesty._MACHINE == honesty._MACHINE_FIELDS | (honesty._JSONLD_KEYWORDS - honesty._JSONLD_VALUES)
    assert honesty._JSONLD_VALUES <= honesty._JSONLD_KEYWORDS
    assert 'mainEntityOfPage' in honesty._MACHINE_FIELDS and 'keywords' in honesty._COPY_FIELDS


def test_a_structured_string_is_read_by_its_field_never_by_its_shape(honesty):
    # from the code: the reader tests a string's field and nothing else, and the address test is gone
    assert calls(honesty._fields) <= {'_fields', 'isinstance', 'value.items', 'item.values'}, calls(honesty._fields)
    assert not hasattr(honesty, '_is_address')
    text_fields = (honesty._COPY_FIELDS | honesty._OBJECT_FIELDS | {'unclassifiedField'}) - honesty._JSONLD_KEYWORDS
    for name in sorted(text_fields):
        for value in (*ADDRESSES, CLAIM):
            for data in ({name: value}, {name: [value]}, {name: {'@value': value}}, {'@graph': [{name: value}]}):
                assert copy_claims(honesty, ld(data)), (name, value)
    machine = honesty._MACHINE_FIELDS - honesty._JSONLD_KEYWORDS
    for name in sorted(machine):
        for value in (*ADDRESSES, CLAIM):
            for data in ({name: value}, {name: [value]}, {name: {'@value': value}}, {name: {'@list': [value]}}):
                assert copy_claims(honesty, ld(data)) == [], (name, value)
        # an object beneath a machine field is still read by its own fields
        assert copy_claims(honesty, ld({name: {'caption': CLAIM, 'url': '/verified.png'}})) == [CLAIM], name


def test_a_reverse_property_holds_addresses_or_nodes(honesty):
    data = {'@reverse': {'author': ['/verify', {'name': CLAIM, '@id': '/verified'}], 'publisher': '#verified'}}
    honesty.structured_inventory(ld(data))
    assert copy_claims(honesty, ld(data)) == [CLAIM]


def test_the_text_elements_are_html_s_and_the_parser_reads_them_as_text(honesty):
    # HTML Living Standard, 13.1.2: raw text elements, and escapable raw text elements
    assert set(honesty.TEXT_ELEMENTS) == {'script', 'style'} | {'textarea', 'title'}
    assert honesty._Reader.CDATA_CONTENT_ELEMENTS == tuple(honesty.TEXT_ELEMENTS)
    assert honesty._Reader.RCDATA_CONTENT_ELEMENTS == ()
    for tag, shown in honesty.TEXT_ELEMENTS.items():
        for name in (tag, tag.upper()):
            literal = f'<{name}>G6 produces <code>verified</code> answers.</{name}>'
            decoded = f'<{name}>G6 produces &lt;b&gt;veri&#102;ied&lt;/b&gt; answers &amp; more.</{name}>'
            comment = f'<{name}>G6 produces <!-- <code> -->verified<!-- </code> --> answers.</{name}>'
            for page in (literal, decoded, comment):
                assert bool(copy_claims(honesty, page)) is shown, page
                assert bool(honesty.g6_claims(page)) is shown, page
            # markup after the element is markup again
            assert copy_claims(honesty, literal + '<p>The state is <code>verified</code>.</p>') == (
                copy_claims(honesty, literal))
    assert honesty._page_copy('<textarea>a &lt;b&gt; &amp; <i>c</i></textarea>') == 'a <b> & <i>c</i>'


def test_an_element_left_open_at_the_end_is_read_and_structured_data_fails(honesty):
    open_script = '<script type="application/ld+json">'
    for body in ('{"articleBody": "G6 verifies every patch."}', '{"articleBody": broken}', '', '{"articleBody": "x"',
                 '{"articleBody": "Checked."}<p>After.</p>'):
        for page in (open_script + body, '<p>Before.</p>' + open_script + body,
                     '<iframe srcdoc="' + html.escape(open_script + body) + '"></iframe>'):
            with pytest.raises(AssertionError):
                honesty.structured_inventory(page)
    assert copy_claims(honesty, open_script + '{"articleBody": "G6 verifies every patch."}')
    honesty.structured_inventory(open_script + '{"articleBody": "Checked."}</script>')
    for tag, shown in honesty.TEXT_ELEMENTS.items():
        assert bool(copy_claims(honesty, f'<p>Checked.</p><{tag}>G6 verifies every patch.')) is shown, tag
    # a code span left open is read to the end, as prose unless it only names something
    assert copy_claims(honesty, '<p>The patch is <code>not verified')
    assert copy_claims(honesty, '<p>G6 calls <code>verify_answer') == []


# Root D: everything a person is shown: microdata, an embedded document, the documents the site's pages link to.

def test_a_microdata_value_is_read_as_its_field_is(honesty):
    # HTML Living Standard, 5.2.4 Values: the attributes that give a property's value, addresses apart
    assert honesty.MICRODATA_VALUES == {'meta': 'content', 'data': 'value', 'meter': 'value', 'time': 'datetime'}
    fields = (honesty._COPY_FIELDS | honesty._OBJECT_FIELDS | honesty._MACHINE_FIELDS) - honesty._JSONLD_KEYWORDS
    for tag, attribute in honesty.MICRODATA_VALUES.items():
        for name in sorted(fields):
            shown = name not in honesty._MACHINE
            for value in (CLAIM, *ADDRESSES):
                page = (f'<div itemscope itemtype="https://schema.org/Thing">'
                        f'<{tag} itemprop="{name}" {attribute}="{value}"></{tag}></div>')
                honesty.structured_inventory(page)
                assert bool(copy_claims(honesty, page)) is shown, (tag, name, value)
            assert bool(honesty.g6_claims(f'<{tag} itemprop="{name}" {attribute}="{CLAIM}"></{tag}>')) is shown, name
        if tag != 'meta':        # without a property the attribute is the element's own machine value
            assert copy_claims(honesty, f'<{tag} {attribute}="{CLAIM}"></{tag}>') == [], tag
    # several names on one element: read when any of them is a text field
    assert copy_claims(honesty, f'<meta itemprop="datePublished description" content="{CLAIM}">')
    assert copy_claims(honesty, f'<meta itemprop="datePublished url" content="{CLAIM}">') == []
    # a field nobody has classified is read at once, and fails the inventory by its name
    unknown = f'<meta itemprop="newReaderFacingField" content="{CLAIM}">'
    assert copy_claims(honesty, unknown)
    with pytest.raises(AssertionError, match='newReaderFacingField'):
        honesty.structured_inventory(unknown)
    with pytest.raises(AssertionError, match='newReaderFacingField'):
        honesty.structured_inventory('<iframe srcdoc="' + html.escape(unknown) + '"></iframe>')


def test_an_embedded_document_is_read_as_a_document(honesty):
    assert honesty.EMBEDDED_DOCUMENTS == {'srcdoc': ('iframe',)}       # HTML Living Standard: iframe's srcdoc
    documents = {
        'text': f'<p>{CLAIM}</p>',
        'shown attribute': f'<img alt="{CLAIM}">',
        'meta': f'<meta name="description" content="{CLAIM}">',
        'microdata': f'<meta itemprop="description" content="{CLAIM}">',
        'structured data': ld({'articleBody': CLAIM}),
        'text area': f'<textarea>{CLAIM}</textarea>',
        'list': '<p>G6 can:</p><ul><li>check.</li><li>verify every patch.</li></ul>',
        'a document in a document': '<iframe srcdoc="' + html.escape(f'<p>{CLAIM}</p>') + '"></iframe>',
    }
    for attribute, tags in honesty.EMBEDDED_DOCUMENTS.items():
        for tag in tags:
            for name, inner in documents.items():
                assert copy_claims(honesty, inner), name
                page = f'<{tag} title="Example output" {attribute}="{html.escape(inner)}"></{tag}>'
                assert copy_claims(honesty, page), (tag, name)
                assert honesty.g6_claims(page), (tag, name)
                assert honesty.g6_claims(f'<p>Other {OTHERS}</p>' + page.replace('verif', 'check')) == [], name
            linked = f'<{tag} {attribute}="' + html.escape('<a href="docs/x.md">x</a>') + f'"></{tag}>'
            assert honesty._reader(linked).links == ['docs/x.md']
            for broken in (ld({'newReaderFacingField': 'Checked.'}),
                           '<script type="application/ld+json">{"articleBody": broken}</script>'):
                with pytest.raises(AssertionError):
                    honesty.structured_inventory(f'<{tag} {attribute}="{html.escape(broken)}"></{tag}>')
    # the embedded document's sentences are its own: the frame's title is not their subject
    frame = '<iframe title="G6" srcdoc="' + html.escape(f'<p>Other {OTHERS}</p>') + '"></iframe>'
    assert honesty.g6_claims(frame) == []


def site_link(honesty):
    return honesty.settings.SITE_URL + '/docs/'


def test_a_link_to_the_site_reaches_every_exported_page_s_links(honesty, monkeypatch, tmp_path):
    assert 'exported_pages' in calls(honesty.public_documents)
    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(honesty, 'OSS', tmp_path)
    repository = honesty.views.evidence.REPO
    names = ('routed', 'not-found', 'framed', 'second-hand', 'apart', 'docs/theory/paper', 'local/site/page')
    for name in names:
        (tmp_path / f'{name}.md').parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f'{name}.md').write_text('Checked.\n', encoding='utf-8')
    (tmp_path / 'routed.md').write_text('[more](second-hand.md)\n', encoding='utf-8')
    pages = {
        'docs/index.html': f'<a href="{repository}routed.md">a</a><a href="/local/site/page.md">b</a>',
        '404.html': f'<map><area href="{repository}not-found.md#part"></map>',
        'graph/index.html': '<iframe srcdoc="' + html.escape(f'<a href="{repository}framed.md">c</a>') + '"></iframe>'
                            f'<a href="{repository}docs/theory/paper.md">d</a><a href="{repository}missing.md">e</a>',
    }
    built = []

    def export():
        built.append(1)
        return pages

    monkeypatch.setattr(honesty, 'exported_pages', export)
    seed, plain = tmp_path / 'seed.md', tmp_path / 'plain.md'
    seed.write_text(f'[The site]({site_link(honesty)}) and [again]({site_link(honesty)}).\n', encoding='utf-8')
    plain.write_text('[Another](apart.md)\n', encoding='utf-8')
    # a page's link to a repository document is followed (from an a, an area, a framed document), and so are that
    # document's links; a page's local link is a page of the site, and an excluded or missing document stays out
    found = {path.name for path in honesty.public_documents([seed])}
    assert found == {'seed.md', 'routed.md', 'not-found.md', 'framed.md', 'second-hand.md'}, found
    assert built == [1]                                    # the export is read once however many links reach it
    # a set of documents that never links to the site is closed over its own links alone
    assert {path.name for path in honesty.public_documents([plain])} == {'plain.md', 'apart.md'}
    assert built == [1]
    # a document reached later links to the site: the site is read then
    plain.write_text('[Next](seed.md)\n', encoding='utf-8')
    assert 'not-found.md' in {path.name for path in honesty.public_documents([plain])}


def test_the_inventory_reaches_the_site_and_holds_what_its_pages_link_to(honesty):
    # the documents this check starts from link to the site, so the closure above runs on the real inventory
    site = honesty.urlsplit(honesty.settings.SITE_URL).netloc
    reaching = [path for path in honesty.MARKDOWN
                if any(honesty.urlsplit(link).netloc.lower() == site for link in honesty._reader(
                    honesty._docs_site_html(path.read_text(encoding='utf-8'))).links)]
    assert reaching, 'No document of the inventory links to the site'
    # and every public document a page of the real export links to is in the inventory the copy test walks
    linked = {honesty._linked_document(link, None)
              for page in honesty.exported_pages().values() for link in honesty._reader(page).links} - {None}
    assert linked and linked <= set(honesty.MARKDOWN), sorted(linked - set(honesty.MARKDOWN))
