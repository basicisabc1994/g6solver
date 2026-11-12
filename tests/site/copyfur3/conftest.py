"""Review 3 of the copy check's follow-up (revcopy3). The check itself is the shared ``honesty`` fixture of
tests/site/conftest.py; ``site`` is the reviewer's own fixture, unchanged."""
import pytest


@pytest.fixture(scope='session')
def site():
    """The real site's pages, through its own views: path -> HTML."""
    from django.test import Client

    client, pages = Client(), {}

    def get(path):
        if path not in pages:
            reply = client.get(path)
            assert reply.status_code == 200, (path, reply.status_code)
            pages[path] = reply.content.decode()
        return pages[path]

    return get
