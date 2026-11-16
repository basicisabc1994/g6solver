# SPDX-License-Identifier: Apache-2.0
"""Config failures and path errors have bounded, path-free G6 refusals."""
import io
from pathlib import Path

import pytest

from g6.core.storage import PathRefused
from g6.sources.ctx_git import config


@pytest.mark.parametrize("error", [RecursionError, ValueError, TypeError, MemoryError])
def test_git_json_resource_errors_are_refusals(error, monkeypatch):
    def fail(_):
        raise error("private-system-details")

    monkeypatch.setattr(config.json, "loads", fail)
    with pytest.raises(config.GitRefused) as refused:
        config.parse("{}", config.CONFIG_ENV)
    assert refused.value.code == config.CONFIG_INVALID


def test_git_confined_context_does_not_claim_an_invalid_path_is_outside(monkeypatch):
    def fail(*_):
        raise PathRefused("private-system-details", outside=False)

    monkeypatch.setattr(config, "confined_path", fail)
    with pytest.raises(config.GitRefused) as refused:
        config._confined("unused", (), "a folder outside every declared root")
    assert refused.value.code == config.PATH_INVALID
    assert "outside" not in refused.value.reason
    assert "private-system-details" not in refused.value.reason


def test_git_inside_option_and_nonempty_destination_have_invalid_codes():
    repo = Path(__file__).resolve().parent
    for call in (lambda: config.inside(repo, "-x"),
                 lambda: config.destination(str(repo), config.GitConfig((repo.parent,), (), "test")),
                 lambda: config.destination(str(repo), config.GitConfig((repo,), (), "test"))):
        with pytest.raises(config.GitRefused) as refused:
            call()
        assert refused.value.code == config.PATH_INVALID
        assert str(repo.parent).lower() not in refused.value.reason.lower()


@pytest.mark.parametrize("payload", [b'{"roots": []}' + b" " * (1024 * 1024), b'{"roots": ["\xff"]}',
                                   b'{"roots": ' + b"[" * 2000 + b"]" * 2000 + b"}",
                                   b'{"roots": ' + b"1" * 5000 + b"}"],
                         ids=["oversized", "not_utf8", "deep", "digits"])
def test_git_config_file_refuses_oversized_or_unreadable_json(payload, monkeypatch):
    class ConfigFile:
        def exists(self):
            return True

        def open(self, mode):
            return io.BytesIO(payload)

    monkeypatch.setattr(config, "state_path", lambda *_: ConfigFile())
    monkeypatch.setattr(config, "plain_file", lambda _: None)
    with pytest.raises(config.GitRefused) as refused:
        config.load({})
    assert refused.value.code == config.CONFIG_INVALID


def test_git_config_exactly_one_mib_remains_readable(monkeypatch):
    payload = b'{"roots": []}'
    payload += b" " * (1024 * 1024 - len(payload))

    class ConfigFile:
        def exists(self):
            return True

        def open(self, mode):
            return io.BytesIO(payload)

    monkeypatch.setattr(config, "state_path", lambda *_: ConfigFile())
    monkeypatch.setattr(config, "plain_file", lambda _: None)
    assert config.load({}).roots == ()


def test_git_config_directory_is_refused(monkeypatch):
    monkeypatch.setattr(config, "state_path", lambda *_: Path(__file__).parent)
    with pytest.raises(config.GitRefused) as refused:
        config.load({})
    assert refused.value.code == config.CONFIG_INVALID
