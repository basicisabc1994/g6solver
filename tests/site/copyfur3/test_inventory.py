# SPDX-License-Identifier: Apache-2.0
"""Repair 3's root inventories: the text model (blocks, the two readings of an inline join, raised marks), the essay
rule (the block is the unit; one list of exempted blocks) and the inventories by kind (meta names, script types,
exported files, the docs configuration, the package metadata). Each walks a table the check reads, or the Unicode
data or the real tree it reads from, and plants a case on every row."""
import ast
import html
import json
import sys
import tomllib
import unicodedata

import pytest
import yaml

CLAIM = 'G6 verifies every patch.'
OTHERS = 'Other tools verify programs.'


def cc(honesty, page):
    return honesty.claims(honesty._page_copy(page))


def refused(honesty, page):
    """Whether a page's inventory fails."""
    try:
        honesty.structured_inventory(page)
    except AssertionError:
        return True
    return False


# Root A: the text model.

def test_a_named_phrase_is_resolved_inside_one_block_of_any_kind(honesty):
    cuts = [(' '.join(words[:cut]), ' '.join(words[cut:]))
            for words in (phrase.split(' ') for phrase in honesty.LEFT) for cut in range(1, len(words))]
    assert len(cuts) > 10
    blocks = sorted(honesty._BLOCK - {'script', 'style', 'pre'})
    assert len(blocks) > 40
    for head, tail in cuts:
        assert cc(honesty, f'<p>{head} {tail}</p>') == [], (head, tail)
        for tag in blocks:
            assert cc(honesty, f'<{tag}>{head}</{tag}><{tag}>{tail}</{tag}>'), (tag, head, tail)
        # a lead-in and a term are kept apart from the block that stands under them
        assert cc(honesty, f'<p>{head}</p><ul><li>{tail}</li></ul>'), (head, tail)
        assert cc(honesty, f'<dl><dt>{head}</dt><dd>{tail}</dd></dl>'), (head, tail)
        # a field, an attribute and a front-matter value are blocks of their own
        assert cc(honesty, f'<img alt="{head}" title="{tail}">'), (head, tail)
        assert honesty.claims(honesty._markdown_copy(f'---\nname: {head}\ndescription: {tail}\n---\n\n# X\n')), head


def test_a_mention_and_a_state_list_are_resolved_inside_one_block(honesty):
    states = list(honesty.STATE_NAMES)
    assert cc(honesty, '<p>' + ', '.join(states) + '</p>') == []
    assert cc(honesty, ''.join(f'<li>{state}</li>' for state in states))           # three blocks: no list
    assert cc(honesty, ''.join(f'<td>{state}</td>' for state in states))
    assert cc(honesty, '<p>Never say "verified".</p>') == []
    assert cc(honesty, '<p>Never say "</p><p>verified</p><p>".</p>')              # the marks are in other blocks
    # plain text keeps its paragraphs too
    assert honesty.claims('verified\n\nqualified-draft\n\nblocked-escalated')
    assert honesty.claims('verified\nqualified-draft\nblocked-escalated') == []


