import importlib.util
import json
from pathlib import Path
import pytest
import yaml


def followup_module(honesty):
    spec = importlib.util.spec_from_file_location('review_controls_followup', Path(honesty.__file__).with_name('test_honesty_followup.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matcher_sentence_reader_and_foreign_reader_controls(honesty):
    followup = followup_module(honesty)
    for word in ('reverifies', 'unverifiably', 're-verified', 'VERIFIERS', 'ReVeRiFyInG', 'verifiability'):
        followup.test_claim_stems_in_all_copy_readers(honesty, word)
    for middle in ('e.g. a runner', 'i.e. a runner', 'tests etc. and a runner', 'vs. another tool', 'A. Smith',
                   'J. R. Smith', 'U.S. tools', 'version 1.2 of a runner'):
        followup.test_both_checkers_share_abbreviation_boundaries(honesty, middle)
    followup.test_sentence_reader_keeps_real_boundaries(honesty)
    followup.test_one_claim_decision_and_one_sentence_reader_from_the_code(honesty)
    followup.test_foreign_readers_are_inventoried_from_the_code(honesty)
    followup.test_regex_calls_have_named_roles_from_the_code(honesty)
    followup.test_claim_vocabulary_does_not_turn_other_word_stems_into_claims(honesty)
    followup.test_machine_fields_do_not_hide_nested_copy(honesty)
    followup.test_unknown_nested_structured_field_fails_the_inventory(honesty)
    followup.test_unreadable_json_ld_fails_the_inventory(honesty)
    followup.test_structured_arrays_read_copy_and_skip_named_machine_strings(honesty)
    for field in ('articleBody', 'keywords', 'jobTitle', 'newReaderFacingField'):
        followup.test_all_structured_strings_are_read_before_their_field_is_registered(honesty, field)


def test_yaml_controls_without_pytest_temporary_directory_acl(honesty, monkeypatch, scratch):
    root = scratch
    path = root / 'mkdocs.yml'
    for source in ('markdown_extensions: [toc, tables]', 'markdown_extensions:\n  - toc: {permalink: true}\n\n  - tables\n'):
        path.write_text(source, encoding='utf-8')
        assert honesty.docs_extensions(path) == {'toc', 'tables'}
    for source in ('markdown_extensions: [toc,', 'markdown_extensions: null', 'markdown_extensions: [42]',
                   'markdown_extensions: [{toc: {}, footnotes: {}}]'):
        path.write_text(source, encoding='utf-8')
        with pytest.raises((AssertionError, yaml.YAMLError)):
            honesty.docs_extensions(path)
    with pytest.raises(FileNotFoundError):
        honesty.docs_extensions(root / 'missing.yml')
    config = (honesty.OSS / 'site/mkdocs.yml').read_text(encoding='utf-8')
    config = config.replace('\nextra_css:', '\n  - footnotes\n\nextra_css:', 1)
    assert 'footnotes' in yaml.safe_load(config)['markdown_extensions']
    (root / 'site').mkdir()
    (root / 'site/mkdocs.yml').write_text(config, encoding='utf-8')
    monkeypatch.setattr(honesty, 'OSS', root)
    with pytest.raises(AssertionError, match='footnotes'):
        honesty.test_the_docs_site_is_read_with_the_extensions_mkdocs_yml_names()


def test_build_and_structured_output(honesty, scratch):
    from pages.management.commands.build_static import build, csp_hash
    from pages.guard import inline_scripts
    root = scratch
    out = root / 'out'
    assert build(out) == []
    for page in honesty.PAGES:
        target = out / page.path / 'index.html'
        assert target.is_file(), target
        honesty.structured_inventory(target.read_text(encoding='utf-8'))
    assert (out / '404.html').is_file()
    headers = (out / '_headers').read_text()
    for target in out.rglob('*.html'):
        for script in inline_scripts(target.read_text(encoding='utf-8')):
            assert csp_hash(script) in headers
    essay = (out / 'essays/move-fast-break-things/index.html').read_text(encoding='utf-8')
    body = essay.split('<script type="application/ld+json">', 1)[1].split('</script>', 1)[0]
    assert json.loads(body)['headline'] == 'Why "Move Fast and Break Things" Doesn\'t Work for AI'

def test_render_statuses_are_not_treated_as_success(monkeypatch):
    from django.http import HttpResponse
    from pages.management.commands import build_static
    from pages.routes import Page
    page = Page('review', 'pages', 'review/', 'template', 'unused.html')
    monkeypatch.setattr(build_static.routes, 'PAGES', (page,))
    for status in (200, 301, 400, 404, 500, 599):
        monkeypatch.setattr(build_static, 'view_for', lambda page, status=status: lambda request: HttpResponse('Checked.', status=status))
        pages, problems = build_static._rendered()
        if status == 200:
            assert pages == {'/review/': 'Checked.'} and problems == []
        else:
            assert pages == {} and problems == [f'/review/: HTTP {status}']

def test_copy_names_exemptions_and_bound_input_membership(honesty):
    import ast
    import subprocess
    from g6.core import component_certificate as certificates, vetting
    from g6.coding_agent_gate.detectors import acknowledges_unverified
    old_source = subprocess.run(['git', 'show', 'd45e79f:tests/site/test_honesty.py'], cwd=honesty.OSS,
                                capture_output=True, text=True, encoding='utf-8', check=True).stdout
    old_left = next(node.value for node in ast.parse(old_source).body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'LEFT' for target in node.targets))
    assert ast.literal_eval(old_left) == honesty.LEFT
    assert acknowledges_unverified('The result is not independently checked beyond the run below.')
    changed = set(subprocess.run(['git', 'diff', '--name-only', 'd45e79f', 'HEAD'], cwd=honesty.OSS,
                                 capture_output=True, text=True, encoding='utf-8', check=True).stdout.splitlines())
    root = honesty.OSS / 'src'
    records = sorted(root.glob('g6/*/*/certificate/certificate.json'))
    assert len(records) == 5
    for record in records:
        folder = record.parent.parent
        package = '.'.join(folder.relative_to(root).parts)
        declaration = vetting.declared(package)
        inputs = certificates.input_files(root, folder, declaration)
        assert not changed.intersection('src/' + name for name in inputs), package
