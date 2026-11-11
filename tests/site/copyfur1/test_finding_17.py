from pathlib import Path
import pytest


@pytest.mark.parametrize('relative', ['docs/CONTINUATION_REVIEW.md', 'docs/releases/UAT_ROUND_1.md'])
def test_linked_public_document_is_in_the_copy_guard(honesty, monkeypatch, relative):
    target = honesty.OSS / relative
    assert target.is_file()
    continuation = (honesty.OSS / 'docs/AI_FOR_AI_CONTINUATION.md').read_text(encoding='utf-8')
    assert target.name in continuation
    read = Path.read_text

    def with_drift(path, *args, **kwargs):
        text = read(path, *args, **kwargs)
        return text + '\n\nG6 verifies every patch.\n' if path == target else text

    monkeypatch.setattr(Path, 'read_text', with_drift)
    assert honesty.claims(honesty._file_copy(target)), 'Control: the injected public claim must be readable.'
    refused = [path for path in honesty.MARKDOWN if honesty.claims(honesty._file_copy(path))]
    assert target in refused, f'Linked public document was never scanned: {relative}'
