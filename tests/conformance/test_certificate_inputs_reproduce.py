# SPDX-License-Identifier: Apache-2.0
"""Each certificate's falsifier makes the inputs its certificate records, on whatever system the tests run on.

A certificate records the inputs its falsifier generated, and the kit's C1 check reissues it and compares. A generator
whose output depends on the system makes a certificate that verifies only where it was issued: Python's
``random.gauss`` calls the platform's ``log`` and ``cos``, whose last bit differs between Windows and Linux (2026-10-26,
the Linux stand-in: ``adapt_bayesian``'s ``convergence`` chains). This regenerates every recorded rule's inputs the way
issuance does, without running the component, so each system the test job runs on is checked."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from g6.workforce import component_certificate as certificates

SRC = Path(certificates.__file__).resolve().parents[2]
RECORDS = sorted(SRC.glob("g6/*/*/certificate/certificate.json"))


def test_the_certificates_are_found() -> None:
    assert len(RECORDS) >= 5, RECORDS


@pytest.mark.parametrize("record", RECORDS, ids=lambda path: path.parent.parent.name)
def test_the_falsifier_makes_the_recorded_inputs_on_this_system(record: Path) -> None:
    body = json.loads(record.read_text(encoding="utf-8"))
    bundle = certificates.load("g6." + ".".join(record.parent.parent.relative_to(SRC / "g6").parts))
    differ = [rule for rule, entry in sorted(body["falsification"].items())
              if certificates._generated(bundle, rule) != entry["inputs"]]
    assert differ == [], f"the falsifier makes other inputs here than {record.parent.parent.name}'s record holds"
