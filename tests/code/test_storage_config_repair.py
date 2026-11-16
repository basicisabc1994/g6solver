# SPDX-License-Identifier: Apache-2.0
"""Both configuration loaders bound bytes and turn hostile JSON into G6 refusals."""
import io
from pathlib import Path

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused


@pytest.mark.parametrize("payload", [b'{"roots": []}' + b" " * (1024 * 1024),
                                   b'{"roots": ["\xff"]}',
                                   b'{"roots": ' + b"[" * 2000 + b"]" * 2000 + b"}",
                                   b'{"roots": ' + b"1" * 5000 + b"}"],
                         ids=["oversized", "not_utf8", "deep", "digits"])
def test_config_file_is_bounded_and_refuses_invalid_text(payload, monkeypatch):
    class ConfigFile:
        def exists(self):
            return True

        def open(self, mode):
            assert mode == "rb"
            return io.BytesIO(payload)

    monkeypatch.setattr(_roots, "state_path", lambda *_: ConfigFile())
    monkeypatch.setattr(_roots, "plain_file", lambda _: None)
    with pytest.raises(CodeRefused) as refused:
        _roots.load({})
    assert refused.value.code == _roots.CONFIG_INVALID


def test_config_file_exactly_one_mib_remains_readable(monkeypatch):
    payload = b'{"roots": []}'
    payload += b" " * (1024 * 1024 - len(payload))

    class ConfigFile:
        def exists(self):
            return True

        def open(self, mode):
            return io.BytesIO(payload)

    monkeypatch.setattr(_roots, "state_path", lambda *_: ConfigFile())
    monkeypatch.setattr(_roots, "plain_file", lambda _: None)
    assert _roots.load({}).roots == ()


def test_config_directory_is_refused(monkeypatch):
    monkeypatch.setattr(_roots, "state_path", lambda *_: Path(__file__).parent)
    with pytest.raises(CodeRefused) as refused:
        _roots.load({})
    assert refused.value.code == _roots.CONFIG_INVALID


@pytest.mark.parametrize("block", ["component_creator", "swe_diagnostics"])
@pytest.mark.parametrize("text", ['{"roots": ' + "1" * 5000 + "}", '{"roots": ' + "[" * 2000 + "]" * 2000 + "}"])
def test_config_resource_errors_reach_block_callers_as_refusals(block, text, monkeypatch):
    monkeypatch.setenv(_roots.CONFIG_ENV, text)
    if block == "component_creator":
        from g6.code.component_creator import ComponentCreatorBlock, ComponentCreatorInput
        out = ComponentCreatorBlock().infer(ComponentCreatorInput(op="spec_from_module", path="/missing.py")).unwrap()
    else:
        from g6.code.swe_diagnostics import SweDiagnosticsBlock, SweDiagnosticsInput
        out = SweDiagnosticsBlock().infer(SweDiagnosticsInput(op="repo_map", path="/missing")).unwrap()
    assert out["code"] == _roots.CONFIG_INVALID
