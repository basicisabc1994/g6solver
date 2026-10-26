# SPDX-License-Identifier: Apache-2.0
"""Integration: a built packet's envelope fields and a loaded handle pass ``g6.invariants.check_envelope``, neither is
above qualified-draft, and a secret in the repository reaches neither."""
from __future__ import annotations

from pathlib import Path

from g6.context import Retriever, build_packet, load_handle
from g6.invariants import check_envelope

_SECRET = "sk-test1234567890abcdef"
_FIELDS = ("completion_state", "warning_card", "evidence", "request_id", "run_id")


def test_packet_and_handle_envelopes_pass_the_library_check(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(f"def invoice_total(x):\n    api_key = '{_SECRET}'\n    return x\n", encoding="utf-8")
    retriever = Retriever([src])
    packet = build_packet("fix invoice_total rounding", retriever, budget=4000, query="invoice_total",
                          run_id="run-1", request_id="req-1")
    envelope = {name: getattr(packet, name) for name in _FIELDS}
    assert check_envelope(envelope) == [], envelope
    assert packet.completion_state == "qualified-draft" and _SECRET not in packet.model_dump_json()

    loaded = load_handle(retriever, packet.repository_handles[0], "invoice_total", request_id="req-1", run_id="run-1")
    reply = loaded.to_dict()
    assert check_envelope(reply) == [], reply
    assert reply["completion_state"] == "qualified-draft" and _SECRET not in str(reply)
