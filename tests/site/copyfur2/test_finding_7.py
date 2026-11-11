import copy
from uuid import uuid4

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings


def test_public_documents_linked_by_a_template_are_inventoried(honesty):
    root = honesty.OSS / '.review' / ('template-link-' + uuid4().hex)
    root.mkdir()
    document = root / 'public.md'
    document.write_text('G6 verifies every patch.\n', encoding='utf-8')
    address = honesty.views.evidence.REPO + document.relative_to(honesty.OSS).as_posix()
    (root / '404.html').write_text(
        '{% extends "base.html" %}{% block content %}<a href="' + address + '">Read the guide</a>{% endblock %}',
        encoding='utf-8',
    )
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]['DIRS'] = [root, *templates[0]['DIRS']]
    with override_settings(TEMPLATES=templates):
        exported = render_to_string('404.html', request=RequestFactory().get('/404.html'))
        assert address in honesty._reader(exported).links
        assert honesty.claims(honesty._file_copy(document)), 'Control: linked copy is a claim.'
        assert document in honesty.public_documents(honesty.MARKDOWN), 'A template-linked public document was omitted.'