INLINE = ('a', 'abbr', 'b', 'bdi', 'button', 'cite', 'data', 'dfn', 'em', 'i', 'kbd', 'label', 'mark', 'q', 's',
          'samp', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u', 'var', 'tspan', 'desc', 'text')


def test_an_inline_join_inside_a_word_is_read_both_ways(honesty):
    assert not set(INLINE) & honesty._BLOCK
    for tag in INLINE:
        for page in (f'<p><{tag}>Checked</{tag}><{tag}>Verified</{tag}></p>',       # an element after an element
                     f'<p>Checked<{tag}>Verified</{tag}></p>',                       # an element after text
                     f'<p><{tag}>Checked</{tag}>Verified</p>',                       # text after an element
                     f'<p>Checked<{tag}><{tag}>Verified</{tag}></{tag}></p>'):       # nested
            assert cc(honesty, page), page
        assert cc(honesty, f'<p>G6 veri<{tag}>fies</{tag}> every patch.</p>'), tag    # a word split by markup: whole
        assert cc(honesty, f'<p><{tag}>veri</{tag}><{tag}>fied</{tag}></p>'), tag
        assert cc(honesty, f'<p>Check<{tag}>ed</{tag}> and <{tag}>re</{tag}>checked.</p>') == [], tag
        assert honesty.g6_claims(f'<p><{tag}>G6</{tag}><{tag}>Verified</{tag}></p>'), tag    # both words found
    # an element with no end tag, and a code span beside a word
    assert cc(honesty, '<p>Checked<img src="x.png">Verified</p>')
    assert cc(honesty, '<p><span>Checked</span><code>verified answers</code></p>')
    assert cc(honesty, '<p><span>Checked</span><code>verified</code></p>') == []       # the state, in code format
    # the copy carries both readings, and they differ only at such a join
    copy = honesty._page_copy('<p>a<b>b</b> c <i>d</i>.</p><p>e</p>')
    assert (str(copy), copy.parted) == ('ab c d.\n\ne', 'a b c d.\n\ne')
    plain = honesty._page_copy('<p>G6 verifies patches.</p>')
    assert plain == plain.parted == 'G6 verifies patches.'


def test_a_run_of_raised_marks_is_parted_from_a_word_and_kept_whole(honesty):
    raised = [chr(point) for point in range(0x80, sys.maxunicode + 1)
              if unicodedata.decomposition(chr(point)).startswith(('<super>', '<sub>'))]
    assert len(raised) > 100 and all(honesty._RAISED.fullmatch(mark) for mark in raised)
    for plain in ('®', '*', '†', 'a', '1', 'é'):
        assert not honesty._RAISED.search(plain), plain
    assert honesty._normalise('verified™') == 'verified TM' and honesty._normalise('x¹²') == 'x 12'
    for text in ('G6 answers are verified¹².', 'G6 Verified℠ answers.', 'G6 gives ¹verified answers.'):
        assert honesty.claims(text), text
        assert honesty.g6_claims(f'<p>{text}</p>'), text
    assert honesty.claims('The area is 5 m² and the gas is CO₂.') == []


# Root B: the essay rule. The block is the unit, and one list holds the blocks that are left.

def test_the_block_is_the_essay_rule_s_unit(honesty):
    for text in ('G6 asks its reviewer—Dr. Smith—to verify every patch.',
                 'G6 runs the tests, etc. It verifies nothing by itself.',
                 'G6 checks it! Who verifies it? Another tool.',
                 'G6 cites a source.[2] Formal methods verify programs.',       # the ruling's cost: refused
                 'Formal methods verify programs; G6 checks patches.'):
        assert honesty.g6_claims(f'<p>{text}</p>') == [text], text
    for page in ('<p>G6 checks every patch.</p><p>Formal methods verify programs.</p>',
                 '<h2>G6</h2><p>Formal methods verify programs.</p>',
                 '<p>Formal methods verify programs.</p><p>G6 checks every patch.</p>',
                 '<table><tr><td>G6</td><td>checks</td></tr><tr><td>Others</td><td>verify</td></tr></table>',
                 '<p>The harness checks every patch and verifies nothing.</p>'):
        assert honesty.g6_claims(page) == [], page


def under(tag, blocks):
    inner = {'ul': '<li>{}</li>', 'ol': '<li>{}</li>', 'menu': '<li>{}</li>', 'dl': '<dt>{}</dt><dd>and so on.</dd>',
             'table': '<tr><td>{}</td></tr>', 'blockquote': '<p>{}</p>', 'details': '<p>{}</p>'}[tag]
    return f'<{tag}>' + ''.join(inner.format(block) for block in blocks) + f'</{tag}>'


LEADS = ('G6 does two things:', 'G6 does two things', 'G6 takes another path. It does two things:',
         'G6 does two things:<sup><a href="#ref-2">[2]</a></sup>', 'G6 does two things: <em>(in this order)</em>',
         'What does <code>G6</code> do?')


@pytest.mark.parametrize('place', [0, 1, 2])
def test_a_block_stands_under_the_block_before_its_list_table_quotation_or_details(honesty, place):
    assert set(honesty._UNDER) == {'ul', 'ol', 'menu', 'dl', 'table', 'blockquote', 'details'}
    blocks = ['Checks a patch.', 'Challenges an answer.', 'Reports it.']
    for tag in sorted(honesty._UNDER):
        assert honesty.g6_claims(f'<p>{LEADS[0]}</p>' + under(tag, blocks)) == [], tag
        planted = [*blocks]
        planted[place] = 'Verifies every patch.'
        for lead in LEADS:
            for wrap in ('<p>{}</p>', '<h3>{}</h3>', '<div>{}</div>', '<p>{}</p><div class="wrap">'):
                page = wrap.format(lead) + under(tag, planted)
                assert honesty.g6_claims(page), (tag, lead, wrap)
        # inside another element of the table, and one level down
        assert honesty.g6_claims(f'<p>{LEADS[0]}</p><blockquote>' + under(tag, planted) + '</blockquote>'), tag
        assert honesty.g6_claims(f'<p>{LEADS[0]}</p><ul><li>First:' + under(tag, planted) + '</li></ul>'), tag
        # the lead-in stops where its element closes, and a lead-in that does not name G6 is no subject
        after = f'<p>{LEADS[0]}</p>' + under(tag, blocks) + f'<p>{OTHERS}</p>' + under(tag, [OTHERS])
        assert honesty.g6_claims(after) == [], tag
        assert honesty.g6_claims('<p>Other tools do two things:</p>' + under(tag, planted)) == [], tag
    # an item starts afresh: the item before it is not the lead-in of a list inside it
    assert honesty.g6_claims('<ul><li>G6 checks.</li><li><ul><li>Others verify programs.</li></ul></li></ul>') == []
    assert honesty.g6_claims('<table><tr><td>G6</td><td><ul><li>Others verify programs.</li></ul></td></tr></table>') == []


def test_a_summary_leads_its_details_and_a_term_its_descriptions(honesty):
    details = '<details><summary>What does G6 do with a patch?</summary>{}</details>'
    for body in ('<p>Verifies it before release.</p>', '<p>Checks it.</p><p>Verifies it before release.</p>',
                 '<ul><li>Checks it.</li><li>Verifies it.</li></ul>', '<div><p>Checks.</p><p>Verifies.</p></div>'):
        assert honesty.g6_claims(details.format(body)), body
    assert honesty.g6_claims(details.format('<p>Checks it.</p>') + f'<p>{OTHERS}</p>') == []
    assert honesty.g6_claims('<details><summary>What do others do?</summary><p>They verify programs.</p></details>'
                             + details.format('<p>Checks it.</p>')) == []
    assert honesty.g6_claims('<dl><dt>G6</dt><dd>Checks.</dd><dd>Verifies every patch.</dd></dl>')
    assert honesty.g6_claims('<dl><dt>G6</dt><dd>Checks.</dd><dt>Others</dt><dd>Verify programs.</dd></dl>') == []


def test_the_exemption_list_holds_exact_blocks_of_the_real_essays_and_nothing_else(honesty, client, monkeypatch):
    block = 'G6 cites a source. Formal methods verify programs.'
    page = f'<p>{block}</p><p>G6 verifies every patch.</p>'
    assert honesty.g6_claims(page) == [block, 'G6 verifies every patch.']
    with monkeypatch.context() as patch:
        patch.setitem(honesty.ESSAY_LEFT, honesty.essay_digest(block), 'a planted block: the claim word is about others')
        assert honesty.g6_claims(page) == ['G6 verifies every patch.']               # that block, and no other
        assert honesty.g6_claims(f'<p>{block} So does G6.</p>')                      # a changed block is read again
    # every digest in the list is a block of a real essay that the rule would refuse: none is stale
    assert all(isinstance(reason, str) and len(reason) > 20 for reason in honesty.ESSAY_LEFT.values())
    listed = dict(honesty.ESSAY_LEFT)
    monkeypatch.setattr(honesty, 'ESSAY_LEFT', {})
    refused_blocks = {honesty.essay_digest(text) for essay in honesty.PAGES if essay.namespace == 'blog'
                      for text in honesty.g6_claims(client.get('/' + essay.path).content.decode())}
    assert set(listed) == refused_blocks, 'ESSAY_LEFT must list exactly the real essay blocks the rule refuses'


# Root C: inventories by kind.

def test_every_meta_name_is_shown_machine_or_unclassified(honesty):
    assert not honesty.SHOWN_META_NAMES & honesty.MACHINE_META_NAMES
    for name in sorted(honesty.SHOWN_META_NAMES):
        for key in ('name', 'property'):
            for spelling in (name, name.upper()):
                page = f'<meta {key}="{spelling}" content="{CLAIM}">'
                assert cc(honesty, page) and not refused(honesty, page), page
    for name in sorted(honesty.MACHINE_META_NAMES):
        for key in ('name', 'property'):
            page = f'<meta {key}="{name}" content="{CLAIM}">'
            assert cc(honesty, page) == [] and not refused(honesty, page), page
    for name in ('apple-mobile-web-app-title', 'abstract', 'dc.description', 'og:book:author', 'next-head-count'):
        for page in (f'<meta name="{name}" content="{CLAIM}">', f'<meta property="{name}" content="{CLAIM}">',
                     '<iframe srcdoc="' + html.escape(f'<meta name="{name}" content="Checked.">') + '"></iframe>'):
            assert refused(honesty, page), page
        assert cc(honesty, f'<meta name="{name}" content="{CLAIM}">'), name
    # a content with no name at all is read and reported; a directive to the browser and a charset are neither
    assert cc(honesty, f'<meta content="{CLAIM}">') and refused(honesty, f'<meta content="{CLAIM}">')
    for page in ('<meta http-equiv="refresh" content="5; url=/verified/">', '<meta charset="utf-8">'):
        assert cc(honesty, page) == [] and not refused(honesty, page), page
    # a name that is shown wins over a machine one on the same element
    assert cc(honesty, f'<meta name="twitter:card" property="og:title" content="{CLAIM}">')


def test_every_script_type_is_code_structured_data_or_unclassified(honesty):
    body = json.dumps({'title': CLAIM, 'nodes': [{'guards': 'answers nobody verified'}], 'verified': 'yes', 'n': 3})
    code = [*honesty.SCRIPT_KEYWORDS, *honesty.JAVASCRIPT_TYPES, 'MODULE', ' text/javascript ',
            'text/javascript; charset=utf-8']
    for declared in sorted(code):
        for page in (f'<script type="{declared}">{body}</script>', f'<script>{body}</script>'):
            assert cc(honesty, page) == [] and not refused(honesty, page), declared
    for declared in ('application/json', 'APPLICATION/JSON', 'application/json; charset=utf-8', 'text/json',
                     'application/manifest+json', 'application/geo+json'):
        page = f'<script id="page-data" type="{declared}">{body}</script>'
        assert len(cc(honesty, page)) == 2 and not refused(honesty, page), declared    # each string, not a name
        assert honesty.g6_claims(page), declared
        assert refused(honesty, f'<script type="{declared}">{{"title": broken}}</script>'), declared
        assert refused(honesty, f'<script type="{declared}">{body}'), declared              # left open
        assert cc(honesty, f'<script type="{declared}">{body}'), declared
    structured = f'<script type="application/ld+json">{json.dumps({"description": CLAIM, "url": CLAIM})}</script>'
    assert len(cc(honesty, structured)) == 1 and not refused(honesty, structured)             # by field
    for declared in ('text/template', 'text/plain', 'text/x-handlebars-template', 'application/xml', 'nonsense'):
        page = f'<script type="{declared}">G6 verifies every patch.</script>'
        assert cc(honesty, page) and refused(honesty, page), declared
        assert not refused(honesty, f'<p>{CLAIM}</p>')


def fake_build(monkeypatch, files):
    from pages.management.commands import build_static

    def build(out):
        for name, text in files.items():
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_text(text, encoding='utf-8')
        return []

    monkeypatch.setattr(build_static, 'build', build)


def test_every_exported_file_has_a_kind_and_a_file_of_no_kind_fails(honesty, monkeypatch, scratch):
    assert not set(honesty.EXPORT_READ) & honesty.EXPORT_MACHINE
    assert set(honesty.EXPORT_READ.values()) == {'page', 'manifest', 'text', 'data'}
    for key, kind in honesty.EXPORT_READ.items():
        assert honesty.export_kind(f'a/b{key}') == kind and honesty.export_kind(f'A{key.upper()}') == kind
    for key in honesty.EXPORT_MACHINE:
        assert honesty.export_kind(key if not key.startswith('.') or key == '.g6-site-build' else f'a/b{key}') == 'machine'
    for tree in honesty.EXPORT_LEFT:
        assert len(honesty.EXPORT_LEFT[tree]) > 20
        for key, kind in honesty.EXPORT_READ.items():
            assert honesty.export_kind(f'{tree}x{key}') == ('page' if kind == 'page' else 'left'), (tree, key)
        assert honesty.export_kind(f'{tree}LICENSE') == 'left'
    for name in ('notes.rst', 'LICENSE', 'static/data.yaml', 'static/app.wasm', 'page.htm'):
        with pytest.raises(AssertionError, match='No reader'):
            honesty.export_kind(name)
    # through the export itself, with a build that writes one file of each kind
    monkeypatch.setattr(honesty, 'OSS', scratch)
    manifest = {'name': 'G6', 'description': CLAIM, 'start_url': '/verified/', 'icons': [{'src': '/verified.png'}],
                'shortcuts': [{'name': 'Verify a patch', 'url': '/verify/'}]}
    files = {'index.html': f'<p>{CLAIM}</p>', 'static/site.webmanifest': json.dumps(manifest),
             'robots.txt': 'User-agent: *\n', 'static/notes.txt': f'Notes.\n\n{CLAIM}\n',
             'static/data/labels.json': json.dumps({'verified': ['Checked.', CLAIM]}),
             'static/images/logo.svg': f'<svg><title>Logo</title><text>{CLAIM}</text></svg>',
             'static/css/site.css': '.verified {}', 'static/js/app.js': 'var verified = 1;', '_headers': '/*',
             'static/vendor/lib/README.md': 'This library verifies input.',
             'static/graph/knowledge-graph.json': '{"summary": "verifies"}'}
    fake_build(monkeypatch, files)
    exported = honesty.exported_pages()
    assert set(exported) == {'index.html', 'static/site.webmanifest', 'robots.txt', 'static/notes.txt',
                             'static/data/labels.json', 'static/images/logo.svg'}
    for name, text in exported.items():
        honesty.export_inventory(name, text)
        assert bool(honesty.claims(honesty.export_copy(name, text))) is (name != 'robots.txt'), name
    assert len(honesty.claims(honesty.export_copy('static/site.webmanifest', exported['static/site.webmanifest']))) == 2
    assert len(honesty.claims(honesty.export_copy('static/data/labels.json', exported['static/data/labels.json']))) == 1
    assert not list((scratch / '.build').iterdir())
    # a manifest member nobody has classified is read and fails; a file of no kind fails the whole export
    odd = json.dumps({'name': 'G6', 'tagline': CLAIM})
    assert honesty.claims(honesty.export_copy('static/site.webmanifest', odd))
    with pytest.raises(AssertionError, match='tagline'):
        honesty.export_inventory('static/site.webmanifest', odd)
    fake_build(monkeypatch, {**files, 'static/notes.rst': CLAIM})
    with pytest.raises(AssertionError, match='No reader'):
        honesty.exported_pages()
    assert not list((scratch / '.build').iterdir())


def test_the_manifest_tables_classify_the_real_manifest(honesty):
    assert not honesty._MANIFEST_COPY & honesty._MANIFEST_MACHINE
    real = json.loads((honesty.OSS / 'web/static/site.webmanifest').read_text(encoding='utf-8'))
    assert honesty._names(real) <= honesty._MANIFEST_COPY | honesty._MANIFEST_MACHINE
    text = json.dumps(real)
    honesty.export_inventory('static/site.webmanifest', text)
    assert set(honesty.export_copy('static/site.webmanifest', text).split('\n\n')) == {real['name'], real['short_name']}


def nav_labels(nav):
    for entry in nav:
        if isinstance(entry, dict):
            for label, target in entry.items():
                yield label
                if isinstance(target, list):
                    yield from nav_labels(target)


def test_the_docs_configuration_is_read_by_setting(honesty, scratch):
    assert not honesty.DOCS_CONFIG_SHOWN & honesty.DOCS_CONFIG_MACHINE
    path = honesty.OSS / 'site' / 'mkdocs.yml'
    assert path in honesty.MARKDOWN and honesty.DOCUMENT_KINDS[path.suffix] == 'docs configuration'
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    shown = honesty.docs_shown(path)
    for text in (config['site_name'], config['site_description'], config['copyright'], config['repo_name'],
                 *nav_labels(config['nav'])):
        assert text in shown, text
    assert not {config['site_url'], config['repo_url'], 'index.md', 'faq.md'} & set(shown)
    assert honesty.claims(honesty._file_copy(path)) == []
    planted = scratch / 'mkdocs.yml'
    for key in sorted(honesty.DOCS_CONFIG_SHOWN - {'nav'}):
        for value in (CLAIM, [CLAIM], {'toggle': {'name': CLAIM}}):
            planted.write_text(yaml.safe_dump({'site_name': 'G6', key: value}), encoding='utf-8')
            assert honesty.claims(honesty._file_copy(planted)), (key, value)
    for nav in ([{CLAIM: 'index.md'}], [{'Guide': [{CLAIM: 'guide/index.md'}]}], [{'Guide': [{'More': [{CLAIM: 'x.md'}]}]}]):
        planted.write_text(yaml.safe_dump({'site_name': 'G6', 'nav': nav}), encoding='utf-8')
        assert honesty.claims(honesty._file_copy(planted)), nav
    planted.write_text(yaml.safe_dump({'site_name': 'G6', 'nav': [{'Guide': 'verified.md'}, 'verify/index.md']}),
                       encoding='utf-8')
    assert honesty.claims(honesty._file_copy(planted)) == []                         # a target is a path
    for key in sorted(honesty.DOCS_CONFIG_MACHINE):
        planted.write_text(yaml.safe_dump({'site_name': 'G6', key: CLAIM}), encoding='utf-8')
        assert honesty.claims(honesty._file_copy(planted)) == [], key
    planted.write_text(yaml.safe_dump({'site_name': 'G6', 'tagline': CLAIM}), encoding='utf-8')
    with pytest.raises(AssertionError, match='tagline'):
        honesty._file_copy(planted)


def test_the_package_metadata_is_read_by_key(honesty, scratch):
    assert not honesty.PACKAGE_SHOWN & honesty.PACKAGE_MACHINE
    path = honesty.OSS / 'pyproject.toml'
    assert path in honesty.MARKDOWN and honesty.DOCUMENT_KINDS[path.suffix] == 'package metadata'
    project = tomllib.loads(path.read_text(encoding='utf-8'))['project']
    assert honesty.package_shown(path) == [project['description'], *project['keywords']]
    assert honesty.claims(honesty._file_copy(path)) == []
    planted = scratch / 'pyproject.toml'
    for body in (f'description = "{CLAIM}"', 'description = "Checked."\nkeywords = ["agents", "verification"]'):
        planted.write_text(f'[project]\nname = "g6"\n{body}\n', encoding='utf-8')
        assert honesty.claims(honesty._file_copy(planted)), body
    planted.write_text('[project]\nname = "verified"\nreadme = "VERIFIED.md"\ndescription = "Checked."\n',
                       encoding='utf-8')
    assert honesty.claims(honesty._file_copy(planted)) == []
    planted.write_text(f'[project]\nname = "g6"\nsummary = "{CLAIM}"\n', encoding='utf-8')
    with pytest.raises(AssertionError, match='summary'):
        honesty._file_copy(planted)
    # a document of no listed kind has no reader
    other = scratch / 'notes.rst'
    other.write_text(CLAIM, encoding='utf-8')
    with pytest.raises(AssertionError, match='No reader'):
        honesty._file_copy(other)
    assert {path.suffix.lower() for path in honesty.MARKDOWN} <= set(honesty.DOCUMENT_KINDS)


def test_the_scratch_folder_is_made_with_its_parents_inside_the_repository(honesty, scratch):
    assert scratch.is_dir() and scratch.parent == honesty.OSS / '.build' and not list(scratch.iterdir())


def test_no_site_test_names_the_reviewers_scratch_folder(honesty):
    # Finding 11's own test is not copied into this folder: it scans tests/site for that folder's name as a string,
    # and a copy here would find itself. This walks the same code, with the name put together so it is no such string.
    name = '.' + 'review'
    users = [path.relative_to(honesty.OSS).as_posix() for path in sorted((honesty.OSS / 'tests' / 'site').rglob('*.py'))
             if any(isinstance(node, ast.Constant) and node.value == name
                    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))))]
    assert not users, f'These tests name a folder a checkout does not have: {users}'
