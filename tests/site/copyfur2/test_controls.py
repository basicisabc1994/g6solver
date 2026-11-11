import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_readers_and_vocabulary_controls(honesty, monkeypatch):
    followup = load(Path(honesty.__file__).with_name('test_honesty_followup.py'), 'review_control_followup')
    followup.test_one_claim_decision_and_one_sentence_reader_from_the_code(honesty)
    followup.test_sentence_and_claim_call_paths_are_observed(honesty, monkeypatch)


def test_family_mentions_markup_entities_and_neutral_words(honesty):
    inventory = load(Path(honesty.__file__).parent / 'copyfur1/test_inventory.py', 'review_control_inventory')
    inventory.test_claim_and_mention_compile_the_same_vocabulary_from_code(honesty)
    for prefix in ('', *honesty.VERIFICATION_PREFIXES):
        for ending in honesty.VERIFICATION_ENDINGS:
            word = prefix + 'verif' + ending
            for form in (word, word.upper(), word.capitalize()):
                assert honesty.claims('G6 ' + form + ' every patch.')
                assert honesty.g6_claims('<p>G6 ' + form + ' every patch.</p>')
                assert not honesty.claims('Avoid the word "' + form + '".')
    for word in ('Verifone', 'Verifire', 'verifax', 'provenance', 'provides'):
        assert not honesty.claims('G6 reads ' + word + ' logs.')
    for copy in ('G6 veri**fies** patches.', 'G6 produces veri\u00ad\nfied answers.'):
        assert honesty.claims(honesty._markdown_copy(copy))
    plain = honesty._page_copy('<p>G6 verifies patches.</p>')
    entity = honesty._page_copy('<p>G6 v&#101;ri<em>fies</em> patches.</p>')
    assert entity == plain
    assert honesty.claims(entity) == honesty.claims(plain)


def test_foreign_format_and_shown_attribute_controls(honesty):
    inventory = load(Path(honesty.__file__).parent / 'copyfur1/test_inventory.py', 'review_format_inventory')
    for constant in ('NaN', 'Infinity', '-Infinity'):
        inventory.test_json_constants_are_strict(honesty, constant)
    for mime in ('application/ld+json', 'Application/LD+JSON', 'application/ld+json; charset=utf-8'):
        inventory.test_mime_parameter_forms(honesty, mime)
    inventory.test_each_standard_shown_attribute_and_meta_table_row_is_read(honesty)
    for data in ({'articleBody': 'G6 verifies patches.'}, {'image': {'caption': 'G6 verifies patches.'}}):
        page = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
        honesty.structured_inventory(page)
        assert honesty.claims(honesty._page_copy(page))
    with pytest.raises(AssertionError, match='newReaderFacingField'):
        honesty.structured_inventory('<script type="application/ld+json">{"newReaderFacingField":"Checked."}</script>')


def test_yaml_extension_inventory_is_complete(honesty):
    root = honesty.OSS / '.review' / ('yaml-' + uuid4().hex)
    root.mkdir()
    config = root / 'mkdocs.yml'
    config.write_text('markdown_extensions:\n  - toc: {permalink: true}\n\n  - footnotes\n', encoding='utf-8')
    assert honesty.docs_extensions(config) == {'toc', 'footnotes'}
    config.write_text('markdown_extensions: [toc,', encoding='utf-8')
    with pytest.raises(yaml.YAMLError):
        honesty.docs_extensions(config)
    honesty.test_the_docs_site_is_read_with_the_extensions_mkdocs_yml_names()


def test_status_and_copy_change_controls(honesty, monkeypatch):
    controls = load(Path(honesty.__file__).parent / 'copyfur1/test_controls.py', 'review_owned_controls')
    controls.test_render_statuses_are_not_treated_as_success(monkeypatch)
    controls.test_copy_names_exemptions_and_bound_input_membership(honesty)
    graph = json.loads((honesty.OSS / 'web/static/graph/knowledge-graph.json').read_text(encoding='utf-8'))
    assert graph


def test_transitive_local_and_canonical_document_links(honesty):
    root = honesty.OSS / '.review' / ('links-' + uuid4().hex)
    root.mkdir()
    first, second, third = (root / name for name in ('first.md', 'second.md', 'third.txt'))
    first.write_text('[Second](second.md#part)\n', encoding='utf-8')
    address = honesty.views.evidence.REPO + third.relative_to(honesty.OSS).as_posix()
    second.write_text('[Third](' + address + ')\n', encoding='utf-8')
    third.write_text('Checked.\n', encoding='utf-8')
    assert set(honesty.public_documents([first])) == {first, second, third}


def test_real_static_export_copy_structured_data_and_cleanup(honesty, monkeypatch):
    root = honesty.OSS / '.review' / ('export-' + uuid4().hex)
    monkeypatch.setattr(honesty, 'OSS', root)
    exported = honesty.exported_pages()
    expected = {page.path + 'index.html' for page in honesty.PAGES}
    assert expected <= exported.keys()
    assert '404.html' in exported
    essays = {page.path + 'index.html' for page in honesty.PAGES if page.namespace == 'blog'}
    for name, page in exported.items():
        honesty.structured_inventory(page)
        assert not (honesty.g6_claims(page) if name in essays else honesty.claims(honesty._page_copy(page))), name
    assert not list((root / '.build').iterdir()), 'The export must clean its own temporary directory.'
