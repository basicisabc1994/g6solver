"""Finding 12: the package's own metadata is public copy (the description and keywords every index and ``pip show``
print) and no reader of the check reads it. It holds the keyword "verification" today, the very keyword the check
refuses in a page's structured data."""
import tomllib


def test_the_package_metadata_passes_the_claim_check(honesty):
    project = tomllib.loads((honesty.OSS / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    # control: the check's own rule for a keyword, from its instrument test
    assert honesty.claims(honesty._page_copy('<script type="application/ld+json">{"keywords": "verification"}</script>'))
    assert honesty.claims(project['description']) == []                          # control: the description passes
    refused = [keyword for keyword in project.get('keywords', []) if honesty.claims(keyword)]
    assert not refused, f'pyproject.toml ships keywords the copy check refuses on a page, and nothing reads them: {refused}'
