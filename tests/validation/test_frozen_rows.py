# SPDX-License-Identifier: Apache-2.0
"""A measurement never reads passed without its evidence, held for every row by an inventory read from the folders:
each protocol's thresholds are frozen data whose digest its harness pins, each label file a protocol names is pinned
by digest, and no harness admits on labels that are not independent, on a missing value or on one that is not a
finite number."""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESSES = ROOT / "validation" / "context_engine"
PROTOCOLS = ROOT / "docs" / "measurements" / "context-engine"
sys.path.insert(0, str(HARNESSES))

import frozen  # noqa: E402

_PIN = re.compile(r'^THRESHOLDS_SHA256 = "([0-9a-f]{64})"', re.MULTILINE)
_PROTOCOL = re.compile(r'"(\d\d-[a-z0-9-]+\.protocol\.md)"')
_LABELS = re.compile(r"tests/knowledge/labels/[\w.-]+\.json")
#: Harness files that read a protocol and pin no digest themselves, each with the file that compares it. Closed.
PINNED_ELSEWHERE = {"colbert_spike/run.py": "records the digest of the thresholds it ran under in each result; "
                                           "colbert_spike/report.py compares it with its pin and decides nothing "
                                           "from a run that differs"}


def harnesses() -> dict[str, tuple[str, str | None]]:
    """Every harness file that names a protocol: its protocol file and the digest it pins."""
    found = {}
    for path in sorted(HARNESSES.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        named = _PROTOCOL.search(text)
        if named and "PROTOCOL" in text:
            pin = _PIN.search(text)
            found[path.relative_to(HARNESSES).as_posix()] = (named.group(1), pin.group(1) if pin else None)
    return found


def test_every_harness_pins_the_digest_of_its_rows_frozen_thresholds():
    found = harnesses()
    assert {"retrieval_baseline.py", "planner_pairs.py", "colbert_spike/report.py", "colbert_spike/run.py"} <= set(found)
    assert {name for name, (_, pin) in found.items() if pin is None} == set(PINNED_ELSEWHERE)
    for name, (protocol, pin) in found.items():
        block = frozen.block(PROTOCOLS / protocol)
        assert isinstance(block.get("thresholds"), dict) and block["thresholds"], protocol
        if pin is not None:
            assert frozen.digest(block["thresholds"]) == pin, f"{name} pins another set of thresholds than {protocol} holds"
            assert frozen.problem(PROTOCOLS / protocol, pin) == ""
            assert "not the frozen ones" in frozen.problem(PROTOCOLS / protocol, "0" * 64)


def test_every_label_file_a_protocol_names_is_pinned_by_its_digest():
    seen = []
    for protocol in sorted(PROTOCOLS.glob("*.protocol.md")):
        block = frozen.block(protocol)
        named = set(_LABELS.findall(protocol.read_text(encoding="utf-8")))
        for label_file in named:
            seen.append((protocol.name, label_file))
            pinned = block.get("labels") or {}
            assert pinned.get("file") == label_file, f"{protocol.name} names {label_file} and does not pin it"
            assert pinned.get("sha256") == hashlib.sha256((ROOT / label_file).read_bytes()).hexdigest(), protocol.name
            assert pinned.get("written"), "the protocol says who wrote the labels"
    assert {name for name, _ in seen} >= {"03-retrieval.protocol.md", "07-planner.protocol.md"}


def test_an_unknown_is_never_a_pass():
    assert frozen.independent("written independently of the generator by a second person")
    for said in ("", None, "the corpus generator's own ground truth; not independently written labels", "generated", 7):
        assert not frozen.independent(said), said
    for value in (0, 1.5, -2):
        assert frozen.finite(value)
    for value in (float("nan"), float("inf"), float("-inf"), None, True, "0.9", [1]):
        assert not frozen.finite(value), repr(value)
