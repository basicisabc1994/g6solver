from email.message import Message


def test_json_ld_mime_parameters_do_not_hide_copy(honesty):
    mime = 'application/ld+json; charset=utf-8'
    message = Message()
    message['content-type'] = mime
    assert message.get_content_type() == 'application/ld+json'
    body = '{"@context": "https://schema.org", "@type": "Article", "articleBody": "G6 verifies every answer."}'
    canonical = '<script type="application/ld+json">' + body + '</script>'
    assert honesty.claims(honesty._page_copy(canonical))
    page = '<script type="' + mime + '">' + body + '</script>'
    assert honesty.claims(honesty._page_copy(page)), 'JSON-LD with a MIME parameter was skipped.'

